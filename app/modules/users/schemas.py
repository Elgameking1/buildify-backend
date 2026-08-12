"""Request/response schemas for accounts and vendor profiles."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import AvailabilityStatus, UserRole


class UserPublic(BaseModel):
    """The safe subset of an account, embeddable anywhere."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    role: UserRole
    region: str | None = None
    city: str | None = None


class UserRead(UserPublic):
    email: EmailStr
    phone: str | None = None
    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)


class VendorProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    business_name: str
    description: str | None = None
    location: str | None = None
    logo_url: str | None = None
    is_verified: bool


class VendorProfileUpdate(BaseModel):
    business_name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = None
    location: str | None = Field(default=None, max_length=160)
    logo_key: str | None = Field(default=None, max_length=512)


class VendorVerificationUpdate(BaseModel):
    is_verified: bool


class WorkerProfileSummary(BaseModel):
    """Scalar-only view of a worker profile.

    Defined here rather than imported from the workers module so that
    `/users/me` does not create an import cycle.  Full worker detail, including
    skills, is served by `/workers/me`.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    headline: str | None = None
    years_experience: int
    base_rate: Decimal | None = None
    availability_status: AvailabilityStatus
    avg_rating: Decimal
    rating_count: int


class MeRead(UserRead):
    """`GET /users/me` - the account plus whichever profile its role implies."""

    vendor_profile: VendorProfileRead | None = None
    worker_profile: WorkerProfileSummary | None = None
