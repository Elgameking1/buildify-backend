from fastapi import APIRouter

from app.core.deps import CurrentUser, CurrentVendor, DbDep
from app.modules.media.r2 import public_url
from app.modules.users import service
from app.modules.users.models import User, VendorProfile
from app.modules.users.schemas import (
    MeRead,
    UserUpdate,
    VendorProfileRead,
    VendorProfileUpdate,
    VendorVerificationUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])


def _serialise_vendor(profile: VendorProfile) -> VendorProfileRead:
    return VendorProfileRead(
        user_id=profile.user_id,
        business_name=profile.business_name,
        description=profile.description,
        location=profile.location,
        logo_url=public_url(profile.logo_key),
        is_verified=profile.is_verified,
    )


def _serialise_me(user: User) -> MeRead:
    data = MeRead.model_validate(user, from_attributes=True)
    if user.vendor_profile is not None:
        data.vendor_profile = _serialise_vendor(user.vendor_profile)
    return data


@router.get("/me", response_model=MeRead)
async def read_me(db: DbDep, user: CurrentUser) -> MeRead:
    """The signed-in account, plus whichever profile its role implies."""
    return _serialise_me(await service.get_user_with_profiles(db, user.id))


@router.patch("/me", response_model=MeRead)
async def update_me(payload: UserUpdate, db: DbDep, user: CurrentUser) -> MeRead:
    return _serialise_me(await service.update_user(db, user, payload))


@router.get("/me/vendor-profile", response_model=VendorProfileRead)
async def read_my_vendor_profile(db: DbDep, vendor: CurrentVendor) -> VendorProfileRead:
    return _serialise_vendor(await service.get_vendor_profile(db, vendor.id))


@router.patch("/me/vendor-profile", response_model=VendorProfileRead)
async def update_my_vendor_profile(
    payload: VendorProfileUpdate, db: DbDep, vendor: CurrentVendor
) -> VendorProfileRead:
    profile = await service.update_vendor_profile(db, vendor, payload)
    return _serialise_vendor(profile)


@router.patch("/{vendor_id}/verification", response_model=VendorProfileRead)
async def set_vendor_verification(
    vendor_id: int, payload: VendorVerificationUpdate, db: DbDep, admin: CurrentUser
) -> VendorProfileRead:
    """Admin only. Marks a seller as vetted, which the storefront badges."""
    profile = await service.set_vendor_verified(db, admin, vendor_id, payload.is_verified)
    return _serialise_vendor(profile)
