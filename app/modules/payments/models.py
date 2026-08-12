"""Payment attempts against an order.

One order can have several rows here: a customer who abandons Paystack's
checkout and comes back later produces a second attempt, and the first must
survive so the history stays auditable.  `service.latest_for_order` is what
callers use to answer "is this order paid".

Money is stored in major units (`Decimal(12,2)` cedis) to match `orders.subtotal`.
Paystack works in the minor unit, so the conversion lives in one place - see
`paystack.to_subunit`.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PaymentStatus
from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.orders.models import Order
    from app.modules.users.models import User


class Payment(PKMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_order_status", "order_id", "status"),)

    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Our own idempotency key, generated before Paystack is called and used as
    # the transaction reference.  Unique so that a webhook replay - Paystack
    # retries on any non-200 - resolves to the same row instead of creating a
    # second one.
    reference: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="GHS")

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, native_enum=False, length=16),
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    # Which channel the payer actually used ("card", "mobile_money"). Unknown
    # until the transaction completes, because the choice is made on Paystack's
    # page rather than ours.
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)

    authorization_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gateway_response: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # The verified transaction as Paystack returned it. Kept whole because a
    # disputed payment is argued from the gateway's record, not from the four
    # fields we happened to map at the time.
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="payments")  # noqa: F821
    client: Mapped["User"] = relationship()  # noqa: F821
