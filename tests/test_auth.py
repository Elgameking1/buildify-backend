"""Registration, login and token lifecycle."""

from tests.conftest import API, auth, register


async def test_register_creates_account_and_returns_tokens(client):
    payload = await register(client, "CLIENT", "new-client@test.com")

    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["user"]["email"] == "new-client@test.com"
    assert payload["user"]["role"] == "CLIENT"


async def test_register_rejects_duplicate_email(client):
    await register(client, "CLIENT", "duplicate@test.com")

    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "duplicate@test.com",
            "password": "DemoPass!2026",
            "full_name": "Someone Else",
            "role": "CLIENT",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


async def test_vendor_registration_requires_business_name(client):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "vendor-no-name@test.com",
            "password": "DemoPass!2026",
            "full_name": "Vendor Without Business",
            "role": "VENDOR",
        },
    )

    assert response.status_code == 422


async def test_admin_cannot_self_register(client):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "sneaky-admin@test.com",
            "password": "DemoPass!2026",
            "full_name": "Sneaky Admin",
            "role": "ADMIN",
        },
    )

    assert response.status_code == 422


async def test_login_returns_usable_token(client):
    await register(client, "CLIENT", "login@test.com")

    login = await client.post(
        f"{API}/auth/login", json={"email": "login@test.com", "password": "DemoPass!2026"}
    )
    assert login.status_code == 200

    me = await client.get(f"{API}/users/me", headers=auth(login.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "login@test.com"


async def test_login_with_wrong_password_is_rejected(client):
    await register(client, "CLIENT", "wrongpass@test.com")

    response = await client.post(
        f"{API}/auth/login", json={"email": "wrongpass@test.com", "password": "NotThePassword"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_unknown_email_gives_the_same_error_as_a_wrong_password(client):
    """Different messages here would turn the login form into an account oracle."""
    response = await client.post(
        f"{API}/auth/login", json={"email": "nobody@test.com", "password": "DemoPass!2026"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_request_without_token_is_rejected(client):
    response = await client.get(f"{API}/users/me")
    assert response.status_code == 401


async def test_refresh_rotates_the_token(client):
    payload = await register(client, "CLIENT", "refresh@test.com")

    first = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": payload["refresh_token"]}
    )
    assert first.status_code == 200
    assert first.json()["refresh_token"] != payload["refresh_token"]

    # The old token was revoked by that rotation.
    replay = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": payload["refresh_token"]}
    )
    assert replay.status_code == 401


async def test_logout_revokes_the_refresh_token(client):
    payload = await register(client, "CLIENT", "logout@test.com")

    logout = await client.post(
        f"{API}/auth/logout", json={"refresh_token": payload["refresh_token"]}
    )
    assert logout.status_code == 204

    response = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": payload["refresh_token"]}
    )
    assert response.status_code == 401


async def test_worker_registration_creates_a_worker_profile(client):
    payload = await register(
        client, "WORKER", "worker-profile@test.com", headline="Mason", region="Greater Accra"
    )

    response = await client.get(
        f"{API}/workers/me", headers=auth(payload["access_token"])
    )
    assert response.status_code == 200
    assert response.json()["headline"] == "Mason"
