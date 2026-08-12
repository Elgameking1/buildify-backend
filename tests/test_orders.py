"""Cart, checkout and fulfilment.

The concurrency test at the bottom is the one that matters most: it is the only
place in the system where two users can genuinely race each other.
"""

import asyncio

from app.core.errors import ConflictError
from app.modules.catalog.models import Product
from app.modules.orders import service as orders_service
from app.modules.orders.schemas import OrderCreate
from app.modules.users.models import User
from tests.conftest import API, auth, create_product, place_order, register, seed_marketplace

ORDER_BODY = {
    "delivery_address": "12 Independence Avenue, Accra",
    "contact_phone": "0244000000",
}


async def test_checkout_creates_an_order_and_decrements_stock(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    product_id = world["product"]["id"]

    response = await place_order(client, world["buyer"]["access_token"], product_id, 3)

    assert response.status_code == 201, response.text
    order = response.json()
    assert order["order_number"].startswith("ORD-")
    assert order["status"] == "PENDING"
    assert float(order["subtotal"]) == 285.0  # 3 x 95.00
    assert order["items"][0]["quantity"] == 3

    product = (await client.get(f"{API}/products/{product_id}")).json()
    assert product["stock_qty"] == 7


async def test_reading_an_untouched_cart_creates_nothing(client, session_factory):
    """A GET must not write. An empty cart is reported, not created."""
    world = await seed_marketplace(client, session_factory)

    response = await client.get(f"{API}/cart", headers=auth(world["buyer"]["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] is None
    assert body["items"] == []
    assert body["item_count"] == 0


async def test_checkout_empties_the_cart(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    token = world["buyer"]["access_token"]

    await place_order(client, token, world["product"]["id"], 1)

    cart = await client.get(f"{API}/cart", headers=auth(token))
    assert cart.json()["items"] == []


async def test_checkout_with_an_empty_cart_is_rejected(client, session_factory):
    world = await seed_marketplace(client, session_factory)

    response = await client.post(
        f"{API}/orders", json=ORDER_BODY, headers=auth(world["buyer"]["access_token"])
    )

    assert response.status_code == 409
    assert response.json()["code"] == "empty_cart"


async def test_adding_more_than_stock_to_the_cart_is_rejected(client, session_factory):
    world = await seed_marketplace(client, session_factory)

    response = await client.post(
        f"{API}/cart/items",
        json={"product_id": world["product"]["id"], "quantity": 99},
        headers=auth(world["buyer"]["access_token"]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_stock"


async def test_insufficient_stock_at_checkout_rolls_everything_back(
    client, session_factory
):
    """Stock is dropped from under the buyer after the item is already in the cart."""
    world = await seed_marketplace(client, session_factory)
    product_id = world["product"]["id"]
    buyer_token = world["buyer"]["access_token"]

    await client.post(
        f"{API}/cart/items",
        json={"product_id": product_id, "quantity": 8},
        headers=auth(buyer_token),
    )
    await client.patch(
        f"{API}/products/{product_id}",
        json={"stock_qty": 2},
        headers=auth(world["vendor"]["access_token"]),
    )

    response = await client.post(f"{API}/orders", json=ORDER_BODY, headers=auth(buyer_token))

    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_stock"

    # Nothing was consumed and no order was written.
    product = (await client.get(f"{API}/products/{product_id}")).json()
    assert product["stock_qty"] == 2
    orders = await client.get(f"{API}/orders", headers=auth(buyer_token))
    assert orders.json()["total"] == 0


async def test_vendor_sees_only_their_own_order_lines(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    other_vendor = await register(client, "VENDOR", "other-vendor@test.com")
    await create_product(
        client,
        other_vendor["access_token"],
        world["category_id"],
        name="Other Vendor Rod",
        price="115.00",
    )

    await place_order(client, world["buyer"]["access_token"], world["product"]["id"], 1)

    mine = await client.get(
        f"{API}/vendor/orders", headers=auth(world["vendor"]["access_token"])
    )
    theirs = await client.get(
        f"{API}/vendor/orders", headers=auth(other_vendor["access_token"])
    )

    assert mine.json()["total"] == 1
    assert theirs.json()["total"] == 0


async def test_order_becomes_fulfilled_once_every_line_is_ready(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    vendor_token = world["vendor"]["access_token"]
    order = (
        await place_order(client, world["buyer"]["access_token"], world["product"]["id"], 1)
    ).json()
    item_id = order["items"][0]["id"]

    await client.patch(
        f"{API}/vendor/orders/items/{item_id}",
        json={"vendor_status": "CONFIRMED"},
        headers=auth(vendor_token),
    )
    confirmed = await client.get(
        f"{API}/orders/{order['id']}", headers=auth(world["buyer"]["access_token"])
    )
    assert confirmed.json()["status"] == "CONFIRMED"

    await client.patch(
        f"{API}/vendor/orders/items/{item_id}",
        json={"vendor_status": "READY"},
        headers=auth(vendor_token),
    )
    fulfilled = await client.get(
        f"{API}/orders/{order['id']}", headers=auth(world["buyer"]["access_token"])
    )
    assert fulfilled.json()["status"] == "FULFILLED"


async def test_illegal_vendor_line_transition_is_rejected(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    order = (
        await place_order(client, world["buyer"]["access_token"], world["product"]["id"], 1)
    ).json()

    response = await client.patch(
        f"{API}/vendor/orders/items/{order['items'][0]['id']}",
        json={"vendor_status": "READY"},  # PENDING -> READY skips CONFIRMED
        headers=auth(world["vendor"]["access_token"]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


async def test_cancelling_an_order_returns_the_stock(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    buyer_token = world["buyer"]["access_token"]
    product_id = world["product"]["id"]
    order = (await place_order(client, buyer_token, product_id, 4)).json()

    assert (await client.get(f"{API}/products/{product_id}")).json()["stock_qty"] == 6

    response = await client.post(
        f"{API}/orders/{order['id']}/cancel", headers=auth(buyer_token)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert (await client.get(f"{API}/products/{product_id}")).json()["stock_qty"] == 10


async def test_a_buyer_cannot_read_someone_elses_order(client, session_factory):
    """404, not 403.

    A 403 would confirm the order id exists, letting an attacker enumerate
    valid ids. The response must be indistinguishable from "no such order".
    """
    world = await seed_marketplace(client, session_factory)
    order = (
        await place_order(client, world["buyer"]["access_token"], world["product"]["id"], 1)
    ).json()
    stranger = await register(client, "CLIENT", "stranger@test.com")
    headers = auth(stranger["access_token"])

    someone_elses = await client.get(f"{API}/orders/{order['id']}", headers=headers)
    nonexistent = await client.get(f"{API}/orders/999999", headers=headers)

    assert someone_elses.status_code == 404
    assert someone_elses.json() == nonexistent.json(), "responses must be indistinguishable"


async def test_concurrent_checkout_does_not_oversell(client, session_factory):
    """Two buyers race for the last unit. Exactly one may win.

    Without `SELECT ... FOR UPDATE` in `checkout`, both transactions read the
    same stock level, both pass the check, and the vendor is left owing stock
    they do not have.
    """
    world = await seed_marketplace(client, session_factory)
    product_id = world["product"]["id"]

    # One unit left, and two buyers each holding it in their cart.
    await client.patch(
        f"{API}/products/{product_id}",
        json={"stock_qty": 1},
        headers=auth(world["vendor"]["access_token"]),
    )

    buyer_one = world["buyer"]
    buyer_two = await register(client, "CLIENT", "racer@test.com")
    for buyer in (buyer_one, buyer_two):
        response = await client.post(
            f"{API}/cart/items",
            json={"product_id": product_id, "quantity": 1},
            headers=auth(buyer["access_token"]),
        )
        assert response.status_code == 201, response.text

    async def attempt(user_id: int) -> str:
        # A separate session per buyer means two real database transactions.
        async with session_factory() as session:
            user = await session.get(User, user_id)
            try:
                await orders_service.checkout(session, user, OrderCreate(**ORDER_BODY))
                await session.commit()
                return "ok"
            except ConflictError:
                await session.rollback()
                return "conflict"

    results = await asyncio.gather(
        attempt(buyer_one["user"]["id"]), attempt(buyer_two["user"]["id"])
    )

    assert sorted(results) == ["conflict", "ok"], f"expected exactly one winner, got {results}"

    async with session_factory() as session:
        product = await session.get(Product, product_id)
        assert product.stock_qty == 0, "stock must never go negative"
