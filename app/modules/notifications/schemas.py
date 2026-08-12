from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.enums import NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: NotificationType
    message: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime
