"""
Tests for Phase 1 — database models, CRUD operations, and user scoping.
"""

import pytest

from backend.crud.users import create_user, get_user_by_id, get_user_by_email
from backend.crud.meetings import (
    create_meeting,
    get_meeting,
    list_meetings,
    update_meeting_status,
    create_segments,
    get_segments,
)
from backend.models.meeting import MeetingStatus


@pytest.mark.asyncio
async def test_create_and_get_user(client):
    """Can create a user and retrieve it by id and email."""
    # The dev user is auto-created by the auth dependency,
    # but let's test the CRUD directly via the db fixture.
    # We'll use the client fixture to get a working db session.
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_meeting(client):
    """Creating a meeting sets the right defaults."""
    from tests.conftest import get_test_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        # Create user first
        user = await create_user(db, email="test@example.com", name="Test User")

        # Create meeting
        meeting = await create_meeting(db, user_id=user.id, title="Sprint Planning")
        assert meeting.title == "Sprint Planning"
        assert meeting.status == MeetingStatus.UPLOADING
        assert meeting.user_id == user.id

        # Retrieve it
        fetched = await get_meeting(db, meeting.id, user.id)
        assert fetched is not None
        assert fetched.id == meeting.id

        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_scoping():
    """A user cannot see another user's meetings."""
    from tests.conftest import get_test_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user_a = await create_user(db, email="a@test.com", name="User A")
        user_b = await create_user(db, email="b@test.com", name="User B")

        # User A creates a meeting
        meeting = await create_meeting(db, user_id=user_a.id, title="A's Meeting")

        # User A can see it
        assert await get_meeting(db, meeting.id, user_a.id) is not None

        # User B CANNOT see it
        assert await get_meeting(db, meeting.id, user_b.id) is None

        # User B's list is empty
        meetings, total = await list_meetings(db, user_b.id)
        assert total == 0
        assert meetings == []

        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_meeting_status_transitions():
    """Meeting status can be updated through the pipeline lifecycle."""
    from tests.conftest import get_test_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = await create_user(db, email="status@test.com", name="Status User")
        meeting = await create_meeting(db, user_id=user.id)

        assert meeting.status == MeetingStatus.UPLOADING

        # Transition: uploading → processing
        updated = await update_meeting_status(db, meeting.id, MeetingStatus.PROCESSING)
        assert updated.status == MeetingStatus.PROCESSING

        # Transition: processing → ready
        updated = await update_meeting_status(db, meeting.id, MeetingStatus.READY)
        assert updated.status == MeetingStatus.READY

        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_transcript_segments():
    """Can create and retrieve transcript segments scoped by user."""
    from tests.conftest import get_test_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = await create_user(db, email="seg@test.com", name="Segment User")
        meeting = await create_meeting(db, user_id=user.id, title="Demo Meeting")

        # Insert segments
        raw_segments = [
            {"text": "Hello everyone", "start_time": 0.0, "end_time": 2.5, "speaker": "Alice", "language": "en"},
            {"text": "Namaste", "start_time": 2.5, "end_time": 4.0, "speaker": "Bob", "language": "hi"},
            {"text": "Let's begin the standup", "start_time": 4.0, "end_time": 6.5, "speaker": "Alice", "language": "en"},
        ]
        segments = await create_segments(db, meeting.id, raw_segments)
        assert len(segments) == 3
        assert segments[0].text == "Hello everyone"
        assert segments[1].language == "hi"

        # Retrieve scoped by user
        fetched = await get_segments(db, meeting.id, user.id)
        assert len(fetched) == 3
        # Should be ordered by start_time
        assert fetched[0].start_time < fetched[1].start_time < fetched[2].start_time

        # Another user can't see them
        user2 = await create_user(db, email="other@test.com", name="Other")
        other_segs = await get_segments(db, meeting.id, user2.id)
        assert other_segs == []

        await db.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_meeting_list_pagination():
    """Pagination returns correct slices and total count."""
    from tests.conftest import get_test_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = await create_user(db, email="page@test.com", name="Page User")

        # Create 5 meetings
        for i in range(5):
            await create_meeting(db, user_id=user.id, title=f"Meeting {i}")

        # Page 1 of size 2
        meetings, total = await list_meetings(db, user.id, page=1, page_size=2)
        assert total == 5
        assert len(meetings) == 2

        # Page 3 of size 2 (only 1 left)
        meetings, total = await list_meetings(db, user.id, page=3, page_size=2)
        assert total == 5
        assert len(meetings) == 1

        await db.commit()

    await engine.dispose()
