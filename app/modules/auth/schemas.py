from pydantic import BaseModel, EmailStr, Field, model_validator

from app.core.enums import UserRole
from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.modules.users.schemas import UserRead


class RegisterRequest(BaseModel):
    email: EmailStr
    # Length is checked here so the API documents it; the full policy
    # (common-password and variety checks) lives in security.validate_password_strength
    # and runs in the service before anything is hashed or stored.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    full_name: str = Field(min_length=2, max_length=160)
    role: UserRole
    phone: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)

    # Vendor-only, required when role is VENDOR.
    business_name: str | None = Field(default=None, max_length=160)
    # Worker-only, optional.
    headline: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def _check_role_fields(self) -> "RegisterRequest":
        if self.role == UserRole.ADMIN:
            raise ValueError("Admin accounts cannot be self-registered.")
        if self.role == UserRole.VENDOR and not self.business_name:
            raise ValueError("business_name is required when registering as a vendor.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    # Bounded so a multi-megabyte "password" cannot be fed to Argon2, which
    # would burn CPU on an unauthenticated request.
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class AuthResponse(TokenPair):
    user: UserRead


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Deliberately says nothing about whether the account exists.

    `reset_token` is populated only when the deployment is configured to hand
    the link back directly, which is a demo convenience and off by default.
    """

    detail: str
    reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    # Same bounds as registration: long enough to be worth having, capped so a
    # multi-megabyte value cannot be fed to Argon2.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
