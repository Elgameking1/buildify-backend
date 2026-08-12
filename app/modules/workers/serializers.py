"""ORM -> schema conversion for worker profiles."""

from app.modules.media.r2 import public_url
from app.modules.users.schemas import UserPublic
from app.modules.workers.models import WorkerProfile
from app.modules.workers.schemas import (
    SkillRead,
    WorkerRead,
    WorkerSkillRead,
    WorkerSummary,
)


def serialise_worker(profile: WorkerProfile) -> WorkerRead:
    return WorkerRead(
        user=UserPublic.model_validate(profile.user, from_attributes=True),
        headline=profile.headline,
        bio=profile.bio,
        years_experience=profile.years_experience,
        base_rate=profile.base_rate,
        availability_status=profile.availability_status,
        region=profile.region,
        city=profile.city,
        avg_rating=profile.avg_rating,
        rating_count=profile.rating_count,
        skills=[
            WorkerSkillRead(
                skill=SkillRead.model_validate(link.skill, from_attributes=True),
                proficiency=link.proficiency,
            )
            for link in profile.skills
        ],
        portfolio_urls=[
            url for url in (public_url(key) for key in profile.portfolio_keys or []) if url
        ],
    )


def serialise_worker_summary(profile: WorkerProfile) -> WorkerSummary:
    keys = profile.portfolio_keys or []
    return WorkerSummary(
        user_id=profile.user_id,
        full_name=profile.user.full_name,
        headline=profile.headline,
        region=profile.region,
        city=profile.city,
        availability_status=profile.availability_status,
        years_experience=profile.years_experience,
        base_rate=profile.base_rate,
        avg_rating=profile.avg_rating,
        rating_count=profile.rating_count,
        skills=[link.skill.name for link in profile.skills],
        photo_url=public_url(keys[0]) if keys else None,
    )
