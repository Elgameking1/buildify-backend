"""Category and product operations.

Every mutating function takes the acting `User` and re-checks ownership.  A
valid VENDOR token proves only that the caller is *a* vendor - never that they
own the row they are editing.
"""

import re
from decimal import Decimal

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ProductStatus, UserRole
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.slugs import slugify, unique_slug
from app.modules.catalog.models import Category, Product, ProductImage
from app.modules.catalog.schemas import (
    CategoryCreate,
    ProductCreate,
    ProductImageCreate,
    ProductUpdate,
)
from app.modules.media import service as media_service
from app.modules.users.models import User

# Characters that carry meaning inside a MySQL boolean-mode search.  Passing
# raw user input through would let "+" or "-" silently invert a search.
_BOOLEAN_OPERATORS = re.compile(r"[+\-><()~*\"@]+")
MIN_FULLTEXT_TOKEN = 3  # MySQL innodb_ft_min_token_size default

_MATCH_SQL = "MATCH (products.name, products.description) AGAINST (:search IN BOOLEAN MODE)"


def _fulltext_clauses(expression: str):
    """Return (predicate, ordering) for a boolean-mode full-text search.

    Two separate clauses because a `text()` construct has no `.desc()` - the
    ordering variant has to carry its own DESC.
    """
    predicate = text(_MATCH_SQL).bindparams(search=expression)
    ordering = text(f"{_MATCH_SQL} DESC").bindparams(search=expression)
    return predicate, ordering


def _product_query() -> Select:
    # populate_existing so a re-read after create/update returns the new row
    # rather than the copy already cached in this request's identity map.
    return (
        select(Product)
        .options(
            selectinload(Product.category),
            selectinload(Product.images),
            selectinload(Product.vendor).selectinload(User.vendor_profile),
        )
        .execution_options(populate_existing=True)
    )


# --- Categories ------------------------------------------------------------


async def list_categories(db: AsyncSession) -> list[Category]:
    stmt = select(Category).order_by(Category.name)
    return list((await db.execute(stmt)).scalars().all())


async def get_category(db: AsyncSession, category_id: int) -> Category:
    category = await db.get(Category, category_id)
    if category is None:
        raise NotFoundError("Category not found.")
    return category


async def create_category(db: AsyncSession, user: User, payload: CategoryCreate) -> Category:
    if user.role != UserRole.ADMIN:
        raise PermissionDeniedError("Only administrators may manage categories.")

    slug = slugify(payload.name)
    existing = await db.execute(select(Category.id).where(Category.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A category with that name already exists.")

    if payload.parent_id is not None:
        await get_category(db, payload.parent_id)

    category = Category(
        name=payload.name,
        slug=slug,
        description=payload.description,
        parent_id=payload.parent_id,
    )
    db.add(category)
    await db.flush()
    return category


async def _descendant_category_ids(db: AsyncSession, category_id: int) -> list[int]:
    """Collect a category and its children.

    Browsing "Cement" must also return products filed under "Bagged cement".
    The tree is two or three levels deep at most, so an iterative walk is
    cheaper and clearer than a recursive CTE.
    """
    collected = {category_id}
    frontier = [category_id]
    while frontier:
        stmt = select(Category.id).where(Category.parent_id.in_(frontier))
        children = [row for row in (await db.execute(stmt)).scalars().all()]
        frontier = [child for child in children if child not in collected]
        collected.update(frontier)
    return list(collected)


# --- Products --------------------------------------------------------------


async def get_product(db: AsyncSession, product_id: int) -> Product:
    stmt = _product_query().where(Product.id == product_id)
    product = (await db.execute(stmt)).scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found.")
    return product


async def list_products(
    db: AsyncSession,
    params: PageParams,
    *,
    q: str | None = None,
    category_id: int | None = None,
    vendor_id: int | None = None,
    region: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    include_inactive: bool = False,
    sort: str = "newest",
) -> tuple[list[Product], int]:
    stmt = _product_query()
    count_stmt = select(func.count()).select_from(Product)
    relevance_order = None

    if not include_inactive:
        # Buyers never see drafts or archived listings.
        stmt = stmt.where(Product.status == ProductStatus.ACTIVE)
        count_stmt = count_stmt.where(Product.status == ProductStatus.ACTIVE)

    if q:
        cleaned = _BOOLEAN_OPERATORS.sub(" ", q).strip()
        if len(cleaned) >= MIN_FULLTEXT_TOKEN:
            # Prefix-match each term so "cem" still finds "cement".
            expression = " ".join(f"+{term}*" for term in cleaned.split() if term)
            predicate, relevance_order = _fulltext_clauses(expression)
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        else:
            # Below MySQL's minimum token size full-text returns nothing at
            # all, so fall back to a prefix LIKE rather than an empty page.
            pattern = f"{cleaned}%"
            like = or_(Product.name.like(pattern), Product.description.like(pattern))
            stmt = stmt.where(like)
            count_stmt = count_stmt.where(like)

    if category_id is not None:
        ids = await _descendant_category_ids(db, category_id)
        stmt = stmt.where(Product.category_id.in_(ids))
        count_stmt = count_stmt.where(Product.category_id.in_(ids))

    if vendor_id is not None:
        stmt = stmt.where(Product.vendor_id == vendor_id)
        count_stmt = count_stmt.where(Product.vendor_id == vendor_id)

    if region:
        # Region lives on the vendor's account, so this needs a join.
        vendor_ids = select(User.id).where(User.region == region)
        stmt = stmt.where(Product.vendor_id.in_(vendor_ids))
        count_stmt = count_stmt.where(Product.vendor_id.in_(vendor_ids))

    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
        count_stmt = count_stmt.where(Product.price >= min_price)

    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
        count_stmt = count_stmt.where(Product.price <= max_price)

    orderings = {
        "price_asc": Product.price.asc(),
        "price_desc": Product.price.desc(),
        "name": Product.name.asc(),
        "newest": Product.created_at.desc(),
    }
    if sort == "relevance" and relevance_order is not None:
        stmt = stmt.order_by(relevance_order)
    else:
        stmt = stmt.order_by(orderings.get(sort, Product.created_at.desc()))

    total = (await db.execute(count_stmt)).scalar_one()
    rows = await db.execute(stmt.offset(params.offset).limit(params.limit))
    return list(rows.scalars().unique().all()), total


async def create_product(db: AsyncSession, vendor: User, payload: ProductCreate) -> Product:
    await get_category(db, payload.category_id)

    product = Product(
        vendor_id=vendor.id,
        category_id=payload.category_id,
        name=payload.name,
        slug=unique_slug(payload.name),
        description=payload.description,
        unit=payload.unit,
        price=payload.price,
        stock_qty=payload.stock_qty,
        status=payload.status,
    )
    db.add(product)
    await db.flush()
    return await get_product(db, product.id)


async def _get_owned_product(db: AsyncSession, vendor: User, product_id: int) -> Product:
    """Load a product and prove the caller may modify it."""
    product = await get_product(db, product_id)
    if product.vendor_id != vendor.id and vendor.role != UserRole.ADMIN:
        raise PermissionDeniedError("This product belongs to another vendor.")
    return product


async def update_product(
    db: AsyncSession, vendor: User, product_id: int, payload: ProductUpdate
) -> Product:
    product = await _get_owned_product(db, vendor, product_id)
    updates = payload.model_dump(exclude_unset=True)

    if "category_id" in updates:
        await get_category(db, updates["category_id"])
    if "name" in updates and updates["name"] != product.name:
        product.slug = unique_slug(updates["name"])

    for field, value in updates.items():
        setattr(product, field, value)

    # Keep the status honest rather than making the vendor remember to.
    if product.stock_qty == 0 and product.status == ProductStatus.ACTIVE:
        product.status = ProductStatus.OUT_OF_STOCK
    elif product.stock_qty > 0 and product.status == ProductStatus.OUT_OF_STOCK:
        product.status = ProductStatus.ACTIVE

    await db.flush()
    return await get_product(db, product.id)


async def archive_product(db: AsyncSession, vendor: User, product_id: int) -> Product:
    """Soft delete.

    Hard deleting would orphan the `order_items` that reference this product,
    destroying the order history vendors and clients both rely on.
    """
    product = await _get_owned_product(db, vendor, product_id)
    product.status = ProductStatus.ARCHIVED
    await db.flush()
    return await get_product(db, product.id)


async def add_product_image(
    db: AsyncSession, vendor: User, product_id: int, payload: ProductImageCreate
) -> Product:
    product = await _get_owned_product(db, vendor, product_id)
    # Proves the key is under the caller's prefix AND that the object exists.
    await media_service.verify_owned_key(payload.object_key, vendor)

    db.add(
        ProductImage(
            product_id=product.id,
            object_key=payload.object_key,
            sort_order=payload.sort_order,
        )
    )
    await db.flush()
    return await get_product(db, product.id)


async def delete_product_image(
    db: AsyncSession, vendor: User, product_id: int, image_id: int
) -> None:
    await _get_owned_product(db, vendor, product_id)
    image = await db.get(ProductImage, image_id)
    if image is None or image.product_id != product_id:
        raise NotFoundError("Image not found on this product.")
    await db.delete(image)
    await db.flush()
