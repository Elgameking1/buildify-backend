"""Async engine and session factory.

`get_db` is the single request-scoped session dependency.  It commits when the
handler returns cleanly and rolls back on any exception, so services never need
to manage transaction boundaries themselves - they just raise.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    # Never `echo=True`: it attaches a second, plain-text handler of its own,
    # so every statement would be logged twice - once raw and once as JSON.
    # SQL logging is switched on instead by raising the level of the
    # sqlalchemy.engine logger in `configure_logging`, which keeps it flowing
    # through the single JSON handler.
    echo=False,
    pool_pre_ping=True,   # a dropped MySQL connection is retried, not surfaced
    pool_recycle=1800,    # MySQL closes idle connections after 8h by default
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Empty unless MYSQL_SSL_CA is set; carries the TLS context a managed
    # database reached over the public internet requires.
    connect_args=settings.db_connect_args,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # response serialisation happens after commit
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
