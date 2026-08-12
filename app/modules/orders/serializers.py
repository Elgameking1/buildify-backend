"""ORM -> schema conversion for carts and orders."""

from decimal import Decimal

from app.modules.catalog.models import Product
from app.modules.media.r2 import public_url
from app.modules.orders.models import Cart, Order, OrderItem
from app.modules.orders.schemas import (
    CartItemRead,
    CartProductSummary,
    CartRead,
    OrderItemRead,
    OrderRead,
    VendorOrderItemRead,
)
from app.modules.users.models import User


def _vendor_name(vendor: User | None) -> str:
    if vendor is None:
        return "Unknown vendor"
    profile = getattr(vendor, "vendor_profile", None)
    return profile.business_name if profile else vendor.full_name


def serialise_cart_product(product: Product) -> CartProductSummary:
    first_image = product.images[0] if product.images else None
    return CartProductSummary(
        id=product.id,
        name=product.name,
        slug=product.slug,
        unit=product.unit,
        price=product.price,
        stock_qty=product.stock_qty,
        image_url=public_url(first_image.object_key) if first_image else None,
        vendor_id=product.vendor_id,
        vendor_name=_vendor_name(product.vendor),
    )


def serialise_cart(cart: Cart | None) -> CartRead:
    """Line totals are computed from the live product price, never stored.

    A client who has never added anything has no cart row at all; that is
    represented as an empty cart with a null id rather than by creating a row
    on a GET.
    """
    if cart is None:
        return CartRead(id=None, items=[], item_count=0, subtotal=Decimal("0.00"))

    items: list[CartItemRead] = []
    subtotal = Decimal("0.00")

    for item in sorted(cart.items, key=lambda i: i.id):
        line_total = item.product.price * item.quantity
        subtotal += line_total
        items.append(
            CartItemRead(
                id=item.id,
                product=serialise_cart_product(item.product),
                quantity=item.quantity,
                line_total=line_total,
            )
        )

    return CartRead(
        id=cart.id,
        items=items,
        item_count=sum(item.quantity for item in cart.items),
        subtotal=subtotal,
    )


def serialise_order_item(item: OrderItem) -> OrderItemRead:
    return OrderItemRead(
        id=item.id,
        product_id=item.product_id,
        product_name=item.product_name,
        quantity=item.quantity,
        unit_price=item.unit_price,
        line_total=item.line_total,
        vendor_id=item.vendor_id,
        vendor_name=_vendor_name(item.vendor),
        vendor_status=item.vendor_status,
    )


def serialise_order(order: Order) -> OrderRead:
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        delivery_address=order.delivery_address,
        contact_phone=order.contact_phone,
        notes=order.notes,
        placed_at=order.placed_at,
        items=[serialise_order_item(item) for item in sorted(order.items, key=lambda i: i.id)],
    )


def serialise_vendor_item(item: OrderItem, order: Order, client: User) -> VendorOrderItemRead:
    return VendorOrderItemRead(
        id=item.id,
        order_id=order.id,
        order_number=order.order_number,
        placed_at=order.placed_at,
        order_status=order.status,
        product_id=item.product_id,
        product_name=item.product_name,
        quantity=item.quantity,
        unit_price=item.unit_price,
        line_total=item.line_total,
        vendor_status=item.vendor_status,
        client_name=client.full_name,
        client_phone=order.contact_phone or client.phone,
        delivery_address=order.delivery_address,
    )
