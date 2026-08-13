from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.deps import DbDep
from app.core.rate_limit import limiter
from app.modules.auth import service
from app.modules.auth.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
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


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit(settings.rate_limit_forgot_password)
async def forgot_password(
    request: Request, response: Response, payload: ForgotPasswordRequest, db: DbDep
) -> ForgotPasswordResponse:
    """Start a password reset.

    Answers identically whether or not the address is registered. Confirming
    it would make this a free account-enumeration endpoint - the same reason
    login returns one message for a bad email and a bad password.

    Rate limited hard: without a limit the uniform response is still an oracle,
    because an attacker can grind addresses and read the *timing* or simply
    exhaust the mail budget.
    """
    token = await service.request_password_reset(db, payload.email)
    return ForgotPasswordResponse(
        detail=(
            "If that email address has an account, a password reset link is on its way."
        ),
        reset_token=token,
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.rate_limit_reset_password)
async def reset_password(
    request: Request, response: Response, payload: ResetPasswordRequest, db: DbDep
) -> None:
    """Consume a reset token and set a new password.

    Every existing session is revoked as a side effect - see the service.
    """
    await service.reset_password(db, payload.token, payload.password)
