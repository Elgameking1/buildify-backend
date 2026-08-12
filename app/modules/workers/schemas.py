from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AvailabilityStatus, SkillProficiency
from app.modules.users.schemas import UserPublic


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None


class SkillCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = None


class WorkerSkillRead(BaseModel):
    skill: SkillRead
    proficiency: SkillProficiency


class WorkerSkillAssign(BaseModel):
    skill_id: int
    proficiency: SkillProficiency = SkillProficiency.INTERMEDIATE


class WorkerSkillsUpdate(BaseModel):
    skills: list[WorkerSkillAssign] = Field(max_length=20)


class WorkerProfileUpdate(BaseModel):
    headline: str | None = Field(default=None, max_length=160)
    bio: str | None = Field(default=None, max_length=4000)
    years_experience: int | None = Field(default=None, ge=0, le=80)
    base_rate: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    availability_status: AvailabilityStatus | None = None
    region: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)


class WorkerPortfolioUpdate(BaseModel):
    object_keys: list[str] = Field(
        default_factory=list, max_length=12, description="Keys from POST /media/upload-url"
    )


class WorkerSummary(BaseModel):
    """A card in the worker-search results."""

    user_id: int
    full_name: str
    headline: str | None = None
    region: str | None = None
    city: str | None = None
    availability_status: AvailabilityStatus
    years_experience: int
    base_rate: Decimal | None = None
    avg_rating: Decimal
    rating_count: int
    skills: list[str]
    photo_url: str | None = None


class WorkerRead(BaseModel):
    user: UserPublic
    headline: str | None = None
    bio: str | None = None
    years_experience: int
    base_rate: Decimal | None = None
    availability_status: AvailabilityStatus
    region: str | None = None
    city: str | None = None
    avg_rating: Decimal
    rating_count: int
    skills: list[WorkerSkillRead]
    portfolio_urls: list[str]
