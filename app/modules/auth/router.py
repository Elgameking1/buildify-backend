from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.deps import DbDep
from app.core.rate_limit import limiter
from app.modules.auth import service
from app.modules.auth.schemas import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.rate_limit_register)
async def register(
    request: Request, response: Response, payload: RegisterRequest, db: DbDep
) -> AuthResponse:
    """Create an account. The role chosen here also creates its profile row.

    The tight rate limit is deliberate: a duplicate email returns 409, which
    unavoidably confirms that an address is registered. Throttling is what
    stops that being usable to enumerate accounts at any scale.
    """
    return await service.register(db, payload)


@router.post("/login", response_model=AuthResponse)
@limiter.limit(settings.rate_limit_login)
async def login(
    request: Request, response: Response, payload: LoginRequest, db: DbDep
) -> AuthResponse:
    return await service.login(db, payload.email, payload.password)


@router.post("/refresh", response_model=AuthResponse)
@limiter.limit(settings.rate_limit_refresh)
async def refresh(
    request: Request, response: Response, payload: RefreshRequest, db: DbDep
) -> AuthResponse:
    """Exchange a refresh token for a new pair. The old token is revoked."""
    return await service.refresh(db, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: DbDep) -> None:
    await service.logout(db, payload.refresh_token)
