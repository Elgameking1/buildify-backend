"""Account and vendor-profile operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import UserRole
from app.core.errors import NotFoundError, PermissionDeniedError
from app.modules.media import service as media_service
from app.modules.users.models import User, VendorProfile
from app.modules.users.schemas import UserUpdate, VendorProfileUpdate


async def get_user_with_profiles(db: AsyncSession, user_id: int) -> User:
    stmt = (
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.vendor_profile), selectinload(User.worker_profile))
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("Account not found.")
    return user


async def update_user(db: AsyncSession, user: User, payload: UserUpdate) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.flush()
    return await get_user_with_profiles(db, user.id)


async def get_vendor_profile(db: AsyncSession, vendor_id: int) -> VendorProfile:
    profile = await db.get(VendorProfile, vendor_id)
    if profile is None:
        raise NotFoundError("Vendor profile not found.")
    return profile


async def set_vendor_verified(
    db: AsyncSession, actor: User, vendor_id: int, verified: bool
) -> VendorProfile:
    """Admin-only vetting of a seller.

    Anyone can self-register as a VENDOR, which is a fraud vector in a
    marketplace where payment happens offline. This does not gate publishing -
    that would be a product decision - but it drives the `is_verified` badge
    the storefront shows next to a seller's name.
    """
    if actor.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only administrators may verify vendors.")

    profile = await get_vendor_profile(db, vendor_id)
    profile.is_verified = verified
    await db.flush()
    return profile


async def update_vendor_profile(
    db: AsyncSession, user: User, payload: VendorProfileUpdate
) -> VendorProfile:
    if user.role != UserRole.VENDOR:
        raise PermissionDeniedError("Only vendor accounts have a vendor profile.")

    profile = await get_vendor_profile(db, user.id)
    updates = payload.model_dump(exclude_unset=True)

    # Same guard as product images and worker portfolios: a client-supplied
    # object key is only stored once we know it is this account's own upload.
    if updates.get("logo_key"):
        await media_service.verify_owned_key(updates["logo_key"], user)

    for field, value in updates.items():
        setattr(profile, field, value)
    await db.flush()
    return profile
