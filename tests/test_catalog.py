"""Product ownership, visibility and search."""

from tests.conftest import (
    API,
    auth,
    create_category,
    create_product,
    make_admin,
    register,
    seed_marketplace,
)


async def test_vendor_can_create_and_list_own_product(client, session_factory):
    world = await seed_marketplace(client, session_factory)

    response = await client.get(
        f"{API}/vendor/products", headers=auth(world["vendor"]["access_token"])
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_vendor_cannot_edit_another_vendors_product(client, session_factory):
    """A valid VENDOR token proves the caller is a vendor, not the owner."""
    world = await seed_marketplace(client, session_factory)
    intruder = await register(client, "VENDOR", "intruder@test.com")

    response = await client.patch(
        f"{API}/products/{world['product']['id']}",
        json={"price": "1.00"},
        headers=auth(intruder["access_token"]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


async def test_client_cannot_create_a_product(client, session_factory):
    world = await seed_marketplace(client, session_factory)

    response = await client.post(
        f"{API}/products",
        json={
            "name": "Sneaky Listing",
            "category_id": world["category_id"],
            "unit": "BAG",
            "price": "10.00",
            "stock_qty": 1,
        },
        headers=auth(world["buyer"]["access_token"]),
    )

    assert response.status_code == 403


async def test_archived_products_disappear_from_public_browsing(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    product_id = world["product"]["id"]

    await client.delete(
        f"{API}/products/{product_id}", headers=auth(world["vendor"]["access_token"])
    )

    public = await client.get(f"{API}/products")
    assert all(item["id"] != product_id for item in public.json()["items"])

    # ...but the vendor still sees it on their own dashboard.
    dashboard = await client.get(
        f"{API}/vendor/products", headers=auth(world["vendor"]["access_token"])
    )
    assert dashboard.json()["items"][0]["status"] == "ARCHIVED"


async def test_keyword_search_finds_a_product(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    await create_product(
        client,
        world["vendor"]["access_token"],
        world["category_id"],
        name="Roofing Sheet Aluzinc",
        price="78.00",
    )

    response = await client.get(f"{API}/products", params={"q": "roofing"})

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert "Roofing Sheet Aluzinc" in names


async def test_price_filter_and_sort(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    token = world["vendor"]["access_token"]
    await create_product(
        client, token, world["category_id"], name="Cheap Block", price="6.50"
    )
    await create_product(
        client, token, world["category_id"], name="Expensive Tank", price="950.00"
    )

    response = await client.get(
        f"{API}/products", params={"max_price": "100", "sort": "price_asc"}
    )

    prices = [float(item["price"]) for item in response.json()["items"]]
    assert prices == sorted(prices)
    assert all(price <= 100 for price in prices)


async def test_category_filter_includes_subcategories(client, session_factory):
    world = await seed_marketplace(client, session_factory)
    admin_token = world["admin"]["access_token"]

    child = await client.post(
        f"{API}/categories",
        json={"name": "Bagged Cement", "parent_id": world["category_id"]},
        headers=auth(admin_token),
    )
    child_id = child.json()["id"]
    await create_product(
        client,
        world["vendor"]["access_token"],
        child_id,
        name="Dangote 50kg",
        price="92.50",
    )

    response = await client.get(
        f"{API}/products", params={"category_id": world["category_id"]}
    )

    names = [item["name"] for item in response.json()["items"]]
    assert "Dangote 50kg" in names, "a parent category must include its children's products"


async def test_only_admins_may_create_categories(client, session_factory):
    vendor = await register(client, "VENDOR", "cat-vendor@test.com")

    response = await client.post(
        f"{API}/categories", json={"name": "Unauthorised"}, headers=auth(vendor["access_token"])
    )

    assert response.status_code == 403


async def test_stock_reaching_zero_marks_the_product_out_of_stock(client, session_factory):
    admin = await register(client, "CLIENT", "stock-admin@test.com")
    await make_admin(session_factory, admin["user"]["id"])
    admin = (
        await client.post(
            f"{API}/auth/login",
            json={"email": "stock-admin@test.com", "password": "DemoPass!2026"},
        )
    ).json()
    category_id = await create_category(client, admin["access_token"], name="Steel")
    vendor = await register(client, "VENDOR", "stock-vendor@test.com")
    product = await create_product(client, vendor["access_token"], category_id, stock=5)

    response = await client.patch(
        f"{API}/products/{product['id']}",
        json={"stock_qty": 0},
        headers=auth(vendor["access_token"]),
    )

    assert response.json()["status"] == "OUT_OF_STOCK"
