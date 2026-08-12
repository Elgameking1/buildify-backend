"""Reviews, and the rating aggregation they drive."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import JobStatus, NotificationType
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.modules.jobs.models import JobRequest
from app.modules.notifications import service as notifications
from app.modules.reviews.models import Review
from app.modules.reviews.schemas import ReviewCreate, WorkerRatingSummary
from app.modules.users.models import User
from app.modules.workers import service as workers_service


async def create_review(
    db: AsyncSession, client: User, job_id: int, payload: ReviewCreate
) -> Review:
    """Anchor a review to a completed job.

    Three guards, all necessary for the rating to mean anything:
      1. only the client who commissioned the job may review it;
      2. the job must have reached COMPLETED - no reviewing work not done;
      3. one review per job, enforced by a unique index as well as this check.
    """
    job = await db.get(JobRequest, job_id)
    if job is None:
        raise NotFoundError("Job request not found.")

    if job.client_id != client.id:
        raise PermissionDeniedError("Only the client who hired the worker may review this job.")

    if job.status != JobStatus.COMPLETED:
        raise ConflictError(
            f"Only completed jobs can be reviewed - this one is {job.status.value.lower()}.",
            code="job_not_completed",
        )

    existing = await db.execute(select(Review.id).where(Review.job_id == job_id))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("This job has already been reviewed.", code="already_reviewed")

    review = Review(
        job_id=job.id,
        client_id=client.id,
        worker_id=job.worker_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(review)
    await db.flush()

    # Same transaction as the insert: the profile can never disagree with the
    # reviews behind it, not even briefly.
    await workers_service.recalculate_rating(db, job.worker_id)

    await notifications.notify(
        db,
        user_id=job.worker_id,
        type=NotificationType.REVIEW_RECEIVED,
        message=f"{client.full_name} rated your work {payload.rating}/5.",
        payload={"job_id": job.id, "review_id": review.id, "rating": payload.rating},
    )

    await db.flush()
    return await get_review(db, review.id)


async def get_review(db: AsyncSession, review_id: int) -> Review:
    stmt = (
        select(Review)
        .where(Review.id == review_id)
        .options(selectinload(Review.client), selectinload(Review.job))
        .execution_options(populate_existing=True)
    )
    review = (await db.execute(stmt)).scalar_one_or_none()
    if review is None:
        raise NotFoundError("Review not found.")
    return review


async def list_worker_reviews(
    db: AsyncSession, worker_id: int, params: PageParams
) -> tuple[list[Review], int]:
    stmt = (
        select(Review)
        .where(Review.worker_id == worker_id)
        .options(selectinload(Review.client), selectinload(Review.job))
        .order_by(Review.created_at.desc())
    )
    count_stmt = select(func.count()).select_from(Review).where(Review.worker_id == worker_id)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().unique().all()), total


async def rating_summary(db: AsyncSession, worker_id: int) -> WorkerRatingSummary:
    stmt = (
        select(Review.rating, func.count(Review.id))
        .where(Review.worker_id == worker_id)
        .group_by(Review.rating)
    )
    rows = (await db.execute(stmt)).all()

    distribution = {star: 0 for star in range(1, 6)}
    for star, count in rows:
        distribution[int(star)] = int(count)

    total = sum(distribution.values())
    weighted = sum(star * count for star, count in distribution.items())

    return WorkerRatingSummary(
        worker_id=worker_id,
        avg_rating=round(weighted / total, 2) if total else 0.0,
        rating_count=total,
        distribution=distribution,
    )


async def reviewed_job_ids(db: AsyncSession, job_ids: Iterable[int]) -> set[int]:
    """Which of these jobs already carry a review.

    Answers the client's "can I still leave a review?" in one query rather than
    one per job.
    """
    ids = list(job_ids)
    if not ids:
        return set()
    stmt = select(Review.job_id).where(Review.job_id.in_(ids))
    return set((await db.execute(stmt)).scalars().all())
