"""Application error hierarchy and the handlers that render it.

Services raise these; they never import FastAPI or build HTTP responses.  The
handlers registered in `main.py` translate them into a single response envelope
so every client sees the same error shape:

    {"detail": "...", "code": "..."}
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base class for every expected, user-facing failure."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    detail: str = "Request could not be processed."

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        if code is not None:
            self.code = code
        super().__init__(self.detail)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    detail = "Resource not found."


class ConflictError(AppError):
    """The request is well-formed but conflicts with current state.

    Used for duplicate emails, insufficient stock, and illegal state
    transitions.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    detail = "Request conflicts with the current state of the resource."


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    detail = "Request failed validation."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    detail = "Authentication credentials are missing or invalid."


class PermissionDeniedError(AppError):
    """The caller is authenticated but not allowed to touch this resource.

    Raised both by role checks and by the per-resource ownership checks inside
    services - a vendor holding a valid token still may not edit another
    vendor's product.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    detail = "You do not have permission to perform this action."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        headers = (
            {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": "http_error"},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": "Request failed validation.",
                "code": "validation_error",
                "errors": [
                    {"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]}
                    for err in exc.errors()
                ],
            },
        )
