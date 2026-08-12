"""Creating and reading in-app notifications.

`notify` is called from other modules' services inside their existing
transaction, so a notification can never exist for an event that was rolled
back.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationType
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.db.base import utc_now
from app.modules.notifications.models import Notification
from app.modules.users.models import User


async def notify(
    db: AsyncSession,
    *,
    user_id: int,
    type: NotificationType,
    message: str,
    payload: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id, type=type, message=message, payload=payload or {}
    )
    db.add(notification)
    await db.flush()
    return notification


async def list_for_user(
    db: AsyncSession, user: User, params: PageParams, *, unread_only: bool = False
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    count_stmt = select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id
    )

    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
        count_stmt = count_stmt.where(Notification.read_at.is_(None))

    stmt = stmt.order_by(Notification.created_at.desc())
    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().all()), total


async def unread_count(db: AsyncSession, user: User) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return (await db.execute(stmt)).scalar_one()


async def mark_read(db: AsyncSession, user: User, notification_id: int) -> Notification:
    notification = await db.get(Notification, notification_id)
    # Same error for "missing" and "someone else's" - a 403 here would confirm
    # that a given notification id exists.
    if notification is None or notification.user_id != user.id:
        raise NotFoundError("Notification not found.")
    if notification.read_at is None:
        notification.read_at = utc_now()
        await db.flush()
    return notification


async def mark_all_read(db: AsyncSession, user: User) -> int:
    stmt = select(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)
    )
    now = utc_now()
    rows = list((await db.execute(stmt)).scalars().all())
    for notification in rows:
        notification.read_at = now
    await db.flush()
    return len(rows)
