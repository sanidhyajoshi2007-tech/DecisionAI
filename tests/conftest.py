"""
Pytest configuration & shared fixtures.

Key fixture: `client` — an async HTTPX test client wired to the FastAPI app.
Each test gets a fresh in-memory SQLite database so tests never interfere
with each other or the real dev database.
"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import Settings, get_settings
from backend.database import get_db
from backend.main import app


def get_test_settings() -> Settings:
    """Settings override for tests — in-memory DB, debug off."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        debug=False,
        audio_storage_dir="test_audio_storage",
    )


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Async test client with an isolated in-memory database.

    Overrides the get_db and get_settings dependencies so every test
    starts with a clean slate.
    """
    test_settings = get_test_settings()

    # Create a fresh in-memory engine per test
    test_engine = create_async_engine(
        test_settings.database_url,
        echo=False,
    )
    test_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Import all models so create_all registers all tables
    import backend.models  # noqa: F401
    from backend.models.base import Base

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Wire overrides
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
    app.dependency_overrides.clear()
    await test_engine.dispose()

    import shutil
    if Path(test_settings.audio_storage_dir).exists():
        shutil.rmtree(test_settings.audio_storage_dir, ignore_errors=True)
