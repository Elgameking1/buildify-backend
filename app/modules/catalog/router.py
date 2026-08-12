from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, CurrentVendor, DbDep
from app.core.pagination import Page, PageParamsDep
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    CategoryCreate,
    CategoryRead,
    CategoryTree,
    ProductCreate,
    ProductImageCreate,
    ProductRead,
    ProductUpdate,
)
from app.modules.catalog.serializers import build_category_tree, serialise_product

router = APIRouter(tags=["catalog"])


# --- Categories ------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryTree])
async def list_categories(db: DbDep) -> list[CategoryTree]:
    """The full category tree - small enough to send in one response."""
    return build_category_tree(await service.list_categories(db))


@router.post(
    "/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED
)
async def create_category(payload: CategoryCreate, db: DbDep, user: CurrentUser) -> CategoryRead:
    """Admin only - the taxonomy is curated, not vendor-supplied."""
    category = await service.create_category(db, user, payload)
    return CategoryRead.model_validate(category, from_attributes=True)


# --- Public product browsing ----------------------------------------------


@router.get("/products", response_model=Page[ProductRead])
async def list_products(
    db: DbDep,
    params: PageParamsDep,
    q: Annotated[str | None, Query(description="Keyword search")] = None,
    category_id: Annotated[int | None, Query(description="Includes sub-categories")] = None,
    vendor_id: int | None = None,
    region: str | None = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    sort: Literal["newest", "price_asc", "price_desc", "name", "relevance"] = "newest",
) -> Page[ProductRead]:
    products, total = await service.list_products(
        db,
        params,
        q=q,
        category_id=category_id,
        vendor_id=vendor_id,
        region=region,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
    )
    return Page.build([serialise_product(p) for p in products], total, params)


@router.get("/products/{product_id}", response_model=ProductRead)
async def read_product(product_id: int, db: DbDep) -> ProductRead:
    return serialise_product(await service.get_product(db, product_id))


# --- Vendor product management --------------------------------------------


@router.get("/vendor/products", response_model=Page[ProductRead])
async def list_my_products(
    db: DbDep, params: PageParamsDep, vendor: CurrentVendor
) -> Page[ProductRead]:
    """The vendor dashboard: includes drafts and archived listings."""
    products, total = await service.list_products(
        db, params, vendor_id=vendor.id, include_inactive=True
    )
    return Page.build([serialise_product(p) for p in products], total, params)


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, db: DbDep, vendor: CurrentVendor) -> ProductRead:
    return serialise_product(await service.create_product(db, vendor, payload))


@router.patch("/products/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: int, payload: ProductUpdate, db: DbDep, vendor: CurrentVendor
) -> ProductRead:
    return serialise_product(await service.update_product(db, vendor, product_id, payload))


@router.delete("/products/{product_id}", response_model=ProductRead)
async def archive_product(product_id: int, db: DbDep, vendor: CurrentVendor) -> ProductRead:
    """Archives rather than deletes, so existing orders keep their references."""
    return serialise_product(await service.archive_product(db, vendor, product_id))


@router.post(
    "/products/{product_id}/images",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_product_image(
    product_id: int, payload: ProductImageCreate, db: DbDep, vendor: CurrentVendor
) -> ProductRead:
    """Attach an image previously uploaded via `POST /media/upload-url`."""
    return serialise_product(await service.add_product_image(db, vendor, product_id, payload))


@router.delete(
    "/products/{product_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product_image(
    product_id: int, image_id: int, db: DbDep, vendor: CurrentVendor
) -> None:
    await service.delete_product_image(db, vendor, product_id, image_id)
