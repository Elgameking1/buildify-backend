import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.core.config import settings
from app.core.deps import CurrentClient, CurrentUser, DbDep
from app.core.errors import NotFoundError
from app.core.rate_limit import limiter
from app.modules.payments import paystack, service
from app.modules.payments.schemas import PaymentInitCreate, PaymentInitRead, PaymentRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/config")
async def payment_config() -> dict[str, object]:
    """Lets the frontend hide the pay button when no keys are configured.

    Cheaper than discovering it by way of a failed checkout, and it exposes
    nothing secret - only whether the feature is switched on and in which
    currency.
    """
    return {
        "enabled": settings.payments_enabled,
        "currency": settings.payment_currency,
        "channels": ["card", "mobile_money"],
    }


@router.post(
    "/orders/{order_id}/initialize",
    response_model=PaymentInitRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.rate_limit_payment_init)
async def initialize_payment(
    request: Request,
    response: Response,
    order_id: int,
    payload: PaymentInitCreate,
    db: DbDep,
    client: CurrentClient,
) -> PaymentInitRead:
    """Start a payment and hand back Paystack's hosted checkout URL."""
    payment = await service.start(db, client, order_id, payload)
    return PaymentInitRead(
        reference=payment.reference,
        authorization_url=payment.authorization_url or "",
        amount=payment.amount,
        currency=payment.currency,
    )


@router.get("/verify/{reference}", response_model=PaymentRead)
async def verify_payment(reference: str, db: DbDep, user: CurrentUser) -> PaymentRead:
    """Settle a payment on the customer's return from Paystack.

    Ownership is enforced *inside* `settle`, before it does anything. Checking
    afterwards would still be a 404 to the caller, but the settlement and its
    notifications would already have fired against a stranger's payment on a
    guessed reference.
    """
    payment = await service.settle(db, reference, expected_client_id=user.id)
    return PaymentRead.model_validate(payment, from_attributes=True)


@router.post("/webhook", include_in_schema=False)
async def paystack_webhook(
    request: Request,
    db: DbDep,
    x_paystack_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Paystack's server-to-server notification.

    Unauthenticated by necessity - Paystack has no bearer token - so the
    signature is the entire access control. The raw body is used for the
    digest; re-serialising the parsed JSON would change the bytes and never
    match.

    Failures answer 500 so Paystack retries. It redelivers non-2xx for hours,
    which is exactly what a transient database blip needs - answering 200 to
    hide the error would drop the only notice that money arrived, stranding a
    paid order as unpaid whenever the customer also closed the tab. The two
    genuinely unprocessable cases - a bad signature and an event with no
    reference - are answered without inviting a retry, because redelivering
    them would never help.
    """
    raw = await request.body()

    if not paystack.signature_is_valid(raw, x_paystack_signature):
        logger.warning("Rejected Paystack webhook with a bad signature")
        # 401 rather than 200: this did not come from Paystack, so there is no
        # retry storm to worry about, and a silent 200 would hide probing.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    event = await request.json()
    reference = (event.get("data") or {}).get("reference")
    if not reference:
        logger.warning("Paystack webhook carried no reference: %s", event.get("event"))
        return {"status": "ignored"}

    try:
        # The event body is not trusted for the outcome - `settle` re-verifies
        # against Paystack. All this supplies is which reference to look at.
        await service.settle(db, reference)
    except NotFoundError:
        # A reference this system never issued. Retrying cannot fix that, and
        # it is worth seeing in the logs: it means either a misconfigured
        # webhook shared with another integration, or someone probing.
        logger.warning("Paystack webhook for unknown reference %s", reference)
        return {"status": "ignored"}
    except Exception:
        logger.exception("Failed to settle payment %s from webhook", reference)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process event",
        ) from None

    return {"status": "ok"}
