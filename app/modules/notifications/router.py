from fastapi import APIRouter

from app.core.deps import CurrentUser, DbDep
from app.core.pagination import Page, PageParamsDep
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationRead

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationRead])
async def list_notifications(
    db: DbDep, params: PageParamsDep, user: CurrentUser, unread_only: bool = False
) -> Page[NotificationRead]:
    items, total = await service.list_for_user(db, user, params, unread_only=unread_only)
    return Page.build(
        [NotificationRead.model_validate(n, from_attributes=True) for n in items], total, params
    )


@router.get("/unread-count")
async def read_unread_count(db: DbDep, user: CurrentUser) -> dict[str, int]:
    """Cheap enough for the dashboard to poll for its badge."""
    return {"unread": await service.unread_count(db, user)}


@router.patch("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(notification_id: int, db: DbDep, user: CurrentUser) -> NotificationRead:
    notification = await service.mark_read(db, user, notification_id)
    return NotificationRead.model_validate(notification, from_attributes=True)


@router.patch("/read-all")
async def mark_all_read(db: DbDep, user: CurrentUser) -> dict[str, int]:
    return {"updated": await service.mark_all_read(db, user)}
