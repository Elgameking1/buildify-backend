"""Cart and order tables.

Payment lives in its own module (`app.modules.payments`) rather than as columns
here.  One order can carry several attempts - an abandoned checkout followed by
a successful retry - so it is a separate table with its own history, and
`Order.status` stays what `_roll_up_status` derives from the vendor lines rather
than becoming a second, conflicting record of whether money arrived.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import OrderStatus, VendorItemStatus
from app.db.base import Base, PKMixin, TimestampMixin, utc_now

if TYPE_CHECKING:
    from app.modules.catalog.models import Product
    from app.modules.payments.models import Payment
    from app.modules.users.models import User


class Cart(PKMixin, TimestampMixin, Base):
    """One open cart per client, created lazily on first use."""

    __tablename__ = "carts"

    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    client: Mapped["User"] = relationship()
    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", lazy="selectin"
    )


class CartItem(PKMixin, TimestampMixin, Base):
    """Deliberately stores no price.

    The cart shows the live product price; the price is frozen only at
    checkout, into `OrderItem.unit_price`.
    """

    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "product_id", name="uq_cart_item_product"),)

    cart_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    cart: Mapped["Cart"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(lazy="selectin")


class Order(PKMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_client_placed", "client_id", "placed_at"),)

    order_number: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, native_enum=False, length=16),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    delivery_address: Mapped[str] = mapped_column(String(400), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.now(), nullable=False
    )

    client: Mapped["User"] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    # selectin, like items: every order is serialised with its payment state,
    # and a lazy load would fire IO from attribute access during serialisation,
    # which raises MissingGreenlet under asyncio.
    payments: Mapped[list["Payment"]] = relationship(  # noqa: F821
        "Payment",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Payment.id",
    )


class OrderItem(PKMixin, TimestampMixin, Base):
    """A single line of an order.

    `vendor_id` and `vendor_status` are denormalised onto the line on purpose.
    One client order can span several vendors, and each vendor must see and act
    on only their own lines - this delivers per-vendor fulfilment without a
    separate `sub_orders` table.
    """

    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_vendor_status", "vendor_id", "vendor_status"),)

    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Snapshot fields: a later vendor price change must not rewrite history.
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    vendor_status: Mapped[VendorItemStatus] = mapped_column(
        SAEnum(VendorItemStatus, native_enum=False, length=16),
        default=VendorItemStatus.PENDING,
        nullable=False,
    )

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()
    vendor: Mapped["User"] = relationship()
