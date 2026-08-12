"""Cart management and order placement.

The interesting part of this module is `checkout`, which is the only place in
the system where two users can genuinely race each other.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import (
    NotificationType,
    OrderStatus,
    ProductStatus,
    UserRole,
    VendorItemStatus,
)
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.db.base import utc_now
from app.modules.catalog.models import Product
from app.modules.notifications import service as notifications
from app.modules.orders.models import Cart, CartItem, Order, OrderItem
from app.modules.orders.schemas import CartItemCreate, CartItemUpdate, OrderCreate
from app.modules.users.models import User

# What a vendor may do to one of their order lines.
_VENDOR_TRANSITIONS: dict[VendorItemStatus, set[VendorItemStatus]] = {
    VendorItemStatus.PENDING: {VendorItemStatus.CONFIRMED, VendorItemStatus.CANCELLED},
    VendorItemStatus.CONFIRMED: {VendorItemStatus.READY, VendorItemStatus.CANCELLED},
    VendorItemStatus.READY: set(),
    VendorItemStatus.CANCELLED: set(),
}


# --- Cart ------------------------------------------------------------------


def _cart_query(client_id: int):
    # Eager-load right through to the vendor profile: a cart row shows the
    # seller's business name, and a lazy load at serialisation time would raise
    # under async SQLAlchemy.
    return (
        select(Cart)
        .where(Cart.client_id == client_id)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.product)
            .selectinload(Product.vendor)
            .selectinload(User.vendor_profile)
        )
        .execution_options(populate_existing=True)
    )


async def get_cart(db: AsyncSession, client: User) -> Cart | None:
    """Read-only. Returns None when the client has never added anything.

    `GET /cart` uses this rather than get_or_create_cart so that reading a cart
    never writes to the database - a GET with a side effect would also mean two
    parallel cart fetches on page load could collide on `carts.client_id`.
    """
    return (await db.execute(_cart_query(client.id))).scalar_one_or_none()


async def get_or_create_cart(db: AsyncSession, client: User) -> Cart:
    """Used by the mutating paths, which genuinely need a cart row to exist."""
    cart = await get_cart(db, client)
    if cart is not None:
        return cart

    try:
        # SAVEPOINT, not a plain flush: if a parallel request created the cart
        # first, only this nested block is rolled back and the caller's
        # transaction survives.
        async with db.begin_nested():
            db.add(Cart(client_id=client.id))
            await db.flush()
    except IntegrityError:
        pass  # someone else won the race; the re-read below picks up their row

    # Re-read rather than returning the object just constructed: releasing the
    # savepoint leaves its `items` collection unloaded, and touching it while
    # serialising would raise MissingGreenlet.
    return (await db.execute(_cart_query(client.id))).scalar_one()


async def add_to_cart(db: AsyncSession, client: User, payload: CartItemCreate) -> Cart:
    cart = await get_or_create_cart(db, client)

    product = await db.get(Product, payload.product_id)
    if product is None:
        raise NotFoundError("Product not found.")
    if product.status != ProductStatus.ACTIVE:
        raise ConflictError("That product is not currently available.")

    existing = next((i for i in cart.items if i.product_id == product.id), None)
    desired = (existing.quantity if existing else 0) + payload.quantity

    # Checked again under a row lock at checkout - this is only so the user
    # finds out now rather than at the end.
    if desired > product.stock_qty:
        raise ConflictError(
            f"Only {product.stock_qty} {product.unit.value.lower()} of "
            f"'{product.name}' are in stock.",
            code="insufficient_stock",
        )

    if existing is not None:
        existing.quantity = desired
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=payload.quantity))

    await db.flush()
    return await get_or_create_cart(db, client)


def _find_item(cart: Cart | None, item_id: int) -> CartItem:
    """Locate a line in the caller's own cart.

    Scoping the lookup to this client's cart is what stops one client editing
    another's cart line by guessing an id - there is no separate ownership
    check to forget.
    """
    item = next((i for i in cart.items if i.id == item_id), None) if cart else None
    if item is None:
        raise NotFoundError("Cart item not found.")
    return item


async def update_cart_item(
    db: AsyncSession, client: User, item_id: int, payload: CartItemUpdate
) -> Cart | None:
    item = _find_item(await get_cart(db, client), item_id)

    if payload.quantity > item.product.stock_qty:
        raise ConflictError(
            f"Only {item.product.stock_qty} in stock.", code="insufficient_stock"
        )

    item.quantity = payload.quantity
    await db.flush()
    return await get_cart(db, client)


async def remove_cart_item(db: AsyncSession, client: User, item_id: int) -> Cart | None:
    item = _find_item(await get_cart(db, client), item_id)
    await db.delete(item)
    await db.flush()
    return await get_cart(db, client)


async def clear_cart(db: AsyncSession, client: User) -> Cart | None:
    cart = await get_cart(db, client)
    if cart is None:
        return None
    for item in list(cart.items):
        await db.delete(item)
    await db.flush()
    return await get_cart(db, client)


# --- Checkout --------------------------------------------------------------


async def checkout(db: AsyncSession, client: User, payload: OrderCreate) -> Order:
    """Turn the cart into an order, atomically.

    Everything below happens inside the request's single transaction, which
    `get_db` commits only if this returns.  Any raise rolls back the stock
    decrements along with the order itself.
    """
    cart = await get_cart(db, client)
    if cart is None or not cart.items:
        raise ConflictError("Your cart is empty.", code="empty_cart")

    # Lock every product row before reading stock.  Without SELECT ... FOR
    # UPDATE two simultaneous checkouts both read the old stock level and both
    # succeed, overselling the vendor.  Sorted ids give every transaction the
    # same lock order, so concurrent checkouts queue instead of deadlocking.
    #
    # populate_existing is not optional here.  Loading the cart above already
    # put these Product objects in the identity map, and without it SQLAlchemy
    # hands back those cached instances with their pre-lock `stock_qty` - the
    # lock would be held while the check ran against a stale value.
    product_ids = sorted({item.product_id for item in cart.items})
    locked = await db.execute(
        select(Product)
        .where(Product.id.in_(product_ids))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    products = {product.id: product for product in locked.scalars().all()}

    order = Order(
        order_number="",  # replaced below, once the row has an id
        client_id=client.id,
        status=OrderStatus.PENDING,
        subtotal=Decimal("0.00"),
        delivery_address=payload.delivery_address,
        contact_phone=payload.contact_phone,
        notes=payload.notes,
        placed_at=utc_now(),
    )
    db.add(order)
    await db.flush()
    # Human-readable and unique, derived from the id the flush just assigned.
    order.order_number = f"ORD-{order.placed_at.year}-{order.id:06d}"

    subtotal = Decimal("0.00")
    vendor_ids: set[int] = set()

    for item in cart.items:
        product = products.get(item.product_id)
        if product is None:
            raise NotFoundError(f"Product {item.product_id} no longer exists.")
        if product.status != ProductStatus.ACTIVE:
            raise ConflictError(
                f"'{product.name}' is no longer available.", code="product_unavailable"
            )
        if product.stock_qty < item.quantity:
            raise ConflictError(
                f"Not enough stock for '{product.name}': "
                f"{product.stock_qty} left, {item.quantity} requested.",
                code="insufficient_stock",
            )

        line_total = product.price * item.quantity
        subtotal += line_total
        vendor_ids.add(product.vendor_id)

        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                vendor_id=product.vendor_id,
                # Snapshots: the order must still read correctly after the
                # vendor renames or reprices the product.
                product_name=product.name,
                quantity=item.quantity,
                unit_price=product.price,
                line_total=line_total,
                vendor_status=VendorItemStatus.PENDING,
            )
        )

        product.stock_qty -= item.quantity
        if product.stock_qty == 0:
            product.status = ProductStatus.OUT_OF_STOCK

    order.subtotal = subtotal

    for item in list(cart.items):
        await db.delete(item)

    for vendor_id in vendor_ids:
        await notifications.notify(
            db,
            user_id=vendor_id,
            type=NotificationType.ORDER_PLACED,
            message=f"New order {order.order_number} from {client.full_name}.",
            payload={"order_id": order.id, "order_number": order.order_number},
        )

    await db.flush()
    return await get_order(db, client, order.id)


# --- Reading orders --------------------------------------------------------


def _order_query():
    # populate_existing so that a read straight after a write (checkout, or a
    # vendor advancing a line) reflects what was just flushed rather than the
    # collection state cached earlier in the same request.
    return (
        select(Order)
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.vendor)
            .selectinload(User.vendor_profile)
        )
        .execution_options(populate_existing=True)
    )


def _order_item_query():
    """OrderItem with everything `serialise_order_item` touches."""
    return (
        select(OrderItem)
        .options(selectinload(OrderItem.vendor).selectinload(User.vendor_profile))
        .execution_options(populate_existing=True)
    )


async def get_order(db: AsyncSession, user: User, order_id: int) -> Order:
    stmt = _order_query().where(Order.id == order_id)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise NotFoundError("Order not found.")

    is_owner = order.client_id == user.id
    is_selling_vendor = any(item.vendor_id == user.id for item in order.items)
    if not (is_owner or is_selling_vendor or user.role == UserRole.ADMIN):
        # 404 rather than 403 on purpose: a 403 would confirm that this order
        # id exists, letting an attacker enumerate order ids. Indistinguishable
        # from "no such order" is the whole point.
        raise NotFoundError("Order not found.")
    return order


async def list_client_orders(
    db: AsyncSession, client: User, params: PageParams, *, status: OrderStatus | None = None
) -> tuple[list[Order], int]:
    stmt = _order_query().where(Order.client_id == client.id)
    count_stmt = select(func.count()).select_from(Order).where(Order.client_id == client.id)

    if status is not None:
        stmt = stmt.where(Order.status == status)
        count_stmt = count_stmt.where(Order.status == status)

    stmt = stmt.order_by(Order.placed_at.desc())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().unique().all()), total


async def list_vendor_items(
    db: AsyncSession,
    vendor: User,
    params: PageParams,
    *,
    status: VendorItemStatus | None = None,
) -> tuple[list[tuple[OrderItem, Order, User]], int]:
    """The vendor queue - only lines belonging to this vendor, never whole orders."""
    stmt = (
        select(OrderItem, Order, User)
        .join(Order, OrderItem.order_id == Order.id)
        .join(User, Order.client_id == User.id)
        .where(OrderItem.vendor_id == vendor.id)
    )
    count_stmt = (
        select(func.count()).select_from(OrderItem).where(OrderItem.vendor_id == vendor.id)
    )

    if status is not None:
        stmt = stmt.where(OrderItem.vendor_status == status)
        count_stmt = count_stmt.where(OrderItem.vendor_status == status)

    stmt = stmt.order_by(Order.placed_at.desc(), OrderItem.id.asc())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return [(item, order, client) for item, order, client in rows.all()], total


# --- Fulfilment ------------------------------------------------------------


async def _restock(db: AsyncSession, item: OrderItem) -> None:
    """Return a cancelled line's units to the shelf."""
    product = await db.get(
        Product, item.product_id, with_for_update=True, populate_existing=True
    )
    if product is None:
        return
    product.stock_qty += item.quantity
    if product.status == ProductStatus.OUT_OF_STOCK and product.stock_qty > 0:
        product.status = ProductStatus.ACTIVE


def _roll_up_status(order: Order) -> None:
    """Derive the parent order status from its lines.

    Cancelled lines are excluded from the calculation - one vendor pulling out
    must not stop the rest of the order from completing.
    """
    active = [i for i in order.items if i.vendor_status != VendorItemStatus.CANCELLED]

    if not active:
        order.status = OrderStatus.CANCELLED
    elif all(i.vendor_status == VendorItemStatus.READY for i in active):
        order.status = OrderStatus.FULFILLED
    elif any(
        i.vendor_status in {VendorItemStatus.CONFIRMED, VendorItemStatus.READY}
        for i in active
    ):
        order.status = OrderStatus.CONFIRMED
    else:
        order.status = OrderStatus.PENDING


async def update_vendor_item_status(
    db: AsyncSession, vendor: User, item_id: int, new_status: VendorItemStatus
) -> OrderItem:
    item = await db.get(OrderItem, item_id)
    if item is None:
        raise NotFoundError("Order item not found.")
    if item.vendor_id != vendor.id and vendor.role != UserRole.ADMIN:
        raise PermissionDeniedError("That order line belongs to another vendor.")

    if new_status == item.vendor_status:
        return item

    allowed = _VENDOR_TRANSITIONS[item.vendor_status]
    if new_status not in allowed:
        allowed_names = ", ".join(sorted(s.value for s in allowed)) or "nothing"
        raise ConflictError(
            f"Cannot move a line from {item.vendor_status.value} to "
            f"{new_status.value}. Allowed: {allowed_names}.",
            code="invalid_transition",
        )

    if new_status == VendorItemStatus.CANCELLED:
        await _restock(db, item)

    item.vendor_status = new_status
    await db.flush()

    order = (
        await db.execute(
            select(Order).where(Order.id == item.order_id).options(selectinload(Order.items))
        )
    ).scalar_one()
    _roll_up_status(order)

    await notifications.notify(
        db,
        user_id=order.client_id,
        type=NotificationType.ORDER_ITEM_UPDATED,
        message=f"'{item.product_name}' in order {order.order_number} is now "
        f"{new_status.value.lower()}.",
        payload={"order_id": order.id, "order_item_id": item.id},
    )

    await db.flush()
    # Re-read so the vendor relationship the serialiser needs is loaded.
    return (
        await db.execute(_order_item_query().where(OrderItem.id == item.id))
    ).scalar_one()


async def cancel_order(db: AsyncSession, client: User, order_id: int) -> Order:
    order = await get_order(db, client, order_id)
    if order.client_id != client.id and client.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only the buyer may cancel this order.")

    if order.status in {OrderStatus.FULFILLED, OrderStatus.CANCELLED}:
        raise ConflictError(
            f"A {order.status.value.lower()} order cannot be cancelled.",
            code="invalid_transition",
        )

    for item in order.items:
        if item.vendor_status != VendorItemStatus.CANCELLED:
            await _restock(db, item)
            item.vendor_status = VendorItemStatus.CANCELLED
            await notifications.notify(
                db,
                user_id=item.vendor_id,
                type=NotificationType.ORDER_ITEM_UPDATED,
                message=f"Order {order.order_number} was cancelled by the buyer.",
                payload={"order_id": order.id, "order_item_id": item.id},
            )

    order.status = OrderStatus.CANCELLED
    await db.flush()
    return await get_order(db, client, order.id)
