"""Test fixtures.

Schema management uses the *synchronous* engine deliberately: doing DDL from a
session-scoped async fixture means juggling event-loop scopes, and gets you
nothing here.  Each test then gets a fresh async engine bound to its own loop.
"""

import os

# Must be set before app.core.config is imported - the limiter reads it at
# import time. Rate limiting is now on by default in every environment, and the
# suite registers and logs in far more often than a human would, so it would
# otherwise throttle itself.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.registry import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_DATABASE = f"{settings.mysql_database}_test"
DEMO_PASSWORD = "DemoPass!2026"


def _swap_database(url: str) -> str:
    head, _, tail = url.rpartition(f"/{settings.mysql_database}")
    return f"{head}/{TEST_DATABASE}{tail}"


SYNC_TEST_URL = _swap_database(settings.sync_database_url)
ASYNC_TEST_URL = _swap_database(settings.database_url)


@pytest.fixture(scope="session")
def schema_engine():
    """Create the schema once for the whole run, drop it at the end."""
    engine = create_engine(SYNC_TEST_URL, poolclass=NullPool)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(schema_engine):
    """Empty every table before each test, so tests cannot leak into each other."""
    with schema_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    yield


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine(ASYNC_TEST_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(async_engine):
    return async_sessionmaker(bind=async_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def client(session_factory):
    """HTTP client wired to the test database via a dependency override."""

    async def _get_test_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


# --- Helpers ---------------------------------------------------------------

API = settings.api_v1_prefix


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register(
    client: AsyncClient, role: str, email: str, **extra
) -> dict:
    """Register an account and return the auth payload (tokens + user)."""
    body = {
        "email": email,
        "password": "DemoPass!2026",
        "full_name": extra.pop("full_name", f"{role.title()} Demo"),
        "role": role,
        **extra,
    }
    if role == "VENDOR":
        body.setdefault("business_name", "Demo Supplies Ltd")

    response = await client.post(f"{API}/auth/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def make_admin(session_factory, user_id: int) -> None:
    """Promote a user directly in the database.

    Admin accounts cannot be self-registered, and categories need one.
    """
    from app.core.enums import UserRole
    from app.modules.users.models import User

    async with session_factory() as session:
        user = await session.get(User, user_id)
        user.role = UserRole.ADMIN
        await session.commit()


async def create_category(client: AsyncClient, token: str, name: str = "Cement") -> int:
    response = await client.post(
        f"{API}/categories", json={"name": name}, headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_product(
    client: AsyncClient,
    token: str,
    category_id: int,
    *,
    name: str = "Ghacem 50kg",
    price: str = "95.00",
    stock: int = 10,
) -> dict:
    response = await client.post(
        f"{API}/products",
        json={
            "name": name,
            "description": "Portland cement suitable for general construction.",
            "category_id": category_id,
            "unit": "BAG",
            "price": price,
            "stock_qty": stock,
            "status": "ACTIVE",
        },
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def seed_marketplace(client: AsyncClient, session_factory) -> dict:
    """A vendor with one product, plus a client - the usual starting point."""
    admin = await register(client, "CLIENT", "admin-seed@test.com")
    await make_admin(session_factory, admin["user"]["id"])
    admin = (
        await client.post(
            f"{API}/auth/login",
            json={"email": "admin-seed@test.com", "password": "DemoPass!2026"},
        )
    ).json()

    category_id = await create_category(client, admin["access_token"])
    vendor = await register(client, "VENDOR", "vendor-seed@test.com")
    product = await create_product(
        client, vendor["access_token"], category_id, stock=10
    )
    buyer = await register(client, "CLIENT", "client-seed@test.com")

    return {
        "admin": admin,
        "vendor": vendor,
        "buyer": buyer,
        "category_id": category_id,
        "product": product,
    }


async def place_order(client: AsyncClient, token: str, product_id: int, quantity: int = 1):
    add = await client.post(
        f"{API}/cart/items",
        json={"product_id": product_id, "quantity": quantity},
        headers=auth(token),
    )
    assert add.status_code == 201, add.text
    return await client.post(
        f"{API}/orders",
        json={
            "delivery_address": "12 Independence Avenue, Accra",
            "contact_phone": "0244000000",
        },
        headers=auth(token),
    )


