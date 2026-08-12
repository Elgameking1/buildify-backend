"""Cloudflare R2 access, over the S3-compatible API.

Uploads never pass through this API.  The client asks for a presigned PUT URL,
sends the bytes straight to R2, and hands back only the object key.  That keeps
the app container small and avoids paying for ingress twice.
"""

import logging
import uuid
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.enums import MediaPurpose
from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

# Only real image types.  An open allowlist here is how a marketplace ends up
# hosting somebody else's malware.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@lru_cache
def get_client():
    """Cached boto3 S3 client pointed at the R2 endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        region_name="auto",
    )


def is_configured() -> bool:
    return bool(settings.r2_account_id and settings.r2_access_key_id)


def build_object_key(purpose: MediaPurpose, user_id: int, content_type: str) -> str:
    """`{purpose}/{user_id}/{uuid}.{ext}`.

    The user id in the prefix is what later lets us prove that a submitted key
    belongs to the caller.
    """
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        raise ValidationError(f"Unsupported image type. Allowed types: {allowed}.")
    return f"{purpose.value}/{user_id}/{uuid.uuid4().hex}.{extension}"


def owns_key(object_key: str, user_id: int) -> bool:
    """True when the key sits under this user's own prefix."""
    parts = object_key.split("/")
    return len(parts) >= 3 and parts[1] == str(user_id)


def create_presigned_put(object_key: str, content_type: str) -> str:
    """Presigning is local signature maths - no network call, safe to await."""
    return get_client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.r2_bucket,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.r2_presign_expire_seconds,
    )


async def object_size(object_key: str) -> int | None:
    """Size of the uploaded object, or None if it does not exist.

    HEAD serves two purposes: it proves the key was actually uploaded before we
    store a reference to it, and it yields the size so an oversized upload can
    be rejected and deleted.  A presigned PUT cannot itself cap the body size -
    only bucket, key and content-type are signed - so this is where the
    `max_upload_bytes` limit is actually enforced.
    """

    def _head() -> int | None:
        try:
            response = get_client().head_object(Bucket=settings.r2_bucket, Key=object_key)
            return int(response.get("ContentLength", 0))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "403"}:
                return None
            raise

    return await run_in_threadpool(_head)


async def object_exists(object_key: str) -> bool:
    return await object_size(object_key) is not None


async def delete_object(object_key: str) -> None:
    def _delete() -> None:
        try:
            get_client().delete_object(Bucket=settings.r2_bucket, Key=object_key)
        except ClientError:
            # A failed cleanup must never fail the user's request; the object
            # is orphaned at worst.
            logger.warning("Could not delete R2 object %s", object_key, exc_info=True)

    await run_in_threadpool(_delete)


def public_url(object_key: str | None) -> str | None:
    """Build the CDN URL at serialisation time.

    Only keys are stored, so switching from an r2.dev subdomain to a custom
    domain is a config change, not a data migration.
    """
    if not object_key:
        return None
    base = settings.r2_public_base_url.rstrip("/")
    if not base:
        return None
    return f"{base}/{object_key}"
