"""Registration, login, token rotation, logout and password reset."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import UserRole
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    hash_password,
    hash_password_reset_token,
    hash_refresh_token,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.db.base import utc_now
from app.modules.auth.schemas import AuthResponse, RegisterRequest
from app.modules.users.models import (
    PasswordResetToken,
    RefreshToken,
    User,
    VendorProfile,
)
from app.modules.users.schemas import UserRead
from app.modules.workers.models import WorkerProfile

logger = logging.getLogger(__name__)


async def _issue_tokens(db: AsyncSession, user: User) -> AuthResponse:
    raw_refresh, token_hash, expires_at = create_refresh_token()

    # The session row is created first so its id can be embedded in the access
    # token as `sid`.  Revoking the row then invalidates both tokens at once.
    session = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        # Stored naive: MySQL DATETIME carries no timezone, and mixing the
        # two is how "expired token" bugs appear only in production.
        expires_at=expires_at.replace(tzinfo=None),
    )
    db.add(session)
    await db.flush()

    access_token = create_access_token(
        user_id=user.id, role=user.role.value, session_id=session.id
    )

    return AuthResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserRead.model_validate(user, from_attributes=True),
    )


async def register(db: AsyncSession, payload: RegisterRequest) -> AuthResponse:
    # Rejected before the database is touched, so a weak password never gets
    # as far as being hashed and stored.
    validate_password_strength(payload.password)

    existing = await db.execute(select(User.id).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        # NOTE: this necessarily confirms an email is registered, i.e. it is an
        # account-enumeration oracle.  Removing it entirely requires email
        # verification, which the proposal excludes; the mitigation is the
        # strict rate limit on this endpoint (see app/core/rate_limit.py).
        raise ConflictError("An account with that email already exists.", code="email_taken")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        region=payload.region,
        city=payload.city,
        is_active=True,
    )
    db.add(user)
    await db.flush()  # assigns user.id

    # The profile row is created with the account, so no endpoint ever has to
    # cope with a vendor or worker that has no profile.
    if payload.role == UserRole.VENDOR:
        db.add(
            VendorProfile(
                user_id=user.id,
                business_name=payload.business_name or payload.full_name,
                location=payload.city or payload.region,
            )
        )
    elif payload.role == UserRole.WORKER:
        db.add(
            WorkerProfile(
                user_id=user.id,
                headline=payload.headline,
                region=payload.region,
                city=payload.city,
                portfolio_keys=[],
            )
        )
    await db.flush()

    return await _issue_tokens(db, user)


async def login(db: AsyncSession, email: str, password: str) -> AuthResponse:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # Identical error for "no such user" and "wrong password" - anything else
    # turns the login form into an account-enumeration oracle.
    if user is None or not verify_password(password, user.password_hash):
        raise AuthenticationError("Incorrect email or password.", code="invalid_credentials")
    if not user.is_active:
        raise PermissionDeniedError("This account has been deactivated.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    return await _issue_tokens(db, user)


async def _load_valid_refresh_token(db: AsyncSession, raw_token: str) -> RefreshToken:
    token_hash = hash_refresh_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    token = (await db.execute(stmt)).scalar_one_or_none()

    if token is None or token.revoked_at is not None:
        raise AuthenticationError("Refresh token is invalid.", code="refresh_invalid")
    if token.expires_at < utc_now():
        raise AuthenticationError("Refresh token has expired.", code="refresh_expired")
    return token


async def refresh(db: AsyncSession, raw_token: str) -> AuthResponse:
    """Rotate: the presented token is revoked and a fresh pair is issued.

    Rotation means a stolen refresh token is usable at most once before the
    legitimate client's next refresh invalidates it.
    """
    token = await _load_valid_refresh_token(db, raw_token)

    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is no longer active.", code="user_inactive")

    token.revoked_at = utc_now()
    await db.flush()

    return await _issue_tokens(db, user)


async def logout(db: AsyncSession, raw_token: str) -> None:
    """Revoke one refresh token.

    Deliberately silent when the token is already unknown or revoked - logging
    out twice is not an error worth surfacing to a user.
    """
    token_hash = hash_refresh_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    token = (await db.execute(stmt)).scalar_one_or_none()
    if token is not None and token.revoked_at is None:
        token.revoked_at = utc_now()
        await db.flush()


# --- Password reset --------------------------------------------------------


async def verify_email_for_reset(db: AsyncSession, email: str) -> str:
    """Confirm an account exists for `email` and open a reset window for it.

    This flow was specified as: enter an email, be told whether it is
    registered, then set a new password - no emailed link, no one-time code.
    That means the endpoint confirms whether an address has an account, and
    that anyone who reaches this step can set that account's password. Both
    follow from the design rather than from the implementation, and the
    trade-off is the product owner's call.

    What the implementation still does is bound the damage:

      * a short-lived, single-use token ties step two to a completed step one,
        so the password cannot be set by a request that skipped the lookup;
      * only its SHA-256 digest is stored, so the window is not readable from
        a database dump;
      * any earlier unused token is invalidated, leaving exactly one open
        window per account;
      * completing the reset revokes every existing session (see below).

    Raises NotFoundError when no active account matches, which is what the UI
    needs in order to say so.
    """
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user is None or not user.is_active:
        raise NotFoundError("No account was found for that email address.")

    now = utc_now()
    outstanding = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    )
    for token in outstanding.scalars().all():
        token.used_at = now

    raw_token, token_hash, expires_at = create_password_reset_token()
    db.add(
        PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    )
    await db.flush()

    logger.warning("Password reset window opened for user %s", user.id)
    return raw_token


async def reset_password(db: AsyncSession, raw_token: str, new_password: str) -> None:
    """Consume a reset token and set a new password.

    Every existing session is revoked as part of this. A password reset is the
    action someone takes when they believe their account is compromised, and
    leaving the attacker's refresh token alive would make it ceremonial.
    """
    token_hash = hash_password_reset_token(raw_token)
    token = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()

    now = utc_now()
    # One message for expired, already-used and never-existed. Distinguishing
    # them tells a holder of a stale link whether the account exists.
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise AuthenticationError(
            "This reset link is invalid or has expired.", code="reset_invalid"
        )

    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError(
            "This reset link is invalid or has expired.", code="reset_invalid"
        )

    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    token.used_at = now

    revoked = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    for session in revoked.scalars().all():
        session.revoked_at = now

    await db.flush()
