"""
CRUD operations for meeting decisions and action items.

All read and delete queries are scoped to the meeting's owning user_id.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.decision import Decision
from backend.models.meeting import Meeting


async def create_decision(
    db: AsyncSession,
    meeting_id: int,
    text: str,
    type: str = "decision",
    timestamp_ms: int | None = None,
) -> Decision:
    """Create a new decision record associated with a meeting."""
    decision = Decision(
        meeting_id=meeting_id,
        text=text,
        type=type,
        timestamp_ms=timestamp_ms,
    )
    db.add(decision)
    await db.flush()
    await db.refresh(decision)
    return decision


async def get_decision(
    db: AsyncSession,
    decision_id: int,
    user_id: int,
) -> Decision | None:
    """
    Get a single decision, verifying ownership via Meeting.user_id.
    """
    result = await db.execute(
        select(Decision)
        .join(Meeting, Decision.meeting_id == Meeting.id)
        .where(
            Decision.id == decision_id,
            Meeting.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_decisions(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
) -> list[Decision]:
    """
    List all decisions for a meeting, scoped by user_id.
    """
    result = await db.execute(
        select(Decision)
        .join(Meeting, Decision.meeting_id == Meeting.id)
        .where(
            Decision.meeting_id == meeting_id,
            Meeting.user_id == user_id,
        )
        .order_by(Decision.created_at.asc())
    )
    return list(result.scalars().all())


async def delete_decision(
    db: AsyncSession,
    decision_id: int,
    user_id: int,
) -> bool:
    """
    Delete a decision, verifying ownership via Meeting.user_id.
    Returns True if deleted, False if not found or unauthorized.
    """
    decision = await get_decision(db, decision_id, user_id)
    if not decision:
        return False
    await db.delete(decision)
    await db.flush()
    return True
