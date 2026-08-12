"""Declarative base and the mixins every table shares."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Naive UTC.

    MySQL DATETIME carries no timezone, so everything is stored naive and
    interpreted as UTC.  Mixing aware and naive values is how "expired token"
    bugs appear only once deployed to a server in a different timezone.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Root of the model hierarchy.  Alembic autogenerate reads its metadata."""


class PKMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    """created_at / updated_at on every table.

    Both carry a *Python-side* default as well as a server default, and the
    Python one is what matters. MySQL has no RETURNING clause, so a
    server-only default leaves the attribute expired after INSERT; reading it
    while serialising the response would then trigger a lazy refresh outside
    the async greenlet context and raise MissingGreenlet. Computing the value
    before the INSERT avoids the refresh entirely. The server defaults remain
    so that rows inserted by raw SQL or a migration are still stamped.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )
