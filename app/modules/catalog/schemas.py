from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ProductStatus, ProductUnit


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None
    parent_id: int | None = None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    parent_id: int | None = None


class CategoryTree(CategoryRead):
    children: list["CategoryTree"] = Field(default_factory=list)


class VendorSummary(BaseModel):
    id: int
    business_name: str
    location: str | None = None
    is_verified: bool = False


class ProductImageRead(BaseModel):
    id: int
    url: str | None
    sort_order: int


class ProductRead(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    unit: ProductUnit
    price: Decimal
    stock_qty: int
    status: ProductStatus
    category: CategoryRead
    vendor: VendorSummary
    images: list[ProductImageRead]
    created_at: datetime


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    category_id: int
    unit: ProductUnit
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    stock_qty: int = Field(default=0, ge=0)
    status: ProductStatus = ProductStatus.ACTIVE


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    category_id: int | None = None
    unit: ProductUnit | None = None
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    stock_qty: int | None = Field(default=None, ge=0)
    status: ProductStatus | None = None


class ProductImageCreate(BaseModel):
    object_key: str = Field(max_length=512, description="Returned by POST /media/upload-url")
    sort_order: int = 0
