"""Thin client over the Paystack REST API.

Only the two calls this app needs: initialise a transaction, and verify one.
Everything Paystack-specific - the subunit conversion, the auth header, the
webhook signature scheme - is contained here so the service layer deals in
cedis and domain objects.

No SDK: the surface used is two endpoints and one HMAC, and a dependency that
wraps that much would be more code to audit than the code it replaces.
"""

import hashlib
import hmac
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ConflictError

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# What a customer is told when the gateway fails for any reason that is ours
# to fix. Deliberately says nothing about keys, configuration or the provider.
GENERIC_FAILURE = "We could not start your payment just now. Please try again shortly."

# Paystack rejects a bad or wrong-type key with 401. Worth its own log line,
# because it is nearly always the public key pasted into the secret setting.
_AUTH_STATUSES = {401, 403}


class PaystackError(ConflictError):
    """Paystack refused the request, or could not be reached.

    A ConflictError so it surfaces as a 4xx the customer can act on rather
    than a 500 that reads like a bug in this application.

    The message given to the customer is always one of ours. Paystack's own
    text describes *our* integration, not their problem - "Invalid key",
    "Merchant is not enabled for live mode", "Invalid amount sent" - and
    forwarding it puts an operations message in front of someone who is only
    trying to pay. Worse, it tells anyone probing the checkout exactly how the
    gateway is misconfigured. The real cause is logged instead.
    """

    def __init__(self, message: str = GENERIC_FAILURE) -> None:
        super().__init__(message, code="payment_gateway_error")


def to_subunit(amount: Decimal) -> int:
    """Cedis -> pesewas.

    Paystack charges in the minor unit, so GHS 85.00 must be sent as 8500.
    Getting this wrong is not a validation error at either end - it is a
    charge 100x too small or too large that both sides accept - so the
    rounding is explicit rather than left to float conversion.
    """
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_subunit(amount: int) -> Decimal:
    return (Decimal(amount) / 100).quantize(Decimal("0.01"))


def is_configured() -> bool:
    return settings.payments_enabled


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key_clean}",
        "Content-Type": "application/json",
    }


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await _request("POST", path, json=payload)


async def _get(path: str) -> dict[str, Any]:
    return await _request("GET", path)


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    if not is_configured():
        # An operator problem, so it is logged as one. The customer is told
        # only that they cannot pay right now - "not configured on this
        # server" is a message for whoever runs the deployment.
        logger.error("Payment attempted with no PAYSTACK_SECRET_KEY configured")
        raise PaystackError("Online payment is unavailable at the moment.")

    url = f"{settings.paystack_base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(method, url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        # Network-level failure. The caller has not been charged, so inviting a
        # retry is safe.
        logger.warning("Paystack %s %s unreachable: %s", method, path, exc)
        raise PaystackError() from exc

    try:
        body = response.json()
    except ValueError:
        logger.error("Paystack %s %s returned non-JSON (%s)", method, path, response.status_code)
        raise PaystackError() from None

    if response.status_code >= 400 or not body.get("status"):
        # Paystack puts the cause in `message` and uses a boolean `status`
        # field independent of the HTTP code. That message is written for
        # whoever integrated the gateway, not for the person paying, so it is
        # logged and never returned - see PaystackError.
        message = body.get("message") or "no message"

        if response.status_code in _AUTH_STATUSES:
            logger.error(
                "Paystack rejected our credentials on %s %s (%s): %s. "
                "Check PAYSTACK_SECRET_KEY - it must be the SECRET key (sk_...), "
                "not the public key, and must match the mode you are testing in.",
                method,
                path,
                response.status_code,
                message,
            )
        else:
            logger.warning(
                "Paystack %s %s failed (%s): %s", method, path, response.status_code, message
            )

        raise PaystackError()

    return body.get("data") or {}


async def initialize_transaction(
    *,
    email: str,
    amount: Decimal,
    reference: str,
    callback_url: str,
    channels: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Create a transaction and get the hosted checkout URL.

    Returns Paystack's `data` object, whose `authorization_url` is where the
    browser is sent.  A hosted redirect rather than the inline popup on
    purpose: the popup needs js.paystack.co, and this app ships
    `script-src 'self'`, which would block it in production only.
    """
    return await _post(
        "/transaction/initialize",
        {
            "email": email,
            "amount": to_subunit(amount),
            "currency": settings.payment_currency,
            "reference": reference,
            "callback_url": callback_url,
            "channels": channels,
            "metadata": metadata,
        },
    )


async def verify_transaction(reference: str) -> dict[str, Any]:
    """Ask Paystack what actually happened to a transaction.

    This is the only source of truth about a payment. Neither the browser
    returning to the callback URL nor the webhook body is trusted on its own -
    both are attacker-controllable inputs that merely say *which* reference to
    go and check.
    """
    return await _get(f"/transaction/verify/{reference}")


def signature_is_valid(raw_body: bytes, signature: str | None) -> bool:
    """Verify the `x-paystack-signature` header on a webhook.

    HMAC-SHA512 of the exact bytes received, keyed with the secret key. The
    raw body matters: re-serialising the parsed JSON changes whitespace and
    key order, and the digest no longer matches.

    Compared with `compare_digest` so that a wrong signature fails in constant
    time and cannot be recovered a byte at a time.
    """
    if not signature or not is_configured():
        return False

    # The cleaned key, matching _headers(): a stray newline picked up when the
    # value was pasted would otherwise key this HMAC differently from the one
    # Paystack computed, and every webhook would be rejected as forged.
    expected = hmac.new(
        settings.paystack_secret_key_clean.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
