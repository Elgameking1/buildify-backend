"""Password hashing, password policy, and JWT issuing/verification.

Argon2id is used for passwords (the current password-hashing competition
winner).  Access tokens are short-lived and carry the id of the session that
issued them, so logging out revokes them immediately rather than leaving a
window until expiry.  Refresh tokens are long-lived, opaque, and stored
*hashed* so a database leak yields no usable sessions.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.errors import AuthenticationError, ValidationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

# Not a substitute for a breach corpus, but it blocks what people actually
# type when a form says "at least 10 characters".  Compared lower-cased, so
# "Password123" is caught by "password123".
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password", "password1", "password12", "password123", "password1234",
        "passw0rd", "p@ssword", "p@ssw0rd", "passsword", "password!",
        "12345678", "123456789", "1234567890", "0123456789", "qwertyuiop",
        "qwerty123", "1q2w3e4r5t", "abc123456", "iloveyou1", "letmein123",
        "welcome123", "admin12345", "administrator", "changeme123", "secret123",
        "football123", "sunshine12", "princess12", "monkey12345", "trustno1234",
        "marketplace", "construction", "ghana12345", "buildify123",
    }
)


# --- Password policy -------------------------------------------------------


def validate_password_strength(password: str) -> str:
    """Raise ValidationError when a password is trivially guessable.

    Deliberately modest: length plus a common-password check catches the bulk
    of real-world weak choices without the complexity rules that only push
    people towards "Password1!".
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            code="password_too_short",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters.",
            code="password_too_long",
        )

    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        raise ValidationError(
            "That password is too common. Choose something less predictable.",
            code="password_too_common",
        )
    if len(set(lowered)) < 4:
        raise ValidationError(
            "Password must use at least four different characters.",
            code="password_not_varied",
        )
    return password


# --- Passwords -------------------------------------------------------------


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --- Access tokens ---------------------------------------------------------


def create_access_token(*, user_id: int, role: str, session_id: int) -> str:
    """Mint an access token bound to a session.

    `sid` is the id of the refresh-token row that issued this access token.
    Revoking that row (logout) invalidates this access token too, which is what
    makes logout take effect immediately instead of at expiry.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "sid": session_id,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        # Algorithms pinned explicitly: passing the configured list rather than
        # trusting the token header is what blocks `alg: none` forgeries.
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Access token is invalid.", code="token_invalid") from exc

    if payload.get("type") != "access":
        raise AuthenticationError("Wrong token type supplied.", code="token_invalid")
    return payload


# --- Refresh tokens --------------------------------------------------------


def create_refresh_token() -> tuple[str, str, datetime]:
    """Return `(raw_token, token_hash, expires_at)`.

    The raw token goes to the client exactly once; only its SHA-256 digest is
    persisted, so a leaked database dump does not yield usable sessions.
    """
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    return raw_token, hash_refresh_token(raw_token), expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --- Password reset tokens -------------------------------------------------


def create_password_reset_token() -> tuple[str, str, datetime]:
    """Return `(raw_token, token_hash, expires_at)`.

    Short-lived by design: a reset link is a bearer credential for the account,
    and it travels through email, which is not a confidential channel.
    """
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.password_reset_expire_minutes
    )
    return raw_token, hash_password_reset_token(raw_token), expires_at


def hash_password_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
