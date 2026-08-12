from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, CurrentWorker, DbDep
from app.core.enums import AvailabilityStatus
from app.core.pagination import Page, PageParamsDep
from app.modules.workers import service
from app.modules.workers.schemas import (
    SkillCreate,
    SkillRead,
    WorkerPortfolioUpdate,
    WorkerProfileUpdate,
    WorkerRead,
    WorkerSkillsUpdate,
    WorkerSummary,
)
from app.modules.workers.serializers import serialise_worker, serialise_worker_summary

router = APIRouter(tags=["workers"])


# --- Skills taxonomy -------------------------------------------------------


@router.get("/skills", response_model=list[SkillRead])
async def list_skills(db: DbDep) -> list[SkillRead]:
    skills = await service.list_skills(db)
    return [SkillRead.model_validate(s, from_attributes=True) for s in skills]


@router.post("/skills", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillCreate, db: DbDep, user: CurrentUser) -> SkillRead:
    """Admin only - workers pick from the list, they do not extend it."""
    skill = await service.create_skill(db, user, payload)
    return SkillRead.model_validate(skill, from_attributes=True)


# --- Worker search ---------------------------------------------------------


@router.get("/workers", response_model=Page[WorkerSummary])
async def search_workers(
    db: DbDep,
    params: PageParamsDep,
    q: Annotated[str | None, Query(description="Name, headline or bio")] = None,
    skill_id: int | None = None,
    skill: Annotated[str | None, Query(description="Skill slug, e.g. plumbing")] = None,
    region: str | None = None,
    city: str | None = None,
    min_rating: Annotated[Decimal | None, Query(ge=0, le=5)] = None,
    availability: AvailabilityStatus | None = None,
    sort: Literal["rating", "experience", "rate_asc", "newest"] = "rating",
) -> Page[WorkerSummary]:
    """Find skilled workers by skill, location and rating."""
    workers, total = await service.search_workers(
        db,
        params,
        q=q,
        skill_id=skill_id,
        skill_slug=skill,
        region=region,
        city=city,
        min_rating=min_rating,
        availability=availability,
        sort=sort,
    )
    return Page.build([serialise_worker_summary(w) for w in workers], total, params)


# --- Own profile (must precede /workers/{worker_id}) -----------------------


@router.get("/workers/me", response_model=WorkerRead)
async def read_my_worker_profile(db: DbDep, worker: CurrentWorker) -> WorkerRead:
    return serialise_worker(await service.get_worker(db, worker.id))


@router.patch("/workers/me", response_model=WorkerRead)
async def update_my_worker_profile(
    payload: WorkerProfileUpdate, db: DbDep, worker: CurrentWorker
) -> WorkerRead:
    return serialise_worker(await service.update_profile(db, worker, payload))


@router.put("/workers/me/skills", response_model=WorkerRead)
async def set_my_skills(
    payload: WorkerSkillsUpdate, db: DbDep, worker: CurrentWorker
) -> WorkerRead:
    """Replaces the whole skill set with the list supplied."""
    return serialise_worker(await service.set_skills(db, worker, payload))


@router.put("/workers/me/portfolio", response_model=WorkerRead)
async def set_my_portfolio(
    payload: WorkerPortfolioUpdate, db: DbDep, worker: CurrentWorker
) -> WorkerRead:
    """Every key is checked against this worker's own R2 prefix before it is stored."""
    return serialise_worker(await service.set_portfolio(db, worker, payload))


@router.get("/workers/{worker_id}", response_model=WorkerRead)
async def read_worker(worker_id: int, db: DbDep) -> WorkerRead:
    return serialise_worker(await service.get_worker(db, worker_id))
