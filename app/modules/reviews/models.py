"""Ratings and reviews for skilled workers."""

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.jobs.models import JobRequest
    from app.modules.users.models import User


class Review(PKMixin, TimestampMixin, Base):
    """One review per completed job.

    The unique constraint on `job_id` is the guard that makes the rating system
    meaningful: a client cannot review a worker repeatedly to inflate or sink
    their score, because a review must be anchored to a job that actually
    completed.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating_range"),
        UniqueConstraint("job_id", name="uq_review_job"),
        Index("ix_reviews_worker", "worker_id"),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("job_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    worker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["JobRequest"] = relationship()
    client: Mapped["User"] = relationship(foreign_keys=[client_id], lazy="selectin")
    worker: Mapped["User"] = relationship(foreign_keys=[worker_id])
