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
    ResetPasswordRequest,
    VerifyEmailRequest,
    VerifyEmailResponse,
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


@router.post("/verify-email", response_model=VerifyEmailResponse)
@limiter.limit(settings.rate_limit_forgot_password)
async def verify_email(
    request: Request, response: Response, payload: VerifyEmailRequest, db: DbDep
) -> VerifyEmailResponse:
    """Step one of the reset: confirm the address belongs to an account.

    404 when it does not, which is what lets the form say so. That makes this
    endpoint able to answer "is this person registered here" - accepted as part
    of the specified flow - so the rate limit is the control that stops it
    being usable to sweep a list of addresses.
    """
    token = await service.verify_email_for_reset(db, payload.email)
    return VerifyEmailResponse(
        detail="Account found. You can now set a new password.",
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
