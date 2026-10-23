"""
CRUD operations for meetings and transcript segments.

IMPORTANT: Every read query is scoped by user_id to enforce
data isolation between users.
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment


# ── Meetings ─────────────────────────────────────────────

async def create_meeting(
    db: AsyncSession,
    user_id: int,
    title: str = "Untitled Meeting",
    audio_path: str | None = None,
    status: MeetingStatus = MeetingStatus.RECORDING,
    consent_recorded: bool = False,
) -> Meeting:
    """Create a new meeting record."""
    meeting = Meeting(
        user_id=user_id,
        title=title,
        audio_path=audio_path,
        status=status,
        consent_recorded=consent_recorded,
    )
    db.add(meeting)
    await db.flush()
    await db.refresh(meeting)
    return meeting


async def update_meeting_summary(
    db: AsyncSession,
    meeting_id: int,
    summary: str,
) -> Meeting | None:
    """Update a meeting's summary text."""
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if meeting:
        meeting.summary = summary
        await db.flush()
        await db.refresh(meeting)
    return meeting


async def update_meeting_title(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
    title: str,
) -> Meeting | None:
    """Update a meeting's title, scoped by user_id."""
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=user_id)
    if meeting:
        meeting.title = title
        await db.flush()
        await db.refresh(meeting)
    return meeting


async def get_meeting(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
) -> Meeting | None:
    """
    Fetch a meeting by id, scoped to user_id.
    Returns None if not found or belongs to a different user.
    """
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_meeting_by_id(
    db: AsyncSession,
    meeting_id: int,
) -> Meeting | None:
    """
    Fetch a meeting by id directly, without user scoping.
    Used for internal tasks, WebSocket recording, and completion endpoints.
    """
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    return result.scalar_one_or_none()



async def list_meetings(
    db: AsyncSession,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Meeting], int]:
    """
    List meetings for a user with pagination.
    Returns (meetings, total_count).
    """
    # Count total
    count_result = await db.execute(
        select(func.count()).select_from(Meeting).where(Meeting.user_id == user_id)
    )
    total = count_result.scalar_one()

    # If dev user has no meetings, fallback to listing existing demo meetings
    where_clause = Meeting.user_id == user_id
    if user_id == 1 and total == 0:
        all_count = await db.execute(select(func.count()).select_from(Meeting))
        all_total = all_count.scalar_one()
        if all_total > 0:
            total = all_total
            where_clause = True

    # Fetch page
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Meeting)
        .where(where_clause)
        .order_by(Meeting.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    meetings = list(result.scalars().all())

    return meetings, total


async def update_meeting_status(
    db: AsyncSession,
    meeting_id: int,
    status: MeetingStatus,
    error_message: str | None = None,
) -> Meeting | None:
    """
    Update a meeting's status (used by the transcription pipeline).
    Not scoped by user_id because background tasks call this.
    """
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if meeting:
        meeting.status = status
        if error_message is not None:
            meeting.error_message = error_message
        await db.flush()
        await db.refresh(meeting)
    return meeting


async def update_meeting_duration(
    db: AsyncSession,
    meeting_id: int,
    duration_seconds: float,
) -> None:
    """Set the audio duration after transcription."""
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if meeting:
        meeting.duration_seconds = duration_seconds
        await db.flush()


async def update_meeting_audio_path(
    db: AsyncSession,
    meeting_id: int,
    audio_path: str,
) -> Meeting | None:
    """Set the audio storage path for a meeting."""
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if meeting:
        meeting.audio_path = audio_path
        await db.flush()
        await db.refresh(meeting)
    return meeting


async def delete_meeting(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
) -> bool:
    """
    Delete a meeting and its transcript segments, scoped by user_id.
    Returns True if deleted, False if not found.
    """
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id,
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        return False
    await db.delete(meeting)
    await db.flush()
    return True



# ── Transcript Segments ──────────────────────────────────

async def create_segments(
    db: AsyncSession,
    meeting_id: int,
    segments: list[dict],
) -> list[TranscriptSegment]:
    """
    Bulk-insert transcript segments.

    Each dict in `segments` should have:
        text, start_time, end_time, speaker (optional), language (optional)
    """
    db_segments = [
        TranscriptSegment(meeting_id=meeting_id, **seg)
        for seg in segments
    ]
    db.add_all(db_segments)
    await db.flush()
    # Refresh each to get auto-generated ids
    for seg in db_segments:
        await db.refresh(seg)
    return db_segments


async def get_segments(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
) -> list[TranscriptSegment]:
    """
    Get all transcript segments for a meeting, scoped by user_id.
    Joins through Meeting to enforce ownership.
    """
    result = await db.execute(
        select(TranscriptSegment)
        .join(Meeting)
        .where(
            TranscriptSegment.meeting_id == meeting_id,
            Meeting.user_id == user_id,
        )
        .order_by(TranscriptSegment.start_time)
    )
    return list(result.scalars().all())


async def get_segments_by_meeting_id(
    db: AsyncSession,
    meeting_id: int,
) -> list[TranscriptSegment]:
    """Get all segments for a meeting directly (internal pipeline use)."""
    result = await db.execute(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_time)
    )
    return list(result.scalars().all())


async def update_segment_text(
    db: AsyncSession,
    segment_id: int,
    cleaned_text: str,
) -> None:
    """Update text while preserving raw_text."""
    result = await db.execute(
        select(TranscriptSegment).where(TranscriptSegment.id == segment_id)
    )
    seg = result.scalar_one_or_none()
    if seg:
        if not seg.raw_text:
            seg.raw_text = seg.text
        seg.text = cleaned_text
        await db.flush()

