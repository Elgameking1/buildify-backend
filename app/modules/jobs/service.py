"""Job requests and their lifecycle.

The transition table below is the whole authorisation and workflow policy for
hiring, in one readable place.  Scattering these rules through the endpoints is
how a system ends up letting a worker mark their own job complete.
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import JobStatus, NotificationType, UserRole
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.db.base import utc_now
from app.modules.jobs.models import JobRequest
from app.modules.jobs.schemas import JobCreate
from app.modules.notifications import service as notifications
from app.modules.users.models import User
from app.modules.workers.models import Skill, WorkerProfile

# from_status -> {to_status: role permitted to make that move}
#
#   Only the worker may accept, decline or start.
#   Only the client may complete or cancel.
_TRANSITIONS: dict[JobStatus, dict[JobStatus, UserRole]] = {
    JobStatus.PENDING: {
        JobStatus.ACCEPTED: UserRole.WORKER,
        JobStatus.DECLINED: UserRole.WORKER,
        JobStatus.CANCELLED: UserRole.CLIENT,
    },
    JobStatus.ACCEPTED: {
        JobStatus.IN_PROGRESS: UserRole.WORKER,
        JobStatus.CANCELLED: UserRole.CLIENT,
    },
    JobStatus.IN_PROGRESS: {
        JobStatus.COMPLETED: UserRole.CLIENT,
    },
    JobStatus.COMPLETED: {},
    JobStatus.DECLINED: {},
    JobStatus.CANCELLED: {},
}

_NOTIFICATION_FOR_STATUS: dict[JobStatus, NotificationType] = {
    JobStatus.ACCEPTED: NotificationType.JOB_ACCEPTED,
    JobStatus.DECLINED: NotificationType.JOB_DECLINED,
    JobStatus.IN_PROGRESS: NotificationType.JOB_IN_PROGRESS,
    JobStatus.COMPLETED: NotificationType.JOB_COMPLETED,
    JobStatus.CANCELLED: NotificationType.JOB_CANCELLED,
}


def _job_query():
    """Always re-read through a real SELECT, never Session.get().

    `Session.get()` returns an identity-mapped object *without* running its
    eager loaders, so `job.client` would still be unloaded and touching it
    while serialising raises MissingGreenlet.  populate_existing additionally
    forces the loaders to overwrite state cached from earlier in the request,
    which is what makes a read-after-write return the new values.
    """
    return (
        select(JobRequest)
        .options(
            selectinload(JobRequest.client),
            selectinload(JobRequest.worker),
            selectinload(JobRequest.skill),
        )
        .execution_options(populate_existing=True)
    )


async def get_job(db: AsyncSession, user: User, job_id: int) -> JobRequest:
    job = (await db.execute(_job_query().where(JobRequest.id == job_id))).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job request not found.")

    involved = user.id in {job.client_id, job.worker_id}
    if not involved and user.role != UserRole.ADMIN:
        # 404, not 403 - a 403 confirms the job id exists. See the same
        # reasoning in orders.service.get_order.
        raise NotFoundError("Job request not found.")
    return job


async def create_job(db: AsyncSession, client: User, payload: JobCreate) -> JobRequest:
    worker = await db.get(User, payload.worker_id)
    if worker is None or worker.role != UserRole.WORKER:
        raise NotFoundError("Worker not found.")
    if not worker.is_active:
        raise ConflictError("That worker is not currently accepting jobs.")
    if worker.id == client.id:
        raise ConflictError("You cannot hire yourself.")

    profile = await db.get(WorkerProfile, worker.id)
    if profile is None:
        raise NotFoundError("That worker has no profile yet.")

    # Validate here rather than letting the foreign key fail: an integrity
    # error would surface as a 500 instead of a clear 404.
    if payload.skill_id is not None and await db.get(Skill, payload.skill_id) is None:
        raise NotFoundError("Skill not found.")

    job = JobRequest(
        client_id=client.id,
        worker_id=worker.id,
        skill_id=payload.skill_id,
        title=payload.title,
        description=payload.description,
        location=payload.location,
        preferred_start_date=payload.preferred_start_date,
        budget=payload.budget,
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.flush()

    await notifications.notify(
        db,
        user_id=worker.id,
        type=NotificationType.JOB_REQUEST_RECEIVED,
        message=f"{client.full_name} sent you a job request: {job.title}.",
        payload={"job_id": job.id},
    )
    await db.flush()
    return await get_job(db, client, job.id)


async def list_jobs(
    db: AsyncSession,
    user: User,
    params: PageParams,
    *,
    status: JobStatus | None = None,
    role: str = "any",
) -> tuple[list[JobRequest], int]:
    """Role-scoped: a client sees what they sent, a worker what they received."""
    if role == "sent":
        scope = JobRequest.client_id == user.id
    elif role == "received":
        scope = JobRequest.worker_id == user.id
    else:
        scope = or_(JobRequest.client_id == user.id, JobRequest.worker_id == user.id)

    stmt = _job_query().where(scope)
    count_stmt = select(func.count()).select_from(JobRequest).where(scope)

    if status is not None:
        stmt = stmt.where(JobRequest.status == status)
        count_stmt = count_stmt.where(JobRequest.status == status)

    stmt = stmt.order_by(JobRequest.created_at.desc())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().unique().all()), total


async def update_status(
    db: AsyncSession, user: User, job_id: int, new_status: JobStatus
) -> JobRequest:
    job = await get_job(db, user, job_id)

    if new_status == job.status:
        return job

    allowed = _TRANSITIONS[job.status]
    if new_status not in allowed:
        options = ", ".join(sorted(s.value for s in allowed)) or "nothing"
        raise ConflictError(
            f"A {job.status.value} job cannot become {new_status.value}. "
            f"Allowed next: {options}.",
            code="invalid_transition",
        )

    # The move is legal for *someone* - now check it is legal for this caller.
    required_role = allowed[new_status]
    actor_is_client = user.id == job.client_id
    actor_is_worker = user.id == job.worker_id
    permitted = (
        (required_role == UserRole.CLIENT and actor_is_client)
        or (required_role == UserRole.WORKER and actor_is_worker)
        or user.role == UserRole.ADMIN
    )
    if not permitted:
        raise PermissionDeniedError(
            f"Only the {required_role.value.lower()} on this job may set it to "
            f"{new_status.value}."
        )

    now = utc_now()
    if new_status in {JobStatus.ACCEPTED, JobStatus.DECLINED}:
        job.responded_at = now
    elif new_status == JobStatus.COMPLETED:
        job.completed_at = now

    job.status = new_status
    await db.flush()

    # Tell the other party, whoever that is.
    recipient_id = job.worker_id if actor_is_client else job.client_id
    await notifications.notify(
        db,
        user_id=recipient_id,
        type=_NOTIFICATION_FOR_STATUS[new_status],
        message=f"Job '{job.title}' is now {new_status.value.lower().replace('_', ' ')}.",
        payload={"job_id": job.id, "status": new_status.value},
    )

    await db.flush()
    return await get_job(db, user, job.id)
