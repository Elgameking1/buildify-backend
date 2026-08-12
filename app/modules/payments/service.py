"""Payment orchestration.

The shape of the flow, and why:

  1. `start` creates a PENDING row and asks Paystack for a checkout URL.
  2. The browser leaves for Paystack and comes back to the callback page.
  3. `settle` re-asks Paystack what happened and writes the outcome.

Step 3 runs from two places - the returning browser and Paystack's webhook -
and is idempotent so that both arriving is normal rather than a bug. Both are
needed: the webhook is authoritative because it still fires when the customer
closes the tab mid-payment, and the browser path exists because a webhook can
take seconds to arrive and the customer is waiting.

Neither caller is trusted to say *what happened*, only *which reference to look
at*. The outcome always comes from `paystack.verify_transaction`.
"""

import logging
import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import NotificationType, OrderStatus, PaymentStatus
from app.core.errors import ConflictError, NotFoundError
from app.db.base import utc_now
from app.modules.notifications import service as notifications
from app.modules.orders.models import Order
from app.modules.payments import paystack
from app.modules.payments.models import Payment
from app.modules.payments.schemas import PaymentInitCreate
from app.modules.users.models import User

logger = logging.getLogger(__name__)

# Paystack's own success state.
PAYSTACK_SUCCESS = "success"

# States that mean "not finished yet" rather than "did not happen". Mobile
# money sits here while the approval prompt is with the payer, sometimes for
# minutes; the webhook settles it when they act.
RETRYABLE_GATEWAY_STATES = {"pending", "ongoing", "processing", "queued"}


def _new_reference(order_id: int) -> str:
    """Unique, and readable enough to find in Paystack's dashboard.

    The random half matters: a retry after an abandoned attempt must not reuse
    a reference Paystack has already seen, or initialisation is rejected.
    """
    return f"BLD-{order_id}-{secrets.token_hex(8)}"


async def latest_for_order(db: AsyncSession, order_id: int) -> Payment | None:
    """The payment that decides whether an order counts as paid.

    A successful attempt always wins, however many abandoned ones precede it;
    otherwise the most recent attempt is the interesting one.
    """
    paid = await db.execute(
        select(Payment)
        .where(Payment.order_id == order_id, Payment.status == PaymentStatus.SUCCESS)
        .order_by(Payment.id.desc())
        .limit(1)
    )
    settled = paid.scalar_one_or_none()
    if settled is not None:
        return settled

    latest = await db.execute(
        select(Payment).where(Payment.order_id == order_id).order_by(Payment.id.desc()).limit(1)
    )
    return latest.scalar_one_or_none()


async def start(
    db: AsyncSession, client: User, order_id: int, payload: PaymentInitCreate
) -> Payment:
    # The order row is locked for the whole check-then-create sequence.
    # Without it, a double-clicked pay button runs this twice concurrently:
    # both reads see no successful payment, both pass the guard, and the
    # customer is handed two live Paystack transactions for the same order and
    # can be charged twice.
    locked = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    order = locked.scalar_one_or_none()

    if order is None or order.client_id != client.id:
        # Same error for missing and someone else's: a 403 would confirm that
        # a given order id exists.
        raise NotFoundError("Order not found.")

    if order.status == OrderStatus.CANCELLED:
        raise ConflictError("This order was cancelled.", code="order_cancelled")

    existing = await latest_for_order(db, order.id)
    if existing is not None and existing.status == PaymentStatus.SUCCESS:
        raise ConflictError("This order has already been paid.", code="already_paid")

    if order.subtotal <= Decimal("0.00"):
        raise ConflictError("This order has nothing to pay for.", code="zero_amount")

    payment = Payment(
        order_id=order.id,
        client_id=client.id,
        reference=_new_reference(order.id),
        amount=order.subtotal,
        currency=settings.payment_currency,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    await db.flush()

    data = await paystack.initialize_transaction(
        email=client.email,
        amount=payment.amount,
        reference=payment.reference,
        callback_url=settings.payment_return_url,
        channels=[channel.value for channel in payload.channels],
        # Echoed back on the webhook and visible in Paystack's dashboard,
        # which is what makes a disputed charge traceable to an order.
        metadata={
            "order_id": order.id,
            "order_number": order.order_number,
            "client_id": client.id,
        },
    )

    authorization_url = data.get("authorization_url")
    if not authorization_url:
        raise paystack.PaystackError("The payment provider did not return a checkout URL.")

    payment.authorization_url = authorization_url
    await db.flush()
    return payment


async def settle(
    db: AsyncSession, reference: str, *, expected_client_id: int | None = None
) -> Payment:
    """Bring a payment to its final state from Paystack's record of it.

    Idempotent: calling it on an already-settled payment returns without
    re-notifying, because the browser and the webhook routinely both arrive.

    `expected_client_id` is checked *before* anything happens, so a caller who
    guesses someone else's reference cannot drive that payment's state or
    trigger notifications to its owner. The webhook passes None - Paystack is
    authenticated by its signature, not by a user.
    """
    # FOR UPDATE, not a plain read. The browser returning and the webhook
    # firing are genuinely concurrent, and without the lock both transactions
    # read PENDING, both verify, and both notify - the customer and every
    # vendor get the payment confirmation twice.
    found = await db.execute(
        select(Payment).where(Payment.reference == reference).with_for_update()
    )
    payment = found.scalar_one_or_none()
    if payment is None:
        raise NotFoundError("Payment not found.")

    if expected_client_id is not None and payment.client_id != expected_client_id:
        raise NotFoundError("Payment not found.")

    # SUCCESS is the only terminal state, deliberately.
    #
    # Mobile money is asynchronous: the payer can return to the site - firing
    # this from the browser - while the approval prompt is still sitting on
    # their phone, and Paystack reports that as pending or abandoned. Treating
    # those as final would make the later charge.success webhook a no-op, and
    # the money would arrive against an order that never records it. So a
    # non-success payment stays re-checkable; duplicate notifications are
    # prevented by comparing against the previous status instead.
    if payment.status == PaymentStatus.SUCCESS:
        return payment

    previous_status = payment.status

    data = await paystack.verify_transaction(reference)

    payment.raw_response = data
    payment.channel = data.get("channel")
    payment.gateway_response = (data.get("gateway_response") or "")[:255]

    gateway_status = (data.get("status") or "").lower()
    if gateway_status != PAYSTACK_SUCCESS:
        # "pending" is not a failure - a mobile money prompt is still with the
        # payer. Recording it as failed would tell them their payment did not
        # go through while it is mid-flight.
        if gateway_status in RETRYABLE_GATEWAY_STATES:
            payment.status = PaymentStatus.PENDING
            await db.flush()
            return payment

        payment.status = (
            PaymentStatus.ABANDONED if gateway_status == "abandoned" else PaymentStatus.FAILED
        )
        payment.failure_reason = payment.gateway_response or gateway_status
        await db.flush()
        # Only on an actual transition, so a repeated callback does not send a
        # second "payment failed" for the same attempt.
        if previous_status != payment.status:
            await _notify_failure(db, payment)
        return payment

    # --- The transaction says success. Check it is the one we asked for. ---
    #
    # `reference` is chosen by the caller of this function, so without these
    # two checks a real Paystack success for GHS 1 - or for another
    # merchant's currency - would mark a GHS 5,000 order as paid.
    paid_amount = paystack.from_subunit(int(data.get("amount") or 0))
    paid_currency = (data.get("currency") or "").upper()

    if paid_currency != payment.currency.upper() or paid_amount < payment.amount:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = (
            f"Amount mismatch: charged {paid_currency} {paid_amount}, "
            f"expected {payment.currency} {payment.amount}."
        )
        logger.error(
            "Payment %s underpaid or wrong currency: %s", payment.reference, payment.failure_reason
        )
        await db.flush()
        await _notify_failure(db, payment)
        return payment

    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = utc_now()
    payment.failure_reason = None

    # The money genuinely arrived, so it is recorded as SUCCESS even if the
    # order was cancelled while the payer was on Paystack's page. Recording it
    # as anything else would be false, and a refund cannot be reasoned about
    # from a payment the system denies receiving. It is flagged loudly instead:
    # `cancel_order` refuses to cancel a paid order, so reaching this means a
    # cancellation landed inside the payment window and a refund is owed.
    order = await _order_with_items(db, payment.order_id)
    if order is not None and order.status == OrderStatus.CANCELLED:
        payment.failure_reason = "Paid against a cancelled order - refund required."
        logger.error(
            "Payment %s settled against cancelled order %s - refund owed to user %s",
            payment.reference,
            order.order_number,
            payment.client_id,
        )

    await db.flush()
    await _notify_success(db, payment)
    return payment


async def _order_with_items(db: AsyncSession, order_id: int) -> Order | None:
    return await db.get(Order, order_id)


async def _notify_success(db: AsyncSession, payment: Payment) -> None:
    order = await _order_with_items(db, payment.order_id)
    if order is None:  # pragma: no cover - the FK makes this unreachable
        return

    await notifications.notify(
        db,
        user_id=payment.client_id,
        type=NotificationType.PAYMENT_RECEIVED,
        message=(
            f"Payment of {payment.currency} {payment.amount} for order "
            f"{order.order_number} was successful."
        ),
        payload={
            "order_id": order.id,
            "order_number": order.order_number,
            "reference": payment.reference,
        },
    )

    # Vendors are told separately: their cue to start fulfilling is the money
    # arriving, not the order being placed.
    for vendor_id in {item.vendor_id for item in await _items(db, order)}:
        await notifications.notify(
            db,
            user_id=vendor_id,
            type=NotificationType.PAYMENT_RECEIVED,
            message=f"Order {order.order_number} has been paid.",
            payload={"order_id": order.id, "order_number": order.order_number},
        )


async def _notify_failure(db: AsyncSession, payment: Payment) -> None:
    order = await _order_with_items(db, payment.order_id)
    if order is None:  # pragma: no cover
        return

    await notifications.notify(
        db,
        user_id=payment.client_id,
        type=NotificationType.PAYMENT_FAILED,
        message=(
            f"Payment for order {order.order_number} did not go through"
            f"{': ' + payment.failure_reason if payment.failure_reason else ''}."
        ),
        payload={
            "order_id": order.id,
            "order_number": order.order_number,
            "reference": payment.reference,
        },
    )


async def _items(db: AsyncSession, order: Order) -> list:
    """Order lines, loaded explicitly.

    `order.items` is a lazy relationship and touching it here would emit IO
    from an attribute access, which raises MissingGreenlet under asyncio.
    """
    from app.modules.orders.models import OrderItem

    rows = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    return list(rows.scalars().all())
