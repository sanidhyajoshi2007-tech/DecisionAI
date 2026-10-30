"""
Database session management.

Creates the async SQLAlchemy engine and session factory.
The `get_db` dependency yields a session per request and ensures
cleanup (commit on success, rollback on error) via the context manager.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings

settings = get_settings()

# echo=True in dev shows SQL in the console — helpful for debugging queries.
engine = create_async_engine(
    settings.resolved_database_url,
    echo=settings.debug,
    # SQLite needs this to allow concurrent readers.
    connect_args={"check_same_thread": False} if "sqlite" in settings.resolved_database_url else {},
)

# expire_on_commit=False so we can access attributes after commit
# without triggering a lazy load (which would fail outside a session).
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
async_session_maker = async_session_factory



async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session.

    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
