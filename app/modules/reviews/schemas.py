from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.users.schemas import UserPublic


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ReviewRead(BaseModel):
    id: int
    job_id: int
    job_title: str
    worker_id: int
    client: UserPublic
    rating: int
    comment: str | None = None
    created_at: datetime


class WorkerRatingSummary(BaseModel):
    worker_id: int
    avg_rating: float
    rating_count: int
    distribution: dict[int, int] = Field(description="Star value -> number of reviews")
