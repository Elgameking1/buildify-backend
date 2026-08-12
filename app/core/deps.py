"""Reusable FastAPI dependencies: database session, current user, role guards.

`require_role` answers "may this *kind* of account call this endpoint".  It
never answers "does this account own this particular row" - that check lives in
the service layer, because only the service knows what ownership means for its
resource.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.db.base import utc_now
from app.db.session import get_db
from app.modules.users.models import RefreshToken, User

DbDep = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so that a missing header raises our own AuthenticationError
# and therefore renders in the standard {detail, code} envelope.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_current_user(db: DbDep, credentials: BearerDep) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authorization header is missing.")

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Access token is malformed.", code="token_invalid") from exc

    # One query for both checks: the account, and whether the session that
    # issued this token is still live.  Without the session check a stolen
    # access token would keep working for its full lifetime after logout.
    row = (
        await db.execute(
            select(User, RefreshToken)
            .join(RefreshToken, RefreshToken.id == session_id, isouter=True)
            .where(User.id == user_id)
        )
    ).first()

    if row is None:
        raise AuthenticationError("Account no longer exists.", code="user_not_found")

    user, session = row
    if session is None or session.user_id != user.id:
        raise AuthenticationError("Session is no longer valid.", code="session_invalid")
    if session.revoked_at is not None:
        raise AuthenticationError("Session has been logged out.", code="session_revoked")
    if session.expires_at < utc_now():
        raise AuthenticationError("Session has expired.", code="session_expired")

    if not user.is_active:
        raise PermissionDeniedError("This account has been deactivated.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(
    *roles: UserRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Build a dependency that admits only the given roles.

    ADMIN is always admitted - it is the support/marking account.
    """

    allowed = {*roles, UserRole.ADMIN}

    async def _guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            names = ", ".join(sorted(role.value for role in roles))
            raise PermissionDeniedError(f"This action requires one of: {names}.")
        return user

    return _guard


CurrentClient = Annotated[User, Depends(require_role(UserRole.CLIENT))]
CurrentVendor = Annotated[User, Depends(require_role(UserRole.VENDOR))]
CurrentWorker = Annotated[User, Depends(require_role(UserRole.WORKER))]
