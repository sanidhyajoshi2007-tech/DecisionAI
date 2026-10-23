"""
Data retention service.
Implements automatic retention policy evaluation and purge jobs.
Deletes expired meetings from SQLite database, local/remote audio storage, and ChromaDB vector store.
Strict constraints: No emojis, no em dashes.
"""

from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.models.meeting import Meeting
from backend.models.user import User
from backend.services.audit import log_audit_event
from backend.services.rag.vector_store import get_vector_store
from backend.services.storage import StorageService, get_storage_service

logger = logging.getLogger("decisionai.retention")


async def run_retention_cleanup(
    db: AsyncSession,
    storage_service: StorageService | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """
    Finds and deletes meetings exceeding each user's configured retention_days policy.
    Purges:
    1. Audio files (user upload and streaming WAVs) via StorageService.
    2. Vector embeddings from ChromaDB via ChromaVectorStore.
    3. Meeting, segments, and decision rows from SQLite database.
    4. Records an audit log entry for each purged meeting.
    """
    cfg = settings or get_settings()
    storage = storage_service or get_storage_service(cfg)
    vector_store = get_vector_store(cfg)

    # 1. Fetch users with an active retention policy (> 0 days)
    stmt = select(User).where(User.retention_days.is_not(None), User.retention_days > 0)
    result = await db.execute(stmt)
    users = list(result.scalars().all())

    total_meetings_deleted = 0
    total_audio_files_deleted = 0
    now = datetime.now(timezone.utc)

    for user in users:
        retention_days = user.retention_days
        if not retention_days or retention_days <= 0:
            continue

        cutoff_date = now - timedelta(days=retention_days)

        # Find expired meetings for this user
        meetings_stmt = select(Meeting).where(
            Meeting.user_id == user.id,
            Meeting.created_at < cutoff_date,
        )
        m_result = await db.execute(meetings_stmt)
        expired_meetings = list(m_result.scalars().all())

        for meeting in expired_meetings:
            meeting_id = meeting.id

            # 1. Delete audio files via storage service
            audio_count = storage.delete_meeting_audio(
                user_id=user.id,
                meeting_id=meeting_id,
                audio_path=meeting.audio_path,
            )
            total_audio_files_deleted += audio_count

            # 2. Delete ChromaDB vectors
            try:
                vector_store.delete_meeting(meeting_id=meeting_id, user_id=user.id)
            except Exception as e:
                logger.warning(f"Failed to delete ChromaDB vectors for meeting {meeting_id}: {e}")

            # 3. Log audit event
            await log_audit_event(
                db=db,
                action="retention_delete",
                user_id=user.id,
                target=f"meeting:{meeting_id}",
            )

            # 4. Delete meeting record (cascade deletes segments and decisions)
            await db.delete(meeting)
            total_meetings_deleted += 1

    await db.commit()

    return {
        "users_checked": len(users),
        "meetings_deleted": total_meetings_deleted,
        "audio_files_deleted": total_audio_files_deleted,
    }
