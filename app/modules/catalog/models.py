"""Materials catalogue: categories, products, and product images."""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ProductStatus, ProductUnit
from app.db.base import Base, PKMixin, TimestampMixin

if TYPE_CHECKING:
    from app.modules.users.models import User


class Category(PKMixin, TimestampMixin, Base):
    """Self-referencing tree, e.g. Cement -> Bagged cement."""

    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # No `parent`/`children` relationships on purpose: the tree is assembled
    # from a single flat SELECT in `serializers.build_category_tree`, and
    # descendants are resolved by id in `service._descendant_category_ids`.
    # Adjacency-list relationships here would only add lazy loads that raise
    # under async SQLAlchemy.
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(PKMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        # Powers ?q= keyword search without a separate search engine.
        Index("ix_products_fulltext", "name", "description", mysql_prefix="FULLTEXT"),
        # The browse-and-filter path: category page sorted by price.
        Index("ix_products_category_status_price", "category_id", "status", "price"),
        Index("ix_products_vendor_status", "vendor_id", "status"),
    )

    vendor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit: Mapped[ProductUnit] = mapped_column(
        SAEnum(ProductUnit, native_enum=False, length=16), nullable=False
    )
    # DECIMAL, never float - a marketplace cannot tolerate binary rounding drift.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[ProductStatus] = mapped_column(
        SAEnum(ProductStatus, native_enum=False, length=16),
        default=ProductStatus.DRAFT,
        nullable=False,
    )

    vendor: Mapped["User"] = relationship()
    category: Mapped["Category"] = relationship(back_populates="products")
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
        lazy="selectin",
    )

    @property
    def is_purchasable(self) -> bool:
        return self.status == ProductStatus.ACTIVE and self.stock_qty > 0


class ProductImage(PKMixin, TimestampMixin, Base):
    """Only the R2 object key is stored.

    Public URLs are built at serialisation time so the CDN domain can change
    without a data migration.
    """

    __tablename__ = "product_images"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="images")
