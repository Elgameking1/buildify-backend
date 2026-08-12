"""Populate the database with demo data.

Run with:  python -m app.seeds.seed

Idempotent - re-running tops the data up rather than duplicating it, so it is
safe to run before every demo.
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    AvailabilityStatus,
    ProductStatus,
    ProductUnit,
    SkillProficiency,
    UserRole,
)
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.core.slugs import slugify, unique_slug
from app.db.session import SessionFactory, engine
from app.modules.catalog.models import Category, Product
from app.modules.users.models import User, VendorProfile
from app.modules.workers.models import Skill, WorkerProfile, WorkerSkill

logger = logging.getLogger("seed")

DEMO_PASSWORD = "DemoPass!2026"

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Cement & Concrete", ["Bagged Cement", "Ready-Mix Concrete", "Concrete Blocks"]),
    ("Steel & Metal", ["Iron Rods", "Roofing Sheets", "Steel Pipes"]),
    ("Timber & Wood", ["Planks", "Plywood", "Doors"]),
    ("Plumbing", ["PVC Pipes", "Fittings", "Water Tanks"]),
    ("Electrical", ["Cables", "Switches & Sockets", "Lighting"]),
    ("Finishing", ["Paint", "Tiles", "Plaster of Paris"]),
]

SKILLS = [
    ("Masonry", "Block laying, plastering and concrete work"),
    ("Carpentry", "Roofing, doors, furniture and formwork"),
    ("Plumbing", "Pipework, fittings, water systems"),
    ("Electrical", "Wiring, sockets, distribution boards"),
    ("Painting", "Interior and exterior painting and finishing"),
    ("Welding", "Gates, burglar-proofing and metal fabrication"),
    ("Tiling", "Floor and wall tiling"),
    ("Roofing", "Roof structures and sheet installation"),
]

# (email, full name, business, region, city)
VENDORS = [
    ("vendor@demo.com", "Kwame Mensah", "Mensah Building Supplies", "Greater Accra", "Accra"),
    ("vendor2@demo.com", "Ama Boateng", "Boateng Hardware Ltd", "Ashanti", "Kumasi"),
]

# (email, full name, headline, region, city, years, rate, [skills])
WORKERS = [
    (
        "worker@demo.com",
        "Yaw Osei",
        "Experienced mason and block layer",
        "Greater Accra",
        "Accra",
        12,
        Decimal("250.00"),
        ["Masonry", "Tiling"],
    ),
    (
        "worker2@demo.com",
        "Akosua Danso",
        "Certified electrician - domestic and commercial",
        "Greater Accra",
        "Tema",
        8,
        Decimal("300.00"),
        ["Electrical"],
    ),
    (
        "worker3@demo.com",
        "Kofi Antwi",
        "Plumber - pipework, tanks and repairs",
        "Ashanti",
        "Kumasi",
        5,
        Decimal("200.00"),
        ["Plumbing", "Welding"],
    ),
]

# (category slug, name, unit, price, stock)
PRODUCTS = [
    ("bagged-cement", "Ghacem Super Rapid 50kg", ProductUnit.BAG, Decimal("95.00"), 500),
    ("bagged-cement", "Dangote 3X Cement 50kg", ProductUnit.BAG, Decimal("92.50"), 400),
    ("concrete-blocks", "6-inch Solid Block", ProductUnit.PIECE, Decimal("6.50"), 3000),
    ("iron-rods", "12mm Iron Rod (12m)", ProductUnit.PIECE, Decimal("115.00"), 250),
    ("iron-rods", "16mm Iron Rod (12m)", ProductUnit.PIECE, Decimal("190.00"), 180),
    ("roofing-sheets", "Aluzinc Roofing Sheet 0.45mm", ProductUnit.METRE, Decimal("78.00"), 600),
    ("planks", "Wawa Plank 2x4 (12ft)", ProductUnit.PIECE, Decimal("45.00"), 400),
    ("plywood", "Marine Plywood 18mm", ProductUnit.PIECE, Decimal("320.00"), 90),
    ("pvc-pipes", "PVC Pipe 4-inch (6m)", ProductUnit.PIECE, Decimal("85.00"), 220),
    ("water-tanks", "Polytank 1000L", ProductUnit.PIECE, Decimal("950.00"), 40),
    ("cables", "2.5mm Single Core Cable", ProductUnit.METRE, Decimal("12.00"), 2000),
    ("paint", "Emulsion Paint White 20L", ProductUnit.LITRE, Decimal("480.00"), 120),
    ("tiles", "Ceramic Floor Tile 40x40", ProductUnit.PIECE, Decimal("28.00"), 1500),
]


async def _get_or_create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    role: UserRole,
    region: str | None = None,
    city: str | None = None,
    phone: str | None = None,
) -> tuple[User, bool]:
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    user = User(
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name=full_name,
        role=role,
        region=region,
        city=city,
        phone=phone,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user, True


async def seed_categories(db: AsyncSession) -> dict[str, Category]:
    by_slug: dict[str, Category] = {
        category.slug: category
        for category in (await db.execute(select(Category))).scalars().all()
    }

    for parent_name, child_names in CATEGORIES:
        parent_slug = slugify(parent_name)
        parent = by_slug.get(parent_slug)
        if parent is None:
            parent = Category(name=parent_name, slug=parent_slug)
            db.add(parent)
            await db.flush()
            by_slug[parent_slug] = parent

        for child_name in child_names:
            child_slug = slugify(child_name)
            if child_slug not in by_slug:
                child = Category(name=child_name, slug=child_slug, parent_id=parent.id)
                db.add(child)
                await db.flush()
                by_slug[child_slug] = child

    return by_slug


async def seed_skills(db: AsyncSession) -> dict[str, Skill]:
    by_name = {skill.name: skill for skill in (await db.execute(select(Skill))).scalars().all()}
    for name, description in SKILLS:
        if name not in by_name:
            skill = Skill(name=name, slug=slugify(name), description=description)
            db.add(skill)
            await db.flush()
            by_name[name] = skill
    return by_name


async def seed_users(db: AsyncSession, skills: dict[str, Skill]) -> list[User]:
    await _get_or_create_user(
        db,
        email="admin@demo.com",
        full_name="System Administrator",
        role=UserRole.ADMIN,
    )
    await _get_or_create_user(
        db,
        email="client@demo.com",
        full_name="Nana Adjei",
        role=UserRole.CLIENT,
        region="Greater Accra",
        city="Accra",
        phone="0244000001",
    )

    vendors: list[User] = []
    for email, full_name, business, region, city in VENDORS:
        vendor, created = await _get_or_create_user(
            db,
            email=email,
            full_name=full_name,
            role=UserRole.VENDOR,
            region=region,
            city=city,
            phone="0244000002",
        )
        if created:
            db.add(
                VendorProfile(
                    user_id=vendor.id,
                    business_name=business,
                    description=f"{business} - building materials supplier in {city}.",
                    location=city,
                    is_verified=True,
                )
            )
            await db.flush()
        vendors.append(vendor)

    for email, full_name, headline, region, city, years, rate, skill_names in WORKERS:
        worker, created = await _get_or_create_user(
            db,
            email=email,
            full_name=full_name,
            role=UserRole.WORKER,
            region=region,
            city=city,
            phone="0244000003",
        )
        if created:
            db.add(
                WorkerProfile(
                    user_id=worker.id,
                    headline=headline,
                    bio=f"{full_name} has {years} years of experience working in {city}.",
                    years_experience=years,
                    base_rate=rate,
                    availability_status=AvailabilityStatus.AVAILABLE,
                    region=region,
                    city=city,
                    portfolio_keys=[],
                )
            )
            await db.flush()
            for skill_name in skill_names:
                skill = skills.get(skill_name)
                if skill is not None:
                    db.add(
                        WorkerSkill(
                            worker_id=worker.id,
                            skill_id=skill.id,
                            proficiency=SkillProficiency.EXPERT,
                        )
                    )
            await db.flush()

    return vendors


async def seed_products(
    db: AsyncSession, categories: dict[str, Category], vendors: list[User]
) -> int:
    existing = {
        name for name in (await db.execute(select(Product.name))).scalars().all()
    }
    created = 0

    for index, (category_slug, name, unit, price, stock) in enumerate(PRODUCTS):
        if name in existing:
            continue
        category = categories.get(category_slug)
        if category is None:
            logger.warning("Skipping %s - unknown category %s", name, category_slug)
            continue

        vendor = vendors[index % len(vendors)]
        db.add(
            Product(
                vendor_id=vendor.id,
                category_id=category.id,
                name=name,
                slug=unique_slug(name),
                description=f"{name}. Supplied in {unit.value.lower()} units. "
                "Quality guaranteed, available for immediate pickup.",
                unit=unit,
                price=price,
                stock_qty=stock,
                status=ProductStatus.ACTIVE,
            )
        )
        created += 1

    await db.flush()
    return created


async def main() -> None:
    configure_logging()
    async with SessionFactory() as db:
        categories = await seed_categories(db)
        skills = await seed_skills(db)
        vendors = await seed_users(db, skills)
        product_count = await seed_products(db, categories, vendors)
        await db.commit()

    logger.info(
        "Seed complete: %d categories, %d skills, %d new products",
        len(categories),
        len(skills),
        product_count,
    )
    logger.info("Demo accounts (password: %s):", DEMO_PASSWORD)
    for email in (
        "admin@demo.com",
        "client@demo.com",
        "vendor@demo.com",
        "worker@demo.com",
    ):
        logger.info("  %s", email)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
