from fastapi import APIRouter, status

from app.core.deps import CurrentClient, DbDep
from app.core.pagination import Page, PageParamsDep
from app.modules.reviews import service
from app.modules.reviews.models import Review
from app.modules.reviews.schemas import ReviewCreate, ReviewRead, WorkerRatingSummary
from app.modules.users.schemas import UserPublic

router = APIRouter(tags=["reviews"])


def _serialise(review: Review) -> ReviewRead:
    return ReviewRead(
        id=review.id,
        job_id=review.job_id,
        job_title=review.job.title,
        worker_id=review.worker_id,
        client=UserPublic.model_validate(review.client, from_attributes=True),
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
    )


@router.post(
    "/jobs/{job_id}/review", response_model=ReviewRead, status_code=status.HTTP_201_CREATED
)
async def create_review(
    job_id: int, payload: ReviewCreate, db: DbDep, client: CurrentClient
) -> ReviewRead:
    """Rate a worker. Allowed once, by the hiring client, on a completed job."""
    return _serialise(await service.create_review(db, client, job_id, payload))


@router.get("/workers/{worker_id}/reviews", response_model=Page[ReviewRead])
async def list_worker_reviews(
    worker_id: int, db: DbDep, params: PageParamsDep
) -> Page[ReviewRead]:
    reviews, total = await service.list_worker_reviews(db, worker_id, params)
    return Page.build([_serialise(r) for r in reviews], total, params)


@router.get("/workers/{worker_id}/rating", response_model=WorkerRatingSummary)
async def read_rating_summary(worker_id: int, db: DbDep) -> WorkerRatingSummary:
    """Average plus the star distribution, for the profile page."""
    return await service.rating_summary(db, worker_id)
