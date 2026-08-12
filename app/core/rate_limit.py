"""Rate limiting for the authentication endpoints.

Two things matter here and both were wrong in the first cut:

1. The limiter is enabled in *every* environment, not only production.  A
   control that first runs on the day it is needed is not a control.
2. The client key is taken from `X-Forwarded-For` when behind a proxy.  Every
   supported deploy target terminates TLS at a proxy, so `get_remote_address`
   would return the proxy's address for every request - putting all users in a
   single bucket, which both throttles innocent traffic and lets an attacker
   hide in it.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    `X-Forwarded-For` is a client-supplied header and trivially spoofed, so it
    is only consulted when `trust_proxy` says a trusted proxy sits in front and
    rewrites it.  The left-most entry is the original client.  Off outside
    production, where nothing rewrites the header: trusting it there would let
    a caller rotate the header and get an unlimited number of login attempts.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=client_key,
    enabled=settings.rate_limit_enabled,
    headers_enabled=True,
)
