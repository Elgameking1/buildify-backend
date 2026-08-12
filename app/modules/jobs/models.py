"""Job requests: a client asking a specific worker to do a specific job."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import JobStatus
from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.users.models import User
    from app.modules.workers.models import Skill


class JobRequest(PKMixin, TimestampMixin, Base):
    __tablename__ = "job_requests"
    __table_args__ = (
        Index("ix_job_requests_worker_status", "worker_id", "status"),
        Index("ix_job_requests_client_status", "client_id", "status"),
    )

    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    preferred_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, length=16),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    client: Mapped["User"] = relationship(foreign_keys=[client_id], lazy="selectin")
    worker: Mapped["User"] = relationship(foreign_keys=[worker_id], lazy="selectin")
    skill: Mapped["Skill | None"] = relationship(lazy="selectin")
