"""Regression tests for the findings raised in the security audit.

Each test names the finding it pins down, so a future change that quietly
undoes one of these fails here rather than in production.
"""

import base64
import hashlib
import hmac
import json

import pytest

from app.core.config import INSECURE_SECRET_PLACEHOLDER, Settings, settings
from app.core.errors import ValidationError
from app.core.rate_limit import client_key
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    validate_password_strength,
)
from tests.conftest import API, auth, register

# --- Finding 2: production config guardrails -------------------------------


def test_production_refuses_the_placeholder_secret():
    with pytest.raises(ValueError, match="placeholder"):
        Settings(
            environment="production",
            jwt_secret_key=INSECURE_SECRET_PLACEHOLDER,
            _env_file=None,
        )


def test_production_refuses_a_short_secret():
    with pytest.raises(ValueError, match="at least 32"):
        Settings(environment="production", jwt_secret_key="tooshort", _env_file=None)


def test_production_refuses_debug_and_wildcard_cors():
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(
            environment="production",
            jwt_secret_key="k" * 40,
            debug=True,
            _env_file=None,
        )
    with pytest.raises(ValueError, match="CORS"):
        Settings(
            environment="production",
            jwt_secret_key="k" * 40,
            cors_origins="*",
            _env_file=None,
        )


def test_local_environment_is_left_alone():
    """The guard must not make local development painful."""
    local = Settings(environment="local", jwt_secret_key="change-me", _env_file=None)
    assert local.jwt_secret_key == "change-me"


def test_docs_are_hidden_in_production_by_default():
    # `debug=False` is passed explicitly: `_env_file=None` ignores the .env
    # file but not the process environment, and the container exports
    # DEBUG=true - which the production guard rightly refuses to start with.
    assert Settings(environment="local", _env_file=None).docs_enabled is True
    prod = Settings(
        environment="production", jwt_secret_key="k" * 40, debug=False, _env_file=None
    )
    assert prod.docs_enabled is False
    # ...but can be re-enabled deliberately.
    override = Settings(
        environment="production",
        jwt_secret_key="k" * 40,
        debug=False,
        expose_docs=True,
        _env_file=None,
    )
    assert override.docs_enabled is True


# --- Finding 1: rate-limit key is proxy-aware ------------------------------


class _FakeRequest:
    def __init__(self, headers: dict[str, str], client_host: str = "10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": client_host})()


def test_rate_limit_key_prefers_the_real_client_behind_a_proxy(monkeypatch):
    """Behind a proxy every request has the proxy's address as its peer.

    Keying on that would put every user in one bucket - throttling innocent
    traffic while letting an attacker hide inside it.
    """
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = _FakeRequest({"X-Forwarded-For": "203.0.113.9, 70.41.3.18"})
    assert client_key(request) == "203.0.113.9"


def test_rate_limit_key_ignores_a_forwarded_header_when_not_behind_a_proxy():
    """The header is caller-supplied, so it is only believed when configured.

    Trusting it unconditionally would hand anyone an unlimited number of login
    attempts: rotate X-Forwarded-For and every request lands in a fresh bucket.
    Outside production nothing rewrites the header, so it is ignored.
    """
    assert settings.trust_proxy is False
    request = _FakeRequest({"X-Forwarded-For": "203.0.113.9"}, client_host="198.51.100.7")
    assert client_key(request) == "198.51.100.7"


def test_rate_limit_key_falls_back_to_the_socket_peer(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert client_key(_FakeRequest({}, client_host="198.51.100.7")) == "198.51.100.7"


def test_proxy_headers_are_trusted_in_production_by_default():
    """A deployment behind a TLS-terminating proxy still gets per-client keys."""
    prod = Settings(
        environment="production", jwt_secret_key="k" * 40, debug=False, _env_file=None
    )
    assert prod.trust_proxy is True
    assert Settings(environment="local", _env_file=None).trust_proxy is False


async def test_repeated_failed_logins_are_eventually_throttled(client):
    """Runs with the limiter actually switched on.

    The rest of the suite disables it (it would throttle itself), which once
    hid a crash that only occurred on the enabled path - so this test exists
    to exercise that path end to end, not just the key function.
    """
    from app.core.rate_limit import limiter

    limiter.enabled = True
    limiter.reset()
    try:
        codes = []
        for _ in range(30):
            response = await client.post(
                f"{API}/auth/login",
                json={"email": "nobody@test.com", "password": "WrongPassword!1"},
            )
            codes.append(response.status_code)
            if response.status_code == 429:
                break

        assert 429 in codes, f"brute force was never throttled: {sorted(set(codes))}"
        assert codes.count(401) <= 20, "throttling kicked in later than configured"
    finally:
        limiter.enabled = False
        limiter.reset()


async def test_rate_limit_headers_are_returned(client):
    """headers_enabled requires the endpoint to declare `response: Response`.

    Without it slowapi raises and every auth request 500s - which is exactly
    what happened once the limiter was first switched on.
    """
    from app.core.rate_limit import limiter

    account = await register(client, "CLIENT", "ratelimit-headers@test.com")

    limiter.enabled = True
    limiter.reset()
    try:
        # A *successful* request: on an error the response is built by our
        # exception handler, which does not carry the injected headers.
        ok = await client.post(
            f"{API}/auth/login",
            json={"email": account["user"]["email"], "password": "DemoPass!2026"},
        )
        assert ok.status_code == 200, "must not be a 500 from header injection"
        assert any(h.lower().startswith("x-ratelimit") for h in ok.headers)

        # And the failure path must still not 500.
        bad = await client.post(
            f"{API}/auth/login",
            json={"email": "nobody@test.com", "password": "WrongPassword!1"},
        )
        assert bad.status_code == 401
    finally:
        limiter.enabled = False
        limiter.reset()


# --- Finding 4: password policy --------------------------------------------


@pytest.mark.parametrize(
    "password",
    ["password", "12345678", "aaaaaaaa", "password123", "Password123", "aaaaaaaaaaaa"],
)
def test_weak_passwords_are_rejected(password):
    with pytest.raises(ValidationError):
        validate_password_strength(password)


def test_a_reasonable_password_is_accepted():
    assert validate_password_strength("DemoPass!2026")


async def test_registration_rejects_a_weak_password(client):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "weak@test.com",
            "password": "password123",
            "full_name": "Weak Password",
            "role": "CLIENT",
        },
    )
    assert response.status_code == 422
    assert "password" in response.text.lower()


async def test_short_password_rejected_at_the_schema(client):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "email": "short@test.com",
            "password": "a" * (MIN_PASSWORD_LENGTH - 1),
            "full_name": "Short Password",
            "role": "CLIENT",
        },
    )
    assert response.status_code == 422


# --- Finding 5: oversized bodies -------------------------------------------


async def test_oversized_login_password_is_rejected(client):
    """Argon2 must never be handed a multi-megabyte 'password'."""
    response = await client.post(
        f"{API}/auth/login", json={"email": "a@b.com", "password": "A" * 200_000}
    )
    assert response.status_code in {413, 422}


# --- Finding 8: security headers -------------------------------------------


async def test_security_headers_are_present(client):
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


# --- Finding 10: logout revokes access tokens ------------------------------


async def test_access_token_stops_working_after_logout(client):
    """The access token must die with its session, not linger until expiry."""
    payload = await register(client, "CLIENT", "revoke@test.com")
    headers = auth(payload["access_token"])

    assert (await client.get(f"{API}/users/me", headers=headers)).status_code == 200

    await client.post(f"{API}/auth/logout", json={"refresh_token": payload["refresh_token"]})

    after = await client.get(f"{API}/users/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["code"] == "session_revoked"


async def test_refresh_rotation_invalidates_the_previous_access_token(client):
    payload = await register(client, "CLIENT", "rotate@test.com")
    old_headers = auth(payload["access_token"])

    refreshed = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": payload["refresh_token"]}
    )
    assert refreshed.status_code == 200

    assert (await client.get(f"{API}/users/me", headers=old_headers)).status_code == 401
    new_headers = auth(refreshed.json()["access_token"])
    assert (await client.get(f"{API}/users/me", headers=new_headers)).status_code == 200


# --- Token forgery ---------------------------------------------------------


def _unsigned_token(payload: dict) -> str:
    def seg(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'none', 'typ': 'JWT'})}.{seg(payload)}."


async def test_alg_none_token_is_rejected(client):
    token = _unsigned_token(
        {"sub": "1", "role": "ADMIN", "sid": 1, "type": "access", "exp": 9_999_999_999}
    )
    response = await client.get(f"{API}/users/me", headers=auth(token))
    assert response.status_code == 401


async def test_token_signed_with_the_placeholder_secret_is_rejected(client):
    def seg(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    header = seg({"alg": "HS256", "typ": "JWT"})
    body = seg({"sub": "1", "role": "ADMIN", "sid": 1, "type": "access", "exp": 9_999_999_999})
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                INSECURE_SECRET_PLACEHOLDER.encode(),
                f"{header}.{body}".encode(),
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    response = await client.get(f"{API}/users/me", headers=auth(f"{header}.{body}.{signature}"))
    assert response.status_code == 401


async def test_refresh_token_cannot_be_used_as_an_access_token(client):
    payload = await register(client, "CLIENT", "wrongtype@test.com")
    response = await client.get(f"{API}/users/me", headers=auth(payload["refresh_token"]))
    assert response.status_code == 401


# --- Finding 11: vendor verification is admin-only -------------------------


async def test_vendor_cannot_verify_themselves(client, session_factory):
    from tests.conftest import make_admin

    vendor = await register(client, "VENDOR", "selfverify@test.com")
    vendor_id = vendor["user"]["id"]

    denied = await client.patch(
        f"{API}/users/{vendor_id}/verification",
        json={"is_verified": True},
        headers=auth(vendor["access_token"]),
    )
    assert denied.status_code == 403

    admin = await register(client, "CLIENT", "verifier@test.com")
    await make_admin(session_factory, admin["user"]["id"])
    relogin = await client.post(
        f"{API}/auth/login",
        json={"email": "verifier@test.com", "password": "DemoPass!2026"},
    )
    allowed = await client.patch(
        f"{API}/users/{vendor_id}/verification",
        json={"is_verified": True},
        headers=auth(relogin.json()["access_token"]),
    )
    assert allowed.status_code == 200
    assert allowed.json()["is_verified"] is True


# --- Mass assignment -------------------------------------------------------


async def test_role_cannot_be_escalated_through_profile_update(client):
    account = await register(client, "CLIENT", "escalate@test.com")
    headers = auth(account["access_token"])

    await client.patch(
        f"{API}/users/me",
        json={"full_name": "Still A Client", "role": "ADMIN", "is_active": True, "id": 9999},
        headers=headers,
    )

    me = (await client.get(f"{API}/users/me", headers=headers)).json()
    assert me["role"] == "CLIENT"
    assert me["id"] == account["user"]["id"]
