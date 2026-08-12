from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.modules.media import service
from app.modules.media.schemas import UploadUrlRequest, UploadUrlResponse

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload-url", response_model=UploadUrlResponse)
async def create_upload_url(payload: UploadUrlRequest, user: CurrentUser) -> UploadUrlResponse:
    """Get a short-lived presigned URL, then PUT the file straight to R2.

    The returned `object_key` is what you send to the endpoint that attaches the
    image (for example `POST /products/{id}/images`).
    """
    return service.create_upload_url(user, payload)
