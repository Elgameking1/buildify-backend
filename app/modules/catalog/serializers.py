"""ORM -> schema conversion for catalogue objects.

Kept out of the router so the orders module can reuse the same product shape.
"""

from app.modules.catalog.models import Category, Product
from app.modules.catalog.schemas import (
    CategoryRead,
    CategoryTree,
    ProductImageRead,
    ProductRead,
    VendorSummary,
)
from app.modules.media.r2 import public_url


def serialise_vendor_summary(product: Product) -> VendorSummary:
    vendor = product.vendor
    profile = getattr(vendor, "vendor_profile", None)
    return VendorSummary(
        id=vendor.id,
        business_name=profile.business_name if profile else vendor.full_name,
        location=profile.location if profile else vendor.city,
        is_verified=profile.is_verified if profile else False,
    )


def serialise_product(product: Product) -> ProductRead:
    return ProductRead(
        id=product.id,
        name=product.name,
        slug=product.slug,
        description=product.description,
        unit=product.unit,
        price=product.price,
        stock_qty=product.stock_qty,
        status=product.status,
        category=CategoryRead.model_validate(product.category, from_attributes=True),
        vendor=serialise_vendor_summary(product),
        images=[
            ProductImageRead(
                id=image.id, url=public_url(image.object_key), sort_order=image.sort_order
            )
            for image in product.images
        ],
        created_at=product.created_at,
    )


def build_category_tree(categories: list[Category]) -> list[CategoryTree]:
    """Assemble a flat category list into a nested tree in one pass."""
    nodes = {
        category.id: CategoryTree.model_validate(category, from_attributes=True)
        for category in categories
    }
    roots: list[CategoryTree] = []
    for category in categories:
        node = nodes[category.id]
        parent = nodes.get(category.parent_id) if category.parent_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            roots.append(node)
    return roots
