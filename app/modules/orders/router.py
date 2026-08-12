from fastapi import APIRouter, status

from app.core.deps import CurrentClient, CurrentUser, CurrentVendor, DbDep
from app.core.enums import OrderStatus, VendorItemStatus
from app.core.pagination import Page, PageParamsDep
from app.modules.orders import service
from app.modules.orders.schemas import (
    CartItemCreate,
    CartItemUpdate,
    CartRead,
    OrderCreate,
    OrderItemRead,
    OrderRead,
    VendorItemStatusUpdate,
    VendorOrderItemRead,
)
from app.modules.orders.serializers import (
    serialise_cart,
    serialise_order,
    serialise_order_item,
    serialise_vendor_item,
)

router = APIRouter(tags=["orders"])


# --- Cart ------------------------------------------------------------------


@router.get("/cart", response_model=CartRead)
async def read_cart(db: DbDep, client: CurrentClient) -> CartRead:
    """Read-only: an empty cart is reported, not created."""
    return serialise_cart(await service.get_cart(db, client))


@router.post("/cart/items", response_model=CartRead, status_code=status.HTTP_201_CREATED)
async def add_cart_item(payload: CartItemCreate, db: DbDep, client: CurrentClient) -> CartRead:
    return serialise_cart(await service.add_to_cart(db, client, payload))


@router.patch("/cart/items/{item_id}", response_model=CartRead)
async def update_cart_item(
    item_id: int, payload: CartItemUpdate, db: DbDep, client: CurrentClient
) -> CartRead:
    return serialise_cart(await service.update_cart_item(db, client, item_id, payload))


@router.delete("/cart/items/{item_id}", response_model=CartRead)
async def remove_cart_item(item_id: int, db: DbDep, client: CurrentClient) -> CartRead:
    return serialise_cart(await service.remove_cart_item(db, client, item_id))


@router.delete("/cart", response_model=CartRead)
async def clear_cart(db: DbDep, client: CurrentClient) -> CartRead:
    return serialise_cart(await service.clear_cart(db, client))


# --- Client orders ---------------------------------------------------------


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def place_order(payload: OrderCreate, db: DbDep, client: CurrentClient) -> OrderRead:
    """Checkout.

    Stock is verified and decremented under a row lock, prices are frozen onto
    the order lines, and the cart is emptied - all in one transaction.
    """
    return serialise_order(await service.checkout(db, client, payload))


@router.get("/orders", response_model=Page[OrderRead])
async def list_my_orders(
    db: DbDep,
    params: PageParamsDep,
    client: CurrentClient,
    status_filter: OrderStatus | None = None,
) -> Page[OrderRead]:
    orders, total = await service.list_client_orders(db, client, params, status=status_filter)
    return Page.build([serialise_order(o) for o in orders], total, params)


@router.get("/orders/{order_id}", response_model=OrderRead)
async def read_order(order_id: int, db: DbDep, user: CurrentUser) -> OrderRead:
    """Readable by the buyer and by any vendor with a line on it."""
    return serialise_order(await service.get_order(db, user, order_id))


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(order_id: int, db: DbDep, client: CurrentClient) -> OrderRead:
    """Cancels every outstanding line and returns the stock to the vendors."""
    return serialise_order(await service.cancel_order(db, client, order_id))


# --- Vendor fulfilment queue ----------------------------------------------


@router.get("/vendor/orders", response_model=Page[VendorOrderItemRead])
async def list_vendor_queue(
    db: DbDep,
    params: PageParamsDep,
    vendor: CurrentVendor,
    status_filter: VendorItemStatus | None = None,
) -> Page[VendorOrderItemRead]:
    rows, total = await service.list_vendor_items(db, vendor, params, status=status_filter)
    return Page.build(
        [serialise_vendor_item(item, order, client) for item, order, client in rows],
        total,
        params,
    )


@router.patch("/vendor/orders/items/{item_id}", response_model=OrderItemRead)
async def update_vendor_item_status(
    item_id: int, payload: VendorItemStatusUpdate, db: DbDep, vendor: CurrentVendor
) -> OrderItemRead:
    """Advance one line. The parent order's status is recalculated from its lines."""
    item = await service.update_vendor_item_status(db, vendor, item_id, payload.vendor_status)
    return serialise_order_item(item)
