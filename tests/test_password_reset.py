"""Forgot-password / reset flow.

The properties worth defending here are the ones that make this a reset rather
than a back door: the endpoint must not reveal whether an address is
registered, the token must be single-use and expiring, and using it must end
every session the account already had.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password_reset_token
from app.db.base import utc_now
from app.modules.users.models import PasswordResetToken, RefreshToken
from tests.conftest import API, register

NEW_PASSWORD = "A-Brand-New-Pass!2026"


@pytest.fixture(autouse=True)
def return_link(monkeypatch):
    """Hand the token back in the response so tests can use it.

    Mirrors the demo configuration; the security tests below assert what the
    endpoint reveals regardless of this setting.
    """
    monkeypatch.setattr(settings, "password_reset_return_link", True, raising=False)


async def _account(client, email="resetme@example.com"):
    return await register(client, "CLIENT", email)


# --- Enumeration -----------------------------------------------------------


async def test_unknown_and_known_addresses_are_indistinguishable(client):
    """The whole point: this must not be a "does this email exist" oracle."""
    await _account(client)

    known = await client.post(
        f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
    )
    unknown = await client.post(
        f"{API}/auth/forgot-password", json={"email": "nobody-here@example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json()["detail"] == unknown.json()["detail"]
    # The token is the one difference, and it exists only because this
    # deployment has no mailer. It is never the *presence* of a field that
    # differs - both responses carry the key.
    assert "reset_token" in known.json() and "reset_token" in unknown.json()
    assert unknown.json()["reset_token"] is None


# --- The happy path --------------------------------------------------------


async def test_reset_lets_the_user_sign_in_with_the_new_password(client):
    await _account(client)
    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    reset = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert reset.status_code == 204

    old = await client.post(
        f"{API}/auth/login",
        json={"email": "resetme@example.com", "password": "DemoPass!2026"},
    )
    assert old.status_code == 401, "the previous password must stop working"

    new = await client.post(
        f"{API}/auth/login",
        json={"email": "resetme@example.com", "password": NEW_PASSWORD},
    )
    assert new.status_code == 200


# --- Token hygiene ---------------------------------------------------------


async def test_a_token_works_only_once(client):
    await _account(client)
    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    first = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert first.status_code == 204

    second = await client.post(
        f"{API}/auth/reset-password",
        json={"token": token, "password": "Another-Pass!2026"},
    )
    assert second.status_code == 401, "a link left in an inbox must not stay a key"


async def test_requesting_again_invalidates_the_previous_link(client):
    await _account(client)
    first = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]
    second = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    assert first != second
    stale = await client.post(
        f"{API}/auth/reset-password", json={"token": first, "password": NEW_PASSWORD}
    )
    assert stale.status_code == 401


async def test_an_expired_token_is_refused(client, session_factory):
    await _account(client)
    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    async with session_factory() as session:
        row = (
            await session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash == hash_password_reset_token(token)
                )
            )
        ).scalar_one()
        row.expires_at = utc_now() - timedelta(minutes=1)
        await session.commit()

    response = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 401


async def test_a_forged_token_is_refused(client):
    await _account(client)
    response = await client.post(
        f"{API}/auth/reset-password",
        json={"token": "x" * 64, "password": NEW_PASSWORD},
    )
    assert response.status_code == 401


async def test_only_the_digest_is_stored(client, session_factory):
    """A database leak must not yield usable reset links."""
    await _account(client)
    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    async with session_factory() as session:
        rows = (await session.execute(select(PasswordResetToken))).scalars().all()
        assert rows, "a token row should exist"
        for row in rows:
            assert row.token_hash != token
            assert row.token_hash == hash_password_reset_token(token)


# --- Session invalidation --------------------------------------------------


async def test_resetting_kills_every_existing_session(client, session_factory):
    """Someone resets because they think they are compromised.

    Leaving the attacker's refresh token alive would make the reset ceremonial.
    """
    account = await _account(client)
    refresh_token = account["refresh_token"]

    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]
    await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )

    replay = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert replay.status_code == 401

    async with session_factory() as session:
        live = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.revoked_at.is_(None))
            )
        ).scalars().all()
        assert live == []


# --- Password policy still applies ----------------------------------------


async def test_a_weak_new_password_is_rejected(client):
    await _account(client)
    token = (
        await client.post(
            f"{API}/auth/forgot-password", json={"email": "resetme@example.com"}
        )
    ).json()["reset_token"]

    response = await client.post(
        f"{API}/auth/reset-password", json={"token": token, "password": "password123"}
    )
    assert response.status_code in (400, 422)
