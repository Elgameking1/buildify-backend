"""Skilled-worker profiles and the skills taxonomy."""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AvailabilityStatus, SkillProficiency
from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.users.models import User


class Skill(PKMixin, TimestampMixin, Base):
    """Seeded taxonomy: Masonry, Carpentry, Plumbing, Electrical, ..."""

    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(90), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    worker_links: Mapped[list["WorkerSkill"]] = relationship(back_populates="skill")


class WorkerProfile(TimestampMixin, Base):
    __tablename__ = "worker_profiles"
    __table_args__ = (
        # "search for and hire workers based on skill and location"
        Index("ix_worker_profiles_location", "region", "city"),
        Index("ix_worker_profiles_rating", "avg_rating"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    headline: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    years_experience: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    base_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        SAEnum(AvailabilityStatus, native_enum=False, length=16),
        default=AvailabilityStatus.AVAILABLE,
        nullable=False,
        index=True,
    )
    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    portfolio_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Denormalised for cheap sorting on the worker-search page.  Recomputed
    # inside the same transaction that writes a review - never by a job.
    avg_rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, nullable=False)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="worker_profile")
    skills: Mapped[list["WorkerSkill"]] = relationship(
        back_populates="worker", cascade="all, delete-orphan", lazy="selectin"
    )


class WorkerSkill(Base):
    """Join table with a proficiency attribute, so it is a mapped class."""

    __tablename__ = "worker_skills"

    worker_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("worker_profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    skill_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    proficiency: Mapped[SkillProficiency] = mapped_column(
        SAEnum(SkillProficiency, native_enum=False, length=16),
        default=SkillProficiency.INTERMEDIATE,
        nullable=False,
    )

    worker: Mapped["WorkerProfile"] = relationship(back_populates="skills")
    skill: Mapped["Skill"] = relationship(back_populates="worker_links", lazy="selectin")
