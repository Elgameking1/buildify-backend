from typing import Literal

from fastapi import APIRouter, status

from app.core.deps import CurrentClient, CurrentUser, DbDep
from app.core.enums import JobStatus
from app.core.pagination import Page, PageParamsDep
from app.modules.jobs import service
from app.modules.jobs.models import JobRequest
from app.modules.jobs.schemas import JobCreate, JobRead, JobStatusUpdate
from app.modules.reviews import service as reviews_service
from app.modules.users.schemas import UserPublic
from app.modules.workers.schemas import SkillRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _serialise(job: JobRequest, *, has_review: bool = False) -> JobRead:
    return JobRead(
        id=job.id,
        client=UserPublic.model_validate(job.client, from_attributes=True),
        worker=UserPublic.model_validate(job.worker, from_attributes=True),
        skill=SkillRead.model_validate(job.skill, from_attributes=True) if job.skill else None,
        title=job.title,
        description=job.description,
        location=job.location,
        preferred_start_date=job.preferred_start_date,
        budget=job.budget,
        status=job.status,
        created_at=job.created_at,
        responded_at=job.responded_at,
        completed_at=job.completed_at,
        has_review=has_review,
    )


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: DbDep, client: CurrentClient) -> JobRead:
    """Send a job request to a specific worker."""
    return _serialise(await service.create_job(db, client, payload))


@router.get("", response_model=Page[JobRead])
async def list_jobs(
    db: DbDep,
    params: PageParamsDep,
    user: CurrentUser,
    role: Literal["any", "sent", "received"] = "any",
    status_filter: JobStatus | None = None,
) -> Page[JobRead]:
    """`sent` for the client dashboard, `received` for the worker dashboard."""
    jobs, total = await service.list_jobs(db, user, params, status=status_filter, role=role)
    # One extra query for the whole page, rather than one per job.
    reviewed = await reviews_service.reviewed_job_ids(db, [job.id for job in jobs])
    return Page.build(
        [_serialise(job, has_review=job.id in reviewed) for job in jobs], total, params
    )


@router.get("/{job_id}", response_model=JobRead)
async def read_job(job_id: int, db: DbDep, user: CurrentUser) -> JobRead:
    job = await service.get_job(db, user, job_id)
    reviewed = await reviews_service.reviewed_job_ids(db, [job.id])
    return _serialise(job, has_review=job.id in reviewed)


@router.patch("/{job_id}/status", response_model=JobRead)
async def update_job_status(
    job_id: int, payload: JobStatusUpdate, db: DbDep, user: CurrentUser
) -> JobRead:
    """Move a job through its lifecycle.

    The transition table decides both whether the move is legal and which party
    is allowed to make it - a worker cannot mark their own job complete, and a
    client cannot accept on the worker's behalf.
    """
    job = await service.update_status(db, user, job_id, payload.status)
    reviewed = await reviews_service.reviewed_job_ids(db, [job.id])
    return _serialise(job, has_review=job.id in reviewed)
