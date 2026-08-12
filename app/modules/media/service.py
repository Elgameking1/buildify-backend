"""Media upload brokering and key verification."""

from app.core.config import settings
from app.core.enums import MediaPurpose, UserRole
from app.core.errors import PermissionDeniedError, ValidationError
from app.modules.media import r2
from app.modules.media.schemas import UploadUrlRequest, UploadUrlResponse
from app.modules.users.models import User

# Which roles may upload which kind of image.
_PURPOSE_ROLES: dict[MediaPurpose, set[UserRole]] = {
    MediaPurpose.PRODUCT: {UserRole.VENDOR, UserRole.ADMIN},
    MediaPurpose.LOGO: {UserRole.VENDOR, UserRole.ADMIN},
    MediaPurpose.PORTFOLIO: {UserRole.WORKER, UserRole.ADMIN},
}


def create_upload_url(user: User, payload: UploadUrlRequest) -> UploadUrlResponse:
    if not r2.is_configured():
        raise ValidationError(
            "Object storage is not configured. Set the R2_* variables in .env.",
            code="storage_unconfigured",
        )

    allowed_roles = _PURPOSE_ROLES[payload.purpose]
    if user.role not in allowed_roles:
        raise PermissionDeniedError(
            f"{user.role.value} accounts may not upload '{payload.purpose.value}' images."
        )

    object_key = r2.build_object_key(payload.purpose, user.id, payload.content_type)
    upload_url = r2.create_presigned_put(object_key, payload.content_type)

    return UploadUrlResponse(
        upload_url=upload_url,
        object_key=object_key,
        public_url=r2.public_url(object_key),
        expires_in=settings.r2_presign_expire_seconds,
        max_bytes=settings.max_upload_bytes,
        required_headers={"Content-Type": payload.content_type},
    )


async def verify_owned_key(object_key: str, user: User) -> None:
    """Guard every place a client-supplied object key is about to be stored.

    Two separate checks, both necessary:
      1. the key is under this user's prefix - otherwise anyone could claim
         another vendor's uploaded image by guessing its key;
      2. the object actually exists in the bucket - otherwise the database
         fills with keys pointing at nothing.
    """
    if not r2.owns_key(object_key, user.id):
        raise PermissionDeniedError("That object key does not belong to your account.")

    size = await r2.object_size(object_key)
    if size is None:
        raise ValidationError(
            "No uploaded file was found for that object key.", code="object_missing"
        )

    # A presigned PUT signs only bucket, key and content-type, so the size cap
    # cannot be applied at upload time; enforce it here and delete the object
    # so an oversized upload cannot squat in the bucket.
    if size > settings.max_upload_bytes:
        await r2.delete_object(object_key)
        limit_mb = settings.max_upload_bytes / (1024 * 1024)
        raise ValidationError(
            f"Uploaded file is too large ({size / (1024 * 1024):.1f} MB); "
            f"the limit is {limit_mb:.0f} MB.",
            code="upload_too_large",
        )
