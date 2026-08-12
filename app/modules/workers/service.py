"""Worker profiles, the skills taxonomy, and worker search."""

from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import AvailabilityStatus, UserRole
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.slugs import slugify
from app.modules.media import service as media_service
from app.modules.users.models import User
from app.modules.workers.models import Skill, WorkerProfile, WorkerSkill
from app.modules.workers.schemas import (
    SkillCreate,
    WorkerPortfolioUpdate,
    WorkerProfileUpdate,
    WorkerSkillsUpdate,
)


def _profile_query():
    # populate_existing is what makes `PUT /workers/me/skills` return the new
    # skill set: without it the already-loaded `skills` collection is reused
    # and the response still shows the skills the worker just replaced.
    return (
        select(WorkerProfile)
        .options(
            selectinload(WorkerProfile.user),
            selectinload(WorkerProfile.skills).selectinload(WorkerSkill.skill),
        )
        .execution_options(populate_existing=True)
    )


# --- Skills ----------------------------------------------------------------


async def list_skills(db: AsyncSession) -> list[Skill]:
    return list((await db.execute(select(Skill).order_by(Skill.name))).scalars().all())


async def get_skill(db: AsyncSession, skill_id: int) -> Skill:
    skill = await db.get(Skill, skill_id)
    if skill is None:
        raise NotFoundError("Skill not found.")
    return skill


async def create_skill(db: AsyncSession, user: User, payload: SkillCreate) -> Skill:
    if user.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only administrators may manage the skills list.")

    slug = slugify(payload.name)
    existing = await db.execute(select(Skill.id).where(Skill.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("That skill already exists.")

    skill = Skill(name=payload.name, slug=slug, description=payload.description)
    db.add(skill)
    await db.flush()
    return skill


# --- Profiles --------------------------------------------------------------


async def get_worker(db: AsyncSession, worker_id: int) -> WorkerProfile:
    stmt = _profile_query().where(WorkerProfile.user_id == worker_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Worker not found.")
    return profile


async def search_workers(
    db: AsyncSession,
    params: PageParams,
    *,
    q: str | None = None,
    skill_id: int | None = None,
    skill_slug: str | None = None,
    region: str | None = None,
    city: str | None = None,
    min_rating: Decimal | None = None,
    availability: AvailabilityStatus | None = None,
    sort: str = "rating",
) -> tuple[list[WorkerProfile], int]:
    """Objective 5: find workers by skill and location."""
    stmt = _profile_query().join(User, WorkerProfile.user_id == User.id)
    count_stmt = (
        select(func.count())
        .select_from(WorkerProfile)
        .join(User, WorkerProfile.user_id == User.id)
    )

    # Deactivated accounts must never appear in search results.
    filters = [User.is_active.is_(True)]

    if skill_id is not None or skill_slug is not None:
        skill_filter = select(WorkerSkill.worker_id).join(
            Skill, WorkerSkill.skill_id == Skill.id
        )
        if skill_id is not None:
            skill_filter = skill_filter.where(WorkerSkill.skill_id == skill_id)
        else:
            skill_filter = skill_filter.where(Skill.slug == skill_slug)
        filters.append(WorkerProfile.user_id.in_(skill_filter))

    if region:
        filters.append(WorkerProfile.region == region)
    if city:
        filters.append(WorkerProfile.city == city)
    if min_rating is not None:
        filters.append(WorkerProfile.avg_rating >= min_rating)
    if availability is not None:
        filters.append(WorkerProfile.availability_status == availability)
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                User.full_name.like(pattern),
                WorkerProfile.headline.like(pattern),
                WorkerProfile.bio.like(pattern),
            )
        )

    stmt = stmt.where(*filters)
    count_stmt = count_stmt.where(*filters)

    orderings = {
        # Rating first, then volume: a single 5-star review should not outrank
        # a worker with twenty.
        "rating": (WorkerProfile.avg_rating.desc(), WorkerProfile.rating_count.desc()),
        "experience": (WorkerProfile.years_experience.desc(),),
        "rate_asc": (WorkerProfile.base_rate.asc(),),
        "newest": (WorkerProfile.created_at.desc(),),
    }
    stmt = stmt.order_by(*orderings.get(sort, orderings["rating"]))

    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().unique().all()), total


async def update_profile(
    db: AsyncSession, worker: User, payload: WorkerProfileUpdate
) -> WorkerProfile:
    profile = await get_worker(db, worker.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.flush()
    return await get_worker(db, worker.id)


async def set_skills(
    db: AsyncSession, worker: User, payload: WorkerSkillsUpdate
) -> WorkerProfile:
    """Replace the worker's skill set wholesale.

    A PUT-style replace keeps the client simple: the profile form sends the
    complete list it wants, and does not have to diff against what is stored.
    """
    profile = await get_worker(db, worker.id)

    requested = {assignment.skill_id: assignment.proficiency for assignment in payload.skills}
    if requested:
        found = await db.execute(select(Skill.id).where(Skill.id.in_(requested)))
        missing = set(requested) - set(found.scalars().all())
        if missing:
            raise NotFoundError(f"Unknown skill ids: {sorted(missing)}.")

    for link in list(profile.skills):
        await db.delete(link)
    await db.flush()

    for skill_id, proficiency in requested.items():
        db.add(WorkerSkill(worker_id=profile.user_id, skill_id=skill_id, proficiency=proficiency))

    await db.flush()
    return await get_worker(db, worker.id)


async def set_portfolio(
    db: AsyncSession, worker: User, payload: WorkerPortfolioUpdate
) -> WorkerProfile:
    profile = await get_worker(db, worker.id)

    # Every key must be one this worker actually uploaded.
    for object_key in payload.object_keys:
        await media_service.verify_owned_key(object_key, worker)

    profile.portfolio_keys = list(payload.object_keys)
    await db.flush()
    return await get_worker(db, worker.id)


async def recalculate_rating(db: AsyncSession, worker_id: int) -> WorkerProfile:
    """Recompute the denormalised rating columns from the review table.

    Called inside the same transaction that writes a review, so the profile can
    never disagree with the reviews behind it.
    """
    from app.modules.reviews.models import Review

    stmt = select(func.avg(Review.rating), func.count(Review.id)).where(
        Review.worker_id == worker_id
    )
    average, count = (await db.execute(stmt)).one()

    profile = await db.get(WorkerProfile, worker_id)
    if profile is None:
        raise NotFoundError("Worker not found.")

    profile.avg_rating = Decimal(average).quantize(Decimal("0.01")) if count else Decimal("0.00")
    profile.rating_count = int(count)
    await db.flush()
    return profile
