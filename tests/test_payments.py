"""Paystack payment flow.

The tests that matter here are the ones guarding money and trust boundaries:
the subunit conversion, the amount/currency check on a "successful"
transaction, the webhook signature, and idempotency. Paystack itself is stubbed
- these assert what this application does with the gateway's answers, not that
the gateway works.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.enums import PaymentStatus
from app.modules.payments import paystack
from app.modules.payments import service as payments_service
from app.modules.payments.models import Payment
from app.modules.payments.schemas import PaymentInitCreate
from app.modules.users.models import User
from tests.conftest import API, auth, place_order, register, seed_marketplace

SECRET = "sk_test_pretend_secret_key_for_tests"


@pytest.fixture(autouse=True)
def paystack_keys(monkeypatch):
    """Every test here runs as if Paystack were configured."""
    monkeypatch.setattr(settings, "paystack_secret_key", SECRET, raising=False)
    monkeypatch.setattr(settings, "payment_currency", "GHS", raising=False)


def _verified(amount_subunit: int, *, status: str = "success", currency: str = "GHS") -> dict:
    """A transaction as Paystack's verify endpoint returns it."""
    return {
        "status": status,
        "amount": amount_subunit,
        "currency": currency,
        "channel": "mobile_money",
        "gateway_response": "Approved",
        "reference": "irrelevant-here",
    }


# --- Money conversion ------------------------------------------------------


def test_cedis_convert_to_pesewas_exactly():
    """A rounding slip here is a charge 100x wrong that both sides accept."""
    assert paystack.to_subunit(Decimal("85.00")) == 8500
    assert paystack.to_subunit(Decimal("0.01")) == 1
    assert paystack.to_subunit(Decimal("1234.56")) == 123456
    # Binary float would give 1004.9999... and truncate to 100499.
    assert paystack.to_subunit(Decimal("1005.00")) == 100500


def test_subunit_round_trips():
    for value in ("85.00", "0.01", "1234.56"):
        amount = Decimal(value)
        assert paystack.from_subunit(paystack.to_subunit(amount)) == amount


# --- Webhook signature -----------------------------------------------------


def test_valid_signature_is_accepted():
    body = b'{"event":"charge.success"}'
    digest = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert paystack.signature_is_valid(body, digest) is True


def test_tampered_body_fails_the_signature():
    """The digest covers the bytes, so editing the amount invalidates it."""
    body = b'{"event":"charge.success","amount":100}'
    digest = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    tampered = b'{"event":"charge.success","amount":999999}'
    assert paystack.signature_is_valid(tampered, digest) is False


def test_missing_signature_is_rejected():
    assert paystack.signature_is_valid(b"{}", None) is False
    assert paystack.signature_is_valid(b"{}", "") is False


async def test_webhook_without_a_valid_signature_is_401(client, session_factory):
    """The signature is the only access control this endpoint has."""
    response = await client.post(
        f"{API}/payments/webhook",
        content=json.dumps({"event": "charge.success", "data": {"reference": "x"}}),
        headers={"x-paystack-signature": "not-the-real-digest"},
    )
    assert response.status_code == 401


# --- Settling --------------------------------------------------------------


async def _order_and_payment(client, session_factory, monkeypatch, quantity=2):
    """Place an order and start a payment against it, with Paystack stubbed."""
    world = await seed_marketplace(client, session_factory)
    token = world["buyer"]["access_token"]
    order = (await place_order(client, token, world["product"]["id"], quantity)).json()

    async def fake_initialize(**kwargs):
        return {"authorization_url": "https://checkout.paystack.com/abc123"}

    monkeypatch.setattr(paystack, "initialize_transaction", fake_initialize)

    async with session_factory() as session:
        buyer = await session.get(User, world["buyer"]["user"]["id"])
        payment = await payments_service.start(
            session, buyer, order["id"], PaymentInitCreate()
        )
        await session.commit()
        reference = payment.reference

    return world, order, reference


async def test_successful_payment_is_recorded(client, session_factory, monkeypatch):
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def fake_verify(ref):
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()

    assert payment.status == PaymentStatus.SUCCESS
    assert payment.paid_at is not None
    assert payment.channel == "mobile_money"


async def test_underpayment_is_not_accepted_as_success(
    client, session_factory, monkeypatch
):
    """Paystack says "success"; it is success for the wrong amount.

    Without this check, initialising a real GHS 1 transaction against someone
    else's reference would settle their order in full.
    """
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)

    async def fake_verify(ref):
        return _verified(100)  # GHS 1.00, against an order worth far more

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()

    assert payment.status == PaymentStatus.FAILED
    assert "mismatch" in (payment.failure_reason or "").lower()


async def test_wrong_currency_is_not_accepted(client, session_factory, monkeypatch):
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def fake_verify(ref):
        # Same number, different (much more valuable) currency.
        return _verified(expected, currency="USD")

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()

    assert payment.status == PaymentStatus.FAILED


async def test_settling_twice_does_not_duplicate_notifications(
    client, session_factory, monkeypatch
):
    """The browser and the webhook both settle; both arriving is normal."""
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    calls = {"n": 0}

    async def fake_verify(ref):
        calls["n"] += 1
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        await payments_service.settle(session, reference)
        await session.commit()
    async with session_factory() as session:
        await payments_service.settle(session, reference)
        await session.commit()

    # Second call short-circuits before touching the gateway.
    assert calls["n"] == 1

    unread = await client.get(
        f"{API}/notifications?page=1&size=50",
        headers=auth(world["buyer"]["access_token"]),
    )
    paid = [
        n for n in unread.json()["items"] if n["type"] == "PAYMENT_RECEIVED"
    ]
    assert len(paid) == 1


async def test_abandoned_transaction_is_marked_abandoned(
    client, session_factory, monkeypatch
):
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)

    async def fake_verify(ref):
        return _verified(0, status="abandoned")

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()

    assert payment.status == PaymentStatus.ABANDONED


# --- Ownership -------------------------------------------------------------


async def test_cannot_start_a_payment_for_someone_elses_order(
    client, session_factory, monkeypatch
):
    world, order, _ = await _order_and_payment(client, session_factory, monkeypatch)

    intruder = await register(client, "CLIENT", "intruder@example.com")

    response = await client.post(
        f"{API}/payments/orders/{order['id']}/initialize",
        json={},
        headers=auth(intruder["access_token"]),
    )
    # 404, not 403: a 403 would confirm the order id exists.
    assert response.status_code == 404


async def test_paid_order_reports_its_payment_status(
    client, session_factory, monkeypatch
):
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def fake_verify(ref):
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    async with session_factory() as session:
        await payments_service.settle(session, reference)
        await session.commit()

    body = (
        await client.get(
            f"{API}/orders/{order['id']}", headers=auth(world["buyer"]["access_token"])
        )
    ).json()

    assert body["payment_status"] == "SUCCESS"
    assert body["payment_reference"] == reference
    # Fulfilment is the vendors' business and must not be moved by payment.
    assert body["status"] == "PENDING"


# --- Security regressions --------------------------------------------------


async def test_a_paid_order_cannot_be_cancelled(client, session_factory, monkeypatch):
    """Cancelling restocks the goods; doing that after payment loses the money.

    The buyer would get their stock returned to the vendor and keep the funds
    they already transferred, with nothing in the system recording a refund.
    """
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def fake_verify(ref):
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)
    async with session_factory() as session:
        await payments_service.settle(session, reference)
        await session.commit()

    response = await client.post(
        f"{API}/orders/{order['id']}/cancel",
        headers=auth(world["buyer"]["access_token"]),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "paid_order"


async def test_verify_does_not_settle_someone_elses_payment(
    client, session_factory, monkeypatch
):
    """Ownership is checked before the gateway is touched.

    Checking afterwards still returns 404, but the payment would already have
    been settled and its owner notified on a guessed reference.
    """
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    intruder = await register(client, "CLIENT", "nosy@example.com")

    calls = {"n": 0}

    async def fake_verify(ref):
        calls["n"] += 1
        return _verified(paystack.to_subunit(Decimal(order["subtotal"])))

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)

    response = await client.get(
        f"{API}/payments/verify/{reference}", headers=auth(intruder["access_token"])
    )

    assert response.status_code == 404
    assert calls["n"] == 0, "Paystack must not be called for a foreign reference"

    # Read the row rather than settling it - the point is that the rejected
    # request left the payment untouched.
    async with session_factory() as session:
        found = await session.execute(
            select(Payment).where(Payment.reference == reference)
        )
        assert found.scalar_one().status == PaymentStatus.PENDING


async def test_a_pending_gateway_state_stays_settleable(
    client, session_factory, monkeypatch
):
    """Mobile money is asynchronous.

    The payer can be back on the site while the approval prompt is still on
    their phone. Recording that as failed would make the later charge.success
    webhook a no-op and strand a real payment.
    """
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def still_pending(ref):
        return _verified(0, status="pending")

    monkeypatch.setattr(paystack, "verify_transaction", still_pending)
    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()
    assert payment.status == PaymentStatus.PENDING

    async def now_paid(ref):
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", now_paid)
    async with session_factory() as session:
        payment = await payments_service.settle(session, reference)
        await session.commit()
    assert payment.status == PaymentStatus.SUCCESS


async def test_unknown_webhook_reference_is_not_retried(client, session_factory):
    """A reference we never issued is answered 200, not 500.

    A 500 would have Paystack redelivering it for hours over something no
    retry can fix.
    """
    body = json.dumps({"event": "charge.success", "data": {"reference": "never-issued"}})
    digest = hmac.new(SECRET.encode(), body.encode(), hashlib.sha512).hexdigest()

    response = await client.post(
        f"{API}/payments/webhook",
        content=body,
        headers={"x-paystack-signature": digest, "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# --- Pay before the order becomes real to the vendor -----------------------


async def test_vendor_neither_sees_nor_hears_an_unpaid_order(
    client, session_factory, monkeypatch
):
    """An unpaid order is an intention, not a commitment.

    The buyer can abandon Paystack's page and never return. Announcing the
    order to vendors at checkout would fill their queue with work that may
    never be paid for - and they could confirm and reserve stock against it.
    """
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    vendor_token = world["vendor"]["access_token"]

    queue = await client.get(f"{API}/vendor/orders", headers=auth(vendor_token))
    assert queue.json()["total"] == 0, "unpaid order must not reach the vendor queue"

    notes = await client.get(
        f"{API}/notifications?page=1&size=50", headers=auth(vendor_token)
    )
    assert notes.json()["total"] == 0, "vendor must not be notified before payment"


async def test_paying_releases_the_order_to_the_vendor(
    client, session_factory, monkeypatch
):
    world, order, reference = await _order_and_payment(client, session_factory, monkeypatch)
    vendor_token = world["vendor"]["access_token"]
    expected = paystack.to_subunit(Decimal(order["subtotal"]))

    async def fake_verify(ref):
        return _verified(expected)

    monkeypatch.setattr(paystack, "verify_transaction", fake_verify)
    async with session_factory() as session:
        await payments_service.settle(session, reference)
        await session.commit()

    queue = await client.get(f"{API}/vendor/orders", headers=auth(vendor_token))
    assert queue.json()["total"] == 1

    notes = await client.get(
        f"{API}/notifications?page=1&size=50", headers=auth(vendor_token)
    )
    kinds = [n["type"] for n in notes.json()["items"]]
    assert "PAYMENT_RECEIVED" in kinds


# --- Operator messages must not reach the customer -------------------------


async def test_gateway_messages_are_never_shown_to_the_customer(
    client, session_factory, monkeypatch
):
    """Paystack's text describes our integration, not the payer's problem.

    "Invalid key" tells someone trying to buy cement nothing they can act on,
    and tells anyone probing the checkout how the gateway is misconfigured.
    """
    world, order, _ = await _order_and_payment(client, session_factory, monkeypatch)

    async def rejected(**kwargs):
        raise paystack.PaystackError()

    monkeypatch.setattr(paystack, "initialize_transaction", rejected)

    response = await client.post(
        f"{API}/payments/orders/{order['id']}/initialize",
        json={},
        headers=auth(world["buyer"]["access_token"]),
    )

    body = response.json()
    assert response.status_code == 409
    assert body["detail"] == paystack.GENERIC_FAILURE
    for leak in ("key", "sk_", "pk_", "paystack", "merchant", "api"):
        assert leak not in body["detail"].lower(), f"{leak!r} leaked to the customer"


def test_a_public_key_does_not_enable_payments(monkeypatch):
    """The commonest misconfiguration, caught before checkout rather than at it."""
    monkeypatch.setattr(settings, "paystack_secret_key", "pk_test_abc123", raising=False)
    assert settings.payments_enabled is False

    monkeypatch.setattr(settings, "paystack_secret_key", "sk_test_abc123", raising=False)
    assert settings.payments_enabled is True


def test_whitespace_around_the_key_is_tolerated(monkeypatch):
    """Pasting into a hosting dashboard routinely appends a newline.

    Left in, it travels into the Authorization header and every call fails
    with a 401 that reads as "Invalid key" - an invisible cause.
    """
    monkeypatch.setattr(
        settings, "paystack_secret_key", "  sk_test_abc123\n", raising=False
    )
    assert settings.payments_enabled is True
    assert settings.paystack_secret_key_clean == "sk_test_abc123"
