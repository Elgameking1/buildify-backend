"""URL slug generation.

Slugs give the React app clean product and category URLs.  Uniqueness is
enforced by the database, so generation only has to make a *likely* unique
candidate and let the caller retry.
"""

import re
import secrets
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = 180) -> str:
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_ALNUM.sub("-", ascii_only).strip("-")
    return slug[:max_length] or "item"


def unique_slug(value: str, *, max_length: int = 180) -> str:
    """Slug with a short random suffix.

    Two vendors will both list "Dangote Cement 50kg"; the suffix keeps the
    unique index happy without an extra round trip to check availability.
    """
    base = slugify(value, max_length=max_length - 7)
    return f"{base}-{secrets.token_hex(3)}"
