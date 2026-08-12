from pydantic import BaseModel, Field

from app.core.enums import MediaPurpose


class UploadUrlRequest(BaseModel):
    purpose: MediaPurpose
    content_type: str = Field(description="MIME type, e.g. image/jpeg")


class UploadUrlResponse(BaseModel):
    upload_url: str = Field(description="Presigned URL - PUT the raw file bytes here")
    object_key: str = Field(description="Send this back when attaching the image")
    public_url: str | None = Field(default=None, description="Where the file will be readable")
    expires_in: int
    max_bytes: int
    required_headers: dict[str, str] = Field(
        description="Headers the PUT must include for the signature to match"
    )
