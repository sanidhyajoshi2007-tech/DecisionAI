"""
RAG API router for DecisionAI.
Endpoints for asking questions across meeting archive with citations and streaming.
Strict constraints: No emojis, no em dashes.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import Settings, get_settings
from backend.database import get_db
from backend.models.meeting import Meeting
from backend.models.user import User
from backend.schemas.rag import AskRequest, AskResponse
from backend.services.audit import log_audit_event
from backend.services.rate_limiter import default_rate_limiter
from backend.services.rag.pipeline import (
    execute_rag_query,
    index_meeting_transcript,
    stream_rag_query,
)
from backend.services.rag.vector_store import get_vector_store

router = APIRouter(tags=["rag"])


@router.post("/api/ask", response_model=AskResponse)
@router.post("/ask", response_model=AskResponse)
async def ask_ai_endpoint(
    request: Request,
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    RAG meeting intelligence query endpoint.
    Retrieves decision records, performs hybrid search over transcript chunks,
    and returns synthesized answer with grounded citations.
    Enforces user_id filtering.
    """
    # Rate limit check per IP / user
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"rag_{current_user.id}_{client_ip}"
    allowed, retry_after, _ = await default_rate_limiter.check(
        rate_key, max_requests=settings.rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
        )

    # Optional meeting_id validation
    if payload.meeting_id is not None:
        m_res = await db.execute(
            select(Meeting).where(Meeting.id == payload.meeting_id, Meeting.user_id == current_user.id)
        )
        if not m_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting #{payload.meeting_id} not found or does not belong to you.",
            )

    # Audit logging: record query occurrence with target scope, NEVER the question text or tokens
    target_scope = f"scope:meeting:{payload.meeting_id}" if payload.meeting_id else "scope:all"
    await log_audit_event(
        db=db,
        action="ask_ai",
        user_id=current_user.id,
        target=target_scope,
        request=request,
    )

    if payload.stream:
        return StreamingResponse(
            stream_rag_query(
                db=db,
                question=payload.question,
                user_id=current_user.id,
                meeting_id=payload.meeting_id,
                settings=settings,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    response = await execute_rag_query(
        db=db,
        question=payload.question,
        user_id=current_user.id,
        meeting_id=payload.meeting_id,
        settings=settings,
    )
    return response


@router.post("/api/rag/index-all")
async def index_all_meetings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Utility endpoint to chunk and embed all completed meetings for current user into ChromaDB.
    """
    res = await db.execute(
        select(Meeting).where(Meeting.user_id == current_user.id)
    )
    meetings = list(res.scalars().all())

    total_chunks = 0
    indexed_meetings = 0
    for m in meetings:
        count = await index_meeting_transcript(
            db=db,
            meeting_id=m.id,
            user_id=current_user.id,
            settings=settings,
        )
        if count > 0:
            total_chunks += count
            indexed_meetings += 1

    return {
        "status": "success",
        "indexed_meetings": indexed_meetings,
        "total_chunks": total_chunks,
    }


@router.get("/api/rag/status")
async def get_rag_status(
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """
    Check ChromaDB indexing status and count of chunks for current user.
    """
    vs = get_vector_store(settings=settings)
    user_chunks = vs.get_user_chunks(user_id=current_user.id)
    return {
        "user_id": current_user.id,
        "indexed_chunks": len(user_chunks),
        "embedding_model": settings.embedding_model,
        "chroma_dir": settings.chroma_persist_dir,
    }
