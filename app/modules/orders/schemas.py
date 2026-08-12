from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.enums import OrderStatus, PaymentStatus, ProductUnit, VendorItemStatus


class CartProductSummary(BaseModel):
    """Just enough of a product to render a cart row."""

    id: int
    name: str
    slug: str
    unit: ProductUnit
    price: Decimal
    stock_qty: int
    image_url: str | None = None
    vendor_id: int
    vendor_name: str


class CartItemRead(BaseModel):
    id: int
    product: CartProductSummary
    quantity: int
    line_total: Decimal = Field(description="Live price x quantity, not a stored value")


class CartRead(BaseModel):
    id: int | None = Field(description="Null until the client adds their first item")
    items: list[CartItemRead]
    item_count: int
    subtotal: Decimal


class CartItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=9999)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=9999)


class OrderCreate(BaseModel):
    delivery_address: str = Field(min_length=5, max_length=400)
    contact_phone: str = Field(min_length=7, max_length=32)
    notes: str | None = Field(default=None, max_length=1000)


class OrderItemRead(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    vendor_id: int
    vendor_name: str
    vendor_status: VendorItemStatus


class OrderRead(BaseModel):
    id: int
    order_number: str
    status: OrderStatus
    subtotal: Decimal
    delivery_address: str
    contact_phone: str
    notes: str | None = None
    placed_at: datetime
    items: list[OrderItemRead]
    # Null when no payment has been attempted. Kept separate from `status`,
    # which tracks vendor fulfilment - an order can be paid and unfulfilled, or
    # confirmed by a vendor with an abandoned payment behind it.
    payment_status: PaymentStatus | None = None
    payment_reference: str | None = None


class VendorOrderItemRead(BaseModel):
    """The vendor's queue: one row per line they are responsible for."""

    id: int
    order_id: int
    order_number: str
    placed_at: datetime
    order_status: OrderStatus
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    vendor_status: VendorItemStatus
    client_name: str
    client_phone: str | None = None
    delivery_address: str


class VendorItemStatusUpdate(BaseModel):
    vendor_status: VendorItemStatus
