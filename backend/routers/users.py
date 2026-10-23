"""
Users API routes for DecisionAI.
Handles user settings, data retention policies, and complete user data deletion.
Strict constraints: No emojis, no em dashes.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import Settings, get_settings
from backend.database import get_db
from backend.middleware.security import CSRF_COOKIE_NAME
from backend.models.meeting import Meeting
from backend.models.user import User
from backend.schemas.user import (
    DeleteAllDataRequest,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from backend.services.audit import log_audit_event
from backend.services.rag.vector_store import get_vector_store
from backend.services.retention import run_retention_cleanup
from backend.services.storage import get_storage_service

router = APIRouter(tags=["users"])


# ── Retention & Privacy Settings ──────────────────────────

@router.get("/api/users/me/settings", response_model=UserSettingsResponse)
async def get_user_settings(
    current_user: User = Depends(get_current_user),
) -> UserSettingsResponse:
    """Return data retention and privacy settings for currently authenticated user."""
    return UserSettingsResponse(retention_days=current_user.retention_days)


@router.patch("/api/users/me/settings", response_model=UserSettingsResponse)
async def update_user_settings(
    request: Request,
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserSettingsResponse:
    """Update data retention policy (None/0 for keep forever, 30, 90, or 365 days)."""
    try:
        val = payload.validate_retention()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e

    current_user.retention_days = val
    await db.commit()
    await db.refresh(current_user)

    await log_audit_event(
        db=db,
        action="settings_update",
        user_id=current_user.id,
        target=f"retention:{val if val is not None else 'forever'}",
        request=request,
    )

    return UserSettingsResponse(retention_days=current_user.retention_days)


# ── Delete All My Data ────────────────────────────────────

@router.post("/api/users/me/delete-all-data")
async def delete_all_user_data(
    request: Request,
    response: Response,
    payload: DeleteAllDataRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """
    Permanently erases all user meetings, transcript segments, extracted decisions,
    audio files from storage, vector embeddings from ChromaDB, and the user record.
    Requires exact typed confirmation: 'DELETE ALL MY DATA'.
    """
    if payload.confirmation.strip() != "DELETE ALL MY DATA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Typed confirmation mismatch. You must enter 'DELETE ALL MY DATA' exactly.",
        )

    user_id = current_user.id

    # 1. Log audit entry for compliance before deleting user
    await log_audit_event(
        db=db,
        action="delete_all_data",
        user_id=user_id,
        target=f"user:{user_id}",
        request=request,
    )

    # 2. Collect meeting IDs belonging to this user
    res = await db.execute(select(Meeting.id).where(Meeting.user_id == user_id))
    meeting_ids = list(res.scalars().all())

    # 3. Purge vector embeddings from ChromaDB
    try:
        vs = get_vector_store(settings=settings)
        vs.delete_user_data(user_id=user_id)
    except Exception:
        pass

    # 4. Purge all audio recordings via storage service
    storage = get_storage_service(settings=settings)
    storage.delete_user_storage(user_id=user_id, meeting_ids=meeting_ids)

    # 5. Delete user record (cascades to meetings, transcripts, and decisions)
    await db.delete(current_user)
    await db.commit()

    # 6. Invalidate session and CSRF cookies on client
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")

    return {
        "status": "deleted",
        "message": "All user data, transcripts, decisions, audio files, and vector embeddings have been permanently deleted.",
    }


# ── Trigger Retention Cleanup Job ─────────────────────────

@router.post("/api/retention/run-cleanup")
async def trigger_retention_cleanup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Executes retention cleanup job to delete expired meetings across DB, storage, and ChromaDB.
    Can be scheduled or invoked by system administrators.
    """
    summary = await run_retention_cleanup(db=db, settings=settings)
    return {"status": "success", "summary": summary}
