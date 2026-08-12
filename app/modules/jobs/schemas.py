from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.enums import JobStatus
from app.modules.users.schemas import UserPublic
from app.modules.workers.schemas import SkillRead


class JobCreate(BaseModel):
    worker_id: int
    skill_id: int | None = None
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=5000)
    location: str = Field(min_length=2, max_length=200)
    preferred_start_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class JobStatusUpdate(BaseModel):
    status: JobStatus


class JobRead(BaseModel):
    id: int
    client: UserPublic
    worker: UserPublic
    skill: SkillRead | None = None
    title: str
    description: str
    location: str
    preferred_start_date: date | None = None
    budget: Decimal | None = None
    status: JobStatus
    created_at: datetime
    responded_at: datetime | None = None
    completed_at: datetime | None = None
    has_review: bool = False
