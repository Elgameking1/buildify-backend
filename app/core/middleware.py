"""Transport-level protections applied to every response.

Kept separate from `logging.py` so the request-id/access-log concern and the
security concern do not tangle.
"""

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive response headers.

    HSTS is only sent over HTTPS - sending it on plaintext local development
    would pin `localhost` to https in the browser and break the demo.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response = await call_next(request)
        headers = response.headers

        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # This is a JSON API: nothing should ever be executed or embedded from
        # a response of ours. The Swagger UI page is the one exception.
        if not request.url.path.startswith(("/docs", "/redoc")):
            headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )

        if request.url.scheme == "https" or (
            settings.trust_proxy
            and request.headers.get("X-Forwarded-Proto") == "https"
        ):
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        return response


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before a handler ever sees them.

    Written as raw ASGI rather than BaseHTTPMiddleware so the stream can be
    cut off mid-flight: a chunked upload with no Content-Length would otherwise
    be buffered in full before any check could run.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = MutableHeaders(scope=scope)
        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._too_large(send)
                    return
            except ValueError:
                pass  # malformed header; the streaming guard below still applies

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._too_large(send)

    async def _too_large(self, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": "Request body is too large.",
                "code": "request_too_large",
            },
        )
        await response(  # type: ignore[call-arg]
            {"type": "http"}, _empty_receive, send
        )


class _BodyTooLarge(Exception):
    """Internal signal; never escapes this module."""


async def _empty_receive() -> Message:  # pragma: no cover - never awaited
    return {"type": "http.disconnect"}
