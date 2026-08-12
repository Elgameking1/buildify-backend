from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PaymentChannel, PaymentStatus


class PaymentInitCreate(BaseModel):
    """Which methods to offer at checkout.

    Defaults to both. Restricting to one is what a "Pay with MoMo" button
    sends, so the payer lands on the right tab instead of Paystack's default.
    """

    channels: list[PaymentChannel] = Field(
        default_factory=lambda: [PaymentChannel.CARD, PaymentChannel.MOBILE_MONEY],
        min_length=1,
    )


class PaymentInitRead(BaseModel):
    """Everything the browser needs to continue at Paystack."""

    reference: str
    authorization_url: str
    amount: Decimal
    currency: str


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    reference: str
    amount: Decimal
    currency: str
    status: PaymentStatus
    channel: str | None
    gateway_response: str | None
    paid_at: datetime | None
    created_at: datetime
