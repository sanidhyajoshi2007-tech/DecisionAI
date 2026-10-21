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
    status: str = "accepted",
    timestamp_ms: int | None = None,
) -> Decision:
    """Create a new decision record associated with a meeting."""
    decision = Decision(
        meeting_id=meeting_id,
        text=text,
        type=type,
        status=status,
        timestamp_ms=timestamp_ms or 0,
    )
    db.add(decision)
    await db.flush()
    await db.refresh(decision)
    return decision


async def bulk_create_decisions(
    db: AsyncSession,
    meeting_id: int,
    items: list[dict],
) -> list[Decision]:
    """Bulk insert extracted decisions, action items, and client requests."""
    db_items = [
        Decision(
            meeting_id=meeting_id,
            text=item["text"],
            type=item.get("type", "decision"),
            status=item.get("status", "accepted"),
            timestamp_ms=item.get("timestamp_ms", 0),
        )
        for item in items
    ]
    db.add_all(db_items)
    await db.flush()
    for d in db_items:
        await db.refresh(d)
    return db_items



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
