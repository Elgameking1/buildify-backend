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


class PaystackError(ConflictError):
    """Paystack refused the request, or could not be reached.

    A ConflictError so it surfaces as a 4xx the client can act on ("payment
    could not be started, try again") rather than a 500 that reads like a bug
    in this application.
    """

    def __init__(self, message: str) -> None:
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
    return bool(settings.paystack_secret_key)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await _request("POST", path, json=payload)


async def _get(path: str) -> dict[str, Any]:
    return await _request("GET", path)


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    if not is_configured():
        raise PaystackError("Online payment is not configured on this server.")

    url = f"{settings.paystack_base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(method, url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        # Network-level failure. The caller has not been charged, so inviting a
        # retry is safe.
        logger.warning("Paystack %s %s unreachable: %s", method, path, exc)
        raise PaystackError("Could not reach the payment provider. Please try again.") from exc

    try:
        body = response.json()
    except ValueError:
        logger.error("Paystack %s %s returned non-JSON (%s)", method, path, response.status_code)
        raise PaystackError("The payment provider returned an unreadable response.") from None

    if response.status_code >= 400 or not body.get("status"):
        # Paystack puts the human-readable cause in `message` and uses a
        # boolean `status` field independent of the HTTP code.
        message = body.get("message") or "The payment provider rejected the request."
        logger.warning("Paystack %s %s failed: %s", method, path, message)
        raise PaystackError(message)

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

    expected = hmac.new(
        settings.paystack_secret_key.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
