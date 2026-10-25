"""Meeting API routes - handles audio upload, live audio streaming, and meeting viewer."""

import html
import json
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import Settings, get_settings
from backend.crud.decisions import (
    create_decision,
    delete_decision,
    list_decisions,
)
from backend.crud.meetings import (
    create_meeting,
    delete_meeting,
    get_meeting,
    get_meeting_by_id,
    get_segments,
    list_meetings,
    update_meeting_audio_path,
    update_meeting_title,
)
from backend.crud.users import create_user, get_user_by_id
from backend.database import async_session_maker, get_db
from backend.models.meeting import MeetingStatus
from backend.models.user import User
from backend.schemas.decision import DecisionCreate, DecisionListResponse, DecisionResponse
from backend.schemas.meeting import (
    MeetingCreate,
    MeetingListResponse,
    MeetingResponse,
    MeetingUpdate,
    TranscriptResponse,
    TranscriptSegmentResponse,
)
from backend.services.audio_streamer import (
    finish_writer,
    get_or_create_writer,
    parse_audio_chunk,
)
from backend.services.audit import log_audit_event
from backend.services.auth import create_extension_token, decode_token
from backend.services.rag.vector_store import get_vector_store
from backend.services.rate_limiter import rate_limit
from backend.services.storage import (
    get_storage_service,
    save_audio_file,
    validate_audio_file,
)

router = APIRouter(tags=["meetings"])


# ── Create Meeting (Live Streaming) ───────────────────────

@router.post(
    "/meetings",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a new meeting for recording",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
@router.post(
    "/api/meetings",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def create_new_meeting(
    request: Request,
    payload: MeetingCreate | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    """
    Create a meeting record ahead of live WebSocket streaming, scoped to current_user.
    """
    title = payload.title if payload and payload.title else "Google Meet Call"
    clean_title = title.strip()[:settings.max_title_length]
    consent = payload.consent_recorded if payload else False

    meeting = await create_meeting(
        db=db,
        user_id=current_user.id,
        title=clean_title,
        status=MeetingStatus.RECORDING,
        consent_recorded=consent,
    )

    await log_audit_event(
        db=db,
        action="meeting_create",
        user_id=current_user.id,
        target=f"meeting:{meeting.id}",
        request=request,
    )

    stream_token = create_extension_token(
        user_id=current_user.id,
        email=current_user.email,
        secret_key=settings.secret_key,
        expires_in_minutes=settings.extension_token_expire_minutes,
    )

    resp = MeetingResponse.model_validate(meeting)
    resp.stream_token = stream_token
    return resp



# ── Audio Upload (Multipart / Phase 2) ───────────────────

@router.post(
    "/api/meetings/upload",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload meeting audio recording",
)
@router.post(
    "/meetings/upload",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def upload_meeting_audio(
    request: Request,
    file: UploadFile | None = File(None, description="Audio file blob"),
    audio_file: UploadFile | None = File(None, description="Alternative field name for audio file"),
    title: str = Form("Untitled Meeting", description="Meeting title"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    """
    Accept a completed audio recording file from the Chrome extension or dashboard.
    """
    # Enforce upload size check from Content-Length header if present
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                    detail=f"File size exceeds maximum limit of {settings.max_upload_size_mb} MB",
                )
        except ValueError:
            pass

    actual_file = file or audio_file
    if actual_file is None:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail="An audio file must be provided under field 'file' or 'audio_file'",
        )

    clean_title = title.strip()[:settings.max_title_length] if title and title.strip() else "Untitled Meeting"
    ext = validate_audio_file(actual_file)

    meeting = await create_meeting(
        db=db,
        user_id=current_user.id,
        title=clean_title,
        consent_recorded=True,
    )

    try:
        relative_path, _full_path, _bytes_written = await save_audio_file(
            file=actual_file,
            user_id=current_user.id,
            meeting_id=meeting.id,
            settings=settings,
            extension=ext,
        )
        await update_meeting_audio_path(db, meeting_id=meeting.id, audio_path=relative_path)
    except Exception:
        await delete_meeting(db, meeting_id=meeting.id, user_id=current_user.id)
        raise

    await log_audit_event(
        db=db,
        action="meeting_create",
        user_id=current_user.id,
        target=f"meeting:{meeting.id}",
        request=request,
    )

    return MeetingResponse.model_validate(meeting)


# ── WebSocket Live Audio Streaming ────────────────────────

@router.websocket("/ws/meetings/{meeting_id}")
@router.websocket("/api/ws/meetings/{meeting_id}")
async def websocket_meeting_stream(
    websocket: WebSocket,
    meeting_id: int,
    settings: Settings = Depends(get_settings),
):
    """
    Stream 1-second 16 kHz 16-bit mono PCM chunks over WebSocket.
    Authenticates via ?token= query parameter or session cookie,
    and scopes connection to meeting owner.
    """
    token = websocket.query_params.get("token") or websocket.cookies.get(settings.session_cookie_name)
    user_id = None
    if token:
        try:
            payload = decode_token(token, secret_key=settings.secret_key)
            user_id = int(payload["sub"])
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
            return

    # Verify meeting ownership if meeting already exists in database
    async with async_session_maker() as db:
        meeting = await get_meeting_by_id(db, meeting_id)
        if meeting:
            if user_id is not None and meeting.user_id != user_id:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized meeting access")
                return
            elif user_id is None and settings.app_env == "development":
                # In local dev environment with Chrome extension, accept meeting's owner
                user_id = meeting.user_id
        elif user_id is None and settings.app_env != "development":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
            return

    await websocket.accept()
    writer = get_or_create_writer(meeting_id, settings)

    try:
        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"]:
                parsed = parse_audio_chunk(message["bytes"])
                if parsed:
                    channel, pcm_data = parsed
                    writer.write_chunk(channel, pcm_data)
            elif "text" in message and message["text"]:
                parsed = parse_audio_chunk(message["text"])
                if parsed:
                    channel, pcm_data = parsed
                    writer.write_chunk(channel, pcm_data)
    except (WebSocketDisconnect, Exception):
        # Gracefully handle client disconnect; keep files intact for reconnect
        pass


# ── Finish Recording ──────────────────────────────────────

@router.post(
    "/meetings/{meeting_id}/finish",
    summary="Finish live audio recording and finalize audio files",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
@router.post(
    "/api/meetings/{meeting_id}/finish",
    include_in_schema=False,
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def finish_meeting_recording(
    background_tasks: BackgroundTasks,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Called when recording stops. Closes writers, queues background transcription,
    and updates meeting status scoped to current_user.
    """
    meeting = await get_meeting_by_id(db, meeting_id)
    if meeting and meeting.user_id != current_user.id:
        if settings.app_env == "development" and current_user.id == 1:
            logger.info("Allowing meeting %d finish in development mode for dev user fallback", meeting_id)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to finish this meeting",
            )

    tab_path, mic_path, duration = finish_writer(meeting_id, settings)

    if not meeting:
        meeting = await create_meeting(db, user_id=current_user.id, title="Google Meet Call", status=MeetingStatus.PROCESSING)

    # Validate audio duration limit
    if duration > settings.max_audio_duration_seconds:
        meeting.status = MeetingStatus.FAILED
        meeting.error_message = f"Audio duration ({round(duration, 1)}s) exceeds maximum limit of {settings.max_audio_duration_seconds}s."
        await db.commit()
        await db.refresh(meeting)
        return {
            "status": meeting.status.value,
            "meeting_id": meeting_id,
            "title": meeting.title,
            "error": meeting.error_message,
        }

    # Launch background transcription and LLM post-processing task if audio files exist
    has_audio = (
        duration > 0.0
        or (tab_path.exists() and tab_path.stat().st_size > 44)
        or (mic_path.exists() and mic_path.stat().st_size > 44)
    )
    if has_audio:
        meeting.status = MeetingStatus.PROCESSING
        meeting.duration_seconds = duration
        meeting.audio_path = f"{meeting_id}_tab.wav"
        await db.commit()
        await db.refresh(meeting)

        from backend.services.stt import process_meeting_transcription
        background_tasks.add_task(
            process_meeting_transcription,
            meeting_id=meeting_id,
            tab_path=tab_path,
            mic_path=mic_path,
            settings=settings,
            glossary=settings.glossary_list,
        )
    else:
        meeting.status = MeetingStatus.READY
        meeting.duration_seconds = duration
        meeting.audio_path = f"{meeting_id}_tab.wav"
        await db.commit()
        await db.refresh(meeting)

    return {
        "status": meeting.status.value,
        "meeting_id": meeting_id,
        "title": meeting.title,
        "duration_seconds": round(duration, 2),
        "files": {
            "tab": tab_path.name if tab_path.exists() else None,
            "mic": mic_path.name if mic_path.exists() else None,
        },
    }


# ── Audio Playback / File Serving ─────────────────────────

@router.get("/meetings/{meeting_id}/audio/{channel}")
@router.get("/api/meetings/{meeting_id}/audio/{channel}", include_in_schema=False)
async def get_meeting_audio_file(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    channel: str = Path(..., pattern="^(tab|mic)$", description="Audio channel (tab or mic)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Serve recorded WAV file for a channel (tab or mic), scoped to current_user."""
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting and settings.app_env == "development" and current_user.id == 1:
        meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio file for meeting {meeting_id} channel '{channel}' not found",
        )

    channel_clean = channel.lower()
    filename = f"{meeting_id}_{channel_clean}.wav"
    file_path = settings.audio_storage_path / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio file for meeting {meeting_id} channel '{channel}' not found",
        )
    return FileResponse(path=file_path, media_type="audio/wav", filename=filename)


# ── Meeting Viewer Page ───────────────────────────────────

@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
async def view_meeting_page(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """HTML Viewer opened in a new tab when recording completes."""
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting and settings.app_env == "development" and current_user.id == 1:
        meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found or you do not have permission to view it",
        )
    title = html.escape(meeting.title or "Untitled Meeting")
    duration_val = meeting.duration_seconds if meeting.duration_seconds is not None else 0.0
    duration_str = f"{duration_val:.1f}s"
    status_str = meeting.status.value

    tab_url = f"/meetings/{meeting_id}/audio/tab"
    mic_url = f"/meetings/{meeting_id}/audio/mic"

    from sqlalchemy import select
    from backend.models.meeting import TranscriptSegment
    from backend.models.decision import Decision

    seg_res = await db.execute(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_ms)
    )
    segments = list(seg_res.scalars().all())

    dec_res = await db.execute(
        select(Decision)
        .where(Decision.meeting_id == meeting_id)
        .order_by(Decision.timestamp_ms)
    )
    decisions = list(dec_res.scalars().all())

    summary_html = ""
    if meeting and meeting.summary:
        summary_escaped = html.escape(meeting.summary)
        summary_html = f"""
        <div style="background: rgba(99, 102, 241, 0.08); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 14px; padding: 20px 22px; margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
                <span style="font-size: 0.82rem; font-weight: 700; background: rgba(99, 102, 241, 0.2); color: #a5b4fc; padding: 2px 8px; border-radius: 4px; text-transform: uppercase;">Summary</span>
                <span style="font-size: 1rem; font-weight: 700; color: #a5b4fc;">Meeting Summary & Key Takeaways</span>
            </div>
            <div style="font-size: 0.92rem; color: #e2e8f0; line-height: 1.6; white-space: pre-wrap;">{summary_escaped}</div>
        </div>
        """

    decisions_html = ""
    if decisions:
        item_rows = []
        for d in decisions:
            type_label = html.escape((d.type or "decision").replace("_", " ").title())
            type_bg = "rgba(56, 189, 248, 0.15)" if d.type == "decision" else ("rgba(244, 114, 182, 0.15)" if d.type == "action_item" else "rgba(251, 191, 36, 0.15)")
            type_color = "#38bdf8" if d.type == "decision" else ("#f472b6" if d.type == "action_item" else "#fbbf24")

            status_val = html.escape(d.status or "accepted")
            status_bg = "rgba(34, 197, 94, 0.15)" if d.status == "accepted" else ("rgba(239, 68, 68, 0.15)" if d.status == "rejected" else "rgba(245, 158, 11, 0.15)")
            status_color = "#4ade80" if d.status == "accepted" else ("#f87171" if d.status == "rejected" else "#fbbf24")

            ts_sec = (d.timestamp_ms or 0) / 1000.0
            ts_min = int(ts_sec // 60)
            ts_s = int(ts_sec % 60)
            time_str = f"{ts_min:02d}:{ts_s:02d}"
            d_text = html.escape(d.text or "")

            item_rows.append(f"""
            <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 10px; padding: 12px 16px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; gap: 14px;">
                <div style="display: flex; flex-direction: column; gap: 4px; flex: 1;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 0.72rem; font-weight: 700; background: {type_bg}; color: {type_color}; padding: 2px 8px; border-radius: 6px; text-transform: uppercase;">{type_label}</span>
                        <span style="font-size: 0.72rem; font-weight: 600; background: {status_bg}; color: {status_color}; padding: 2px 8px; border-radius: 6px; text-transform: uppercase;">{status_val}</span>
                        <span style="font-size: 0.72rem; color: #64748b; font-family: monospace;">Time: {time_str}</span>
                    </div>
                    <div style="font-size: 0.9rem; color: #f1f5f9; line-height: 1.4; margin-top: 2px;">{d_text}</div>
                </div>
            </div>
            """)

        decisions_html = f"""
        <div style="margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 1.05rem; font-weight: 600; color: #f1f5f9; display: flex; align-items: center; gap: 8px;">
                    <span>Decisions, Action Items & Client Requests</span>
                    <span style="font-size: 0.75rem; color: #94a3b8; font-weight: 400;">({len(decisions)} extracted)</span>
                </h3>
            </div>
            {"".join(item_rows)}
        </div>
        """

    transcript_html = ""
    if segments:
        segment_items = []
        for s in segments:
            is_user = "you" in (s.speaker or "").lower() or "mic" in (s.speaker or "").lower()
            speaker_color = "#c084fc" if is_user else "#38bdf8"
            speaker_bg = "rgba(192, 132, 252, 0.15)" if is_user else "rgba(56, 189, 248, 0.15)"
            speaker_border = "rgba(192, 132, 252, 0.3)" if is_user else "rgba(56, 189, 248, 0.3)"

            s_min = int(s.start_time // 60)
            s_sec = int(s.start_time % 60)
            e_min = int(s.end_time // 60)
            e_sec = int(s.end_time % 60)
            time_str = f"{s_min:02d}:{s_sec:02d} - {e_min:02d}:{e_sec:02d}"

            speaker_escaped = html.escape(s.speaker or "Speaker")
            seg_text_escaped = html.escape(s.text or "")

            segment_items.append(f"""
            <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 0.78rem; font-weight: 600; background: {speaker_bg}; color: {speaker_color}; border: 1px solid {speaker_border}; padding: 2px 8px; border-radius: 6px;">
                        {speaker_escaped}
                    </span>
                    <span style="font-size: 0.75rem; color: #64748b; font-family: monospace;">{time_str}</span>
                </div>
                <div style="font-size: 0.92rem; color: #f1f5f9; line-height: 1.5;">{seg_text_escaped}</div>
            </div>
            """)

        transcript_html = f"""
        <div style="margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 1.05rem; font-weight: 600; color: #f1f5f9; display: flex; align-items: center; gap: 8px;">
                    <span>Multi-Speaker Transcript</span>
                    <span style="font-size: 0.75rem; color: #94a3b8; font-weight: 400;">({len(segments)} segments merged)</span>
                </h3>
            </div>
            {"".join(segment_items)}
        </div>
        """
    elif meeting and meeting.status == MeetingStatus.RECORDING:
        transcript_html = """
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; color: #fca5a5; font-size: 0.88rem;">
            <div>
                <div style="font-weight: 600; color: #f87171; margin-bottom: 2px;">Live Audio Recording in Progress...</div>
                <div style="color: #94a3b8;">Capturing audio streams from Google Meet call. Click 'Stop Recording' in the extension popup when finished.</div>
            </div>
        </div>
        """
    elif meeting and meeting.status == MeetingStatus.PROCESSING:
        transcript_html = """
        <div style="background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; color: #7dd3fc; font-size: 0.88rem;">
            <div>
                <div style="font-weight: 600; color: #38bdf8; margin-bottom: 2px;">Processing Meeting (Transcription & AI Extraction)...</div>
                <div style="color: #94a3b8;">Running multi-speaker speech transcription, glossary cleanup, executive summary, and structured decisions extraction. Please refresh in a few moments.</div>
            </div>
        </div>
        """
    elif meeting and (meeting.status in (MeetingStatus.FAILED, MeetingStatus.ERROR) or meeting.error_message):
        err = html.escape(meeting.error_message or "Meeting processing failed")
        transcript_html = f"""
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.28); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; color: #fca5a5; font-size: 0.88rem;">
            <div style="font-weight: 600; color: #f87171; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                <span>Processing Error</span>
            </div>
            <div style="line-height: 1.5; color: #e2e8f0; font-family: monospace; font-size: 0.82rem; word-break: break-all; background: rgba(0,0,0,0.3); padding: 10px; border-radius: 6px; margin-top: 8px;">{err}</div>
        </div>
        """
    else:
        transcript_html = """
        <div style="background: rgba(255, 255, 255, 0.02); border: 1px dashed rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 24px 20px; margin-top: 24px; margin-bottom: 24px; text-align: center; color: #94a3b8; font-size: 0.88rem;">
            No speech transcript available for this recording yet.
        </div>
        """

    empty_notice = ""
    if duration_val <= 0.0:
        empty_notice = """
        <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 10px; padding: 16px 20px; margin-bottom: 24px; color: #fca5a5; font-size: 0.9rem; line-height: 1.5;">
            <div style="font-weight: 600; color: #f87171; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                <span>No Audio Data Captured (0.0s)</span>
            </div>
            <div>The recording finished without capturing audio samples. Common reasons:</div>
            <ul style="margin: 6px 0 0 20px;">
                <li>Microphone permission was not yet granted in the extension popup.</li>
                <li>No participant was speaking or tab audio was muted during capture.</li>
                <li>Extension was updated and needs to be reloaded at <code>chrome://extensions</code>.</li>
            </ul>
        </div>
        """

    rendered_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - DecisionAI</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: #090d16;
            color: #e2e8f0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 780px;
            width: 100%;
            background: rgba(18, 24, 38, 0.9);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 36px;
            box-shadow: 0 24px 50px rgba(0, 0, 0, 0.6);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.07);
            padding-bottom: 20px;
        }}
        h1 {{
            font-size: 1.65rem;
            font-weight: 700;
            background: linear-gradient(135deg, #c084fc, #38bdf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(34, 197, 94, 0.15);
            color: #4ade80;
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.82rem;
            font-weight: 600;
        }}
        .badge::before {{
            content: '';
            width: 8px;
            height: 8px;
            background: #4ade80;
            border-radius: 50%;
        }}
        .meta {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 14px;
            margin-bottom: 28px;
        }}
        .meta-card {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
            padding: 14px 16px;
        }}
        .meta-label {{
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #94a3b8;
            margin-bottom: 4px;
        }}
        .meta-value {{
            font-size: 1.05rem;
            font-weight: 600;
            color: #f1f5f9;
        }}
        .suite-banner {{
            background: linear-gradient(135deg, rgba(124, 58, 237, 0.15), rgba(14, 165, 233, 0.15));
            border: 1px solid rgba(168, 85, 247, 0.35);
            border-radius: 14px;
            padding: 18px 22px;
            margin-bottom: 24px;
        }}
        .suite-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 12px;
        }}
        .suite-title {{
            font-size: 1.05rem;
            font-weight: 700;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .suite-desc {{
            font-size: 0.82rem;
            color: #cbd5e1;
            line-height: 1.45;
        }}
        .suite-controls {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 10px;
        }}
        .btn-suite {{
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #e2e8f0;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn-suite:hover {{
            background: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.3);
            color: white;
            transform: translateY(-1px);
        }}
        .btn-suite.active {{
            background: linear-gradient(135deg, #7c3aed, #0284c7);
            border-color: #38bdf8;
            color: white;
            box-shadow: 0 0 16px rgba(56, 189, 248, 0.4);
        }}
        .btn-suite-ab {{
            background: rgba(245, 158, 11, 0.15);
            border: 1px solid rgba(245, 158, 11, 0.4);
            color: #fbbf24;
        }}
        .btn-suite-ab:hover {{
            background: rgba(245, 158, 11, 0.25);
            color: #fde68a;
        }}
        .channel-card {{
            background: rgba(255, 255, 255, 0.025);
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 14px;
            padding: 20px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }}
        .channel-card:focus-within, .channel-card:hover {{
            border-color: rgba(168, 85, 247, 0.35);
        }}
        .channel-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 12px;
        }}
        .channel-name {{
            font-size: 1.02rem;
            font-weight: 600;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .channel-badge {{
            font-size: 0.72rem;
            font-weight: 600;
            padding: 3px 9px;
            border-radius: 6px;
            background: rgba(99, 102, 241, 0.2);
            color: #a5b4fc;
            border: 1px solid rgba(99, 102, 241, 0.4);
        }}
        .pills-group {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-bottom: 14px;
        }}
        .pill {{
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #94a3b8;
            padding: 5px 12px;
            border-radius: 7px;
            font-size: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .pill:hover {{
            background: rgba(255, 255, 255, 0.08);
            color: #e2e8f0;
        }}
        .pill.active-studio {{
            background: rgba(99, 102, 241, 0.28);
            border-color: #818cf8;
            color: #c7d2fe;
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.3);
        }}
        .pill.active-max {{
            background: rgba(236, 72, 153, 0.28);
            border-color: #f472b6;
            color: #fbcfe8;
            box-shadow: 0 0 10px rgba(236, 72, 153, 0.3);
        }}
        .pill.active-ultra {{
            background: linear-gradient(135deg, #7c3aed, #db2777);
            border-color: #f472b6;
            color: #ffffff;
            box-shadow: 0 0 14px rgba(219, 39, 119, 0.4);
        }}
        .pill.active-bypass {{
            background: rgba(148, 163, 184, 0.15);
            border-color: #64748b;
            color: #94a3b8;
        }}
        .visualizer-canvas {{
            width: 100%;
            height: 32px;
            background: rgba(11, 15, 25, 0.6);
            border-radius: 6px;
            margin-bottom: 12px;
            display: block;
        }}
        audio {{
            width: 100%;
            height: 40px;
            border-radius: 8px;
            outline: none;
        }}
        .volume-row {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-top: 10px;
            font-size: 0.75rem;
            color: #94a3b8;
        }}
        .volume-row input[type="range"] {{
            flex: 1;
            accent-color: #a855f7;
            height: 4px;
            cursor: pointer;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            margin-top: 30px;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 10px 18px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 0.88rem;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.07);
            color: #cbd5e1;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.12);
            color: white;
            border-color: rgba(255, 255, 255, 0.16);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>{title}</h1>
                <p style="color: #64748b; font-size: 0.85rem; margin-top: 4px;">Meeting ID: #{meeting_id} · Recorded with DecisionAI</p>
            </div>
            <span class="badge">{status_str.upper()}</span>
        </div>

        <div class="meta">
            <div class="meta-card">
                <div class="meta-label">Duration</div>
                <div class="meta-value">{duration_str}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Audio Tracks</div>
                <div class="meta-value">2 (Tab + Mic)</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Audio Fidelity</div>
                <div class="meta-value">16 kHz Studio PCM</div>
            </div>
        </div>

        {empty_notice}

        <!-- Recorded Call Audio -->
        <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 20px; margin-bottom: 24px;">
            <h3 style="font-size: 1rem; font-weight: 600; color: #f1f5f9; margin-bottom: 16px;">Recorded Call Audio</h3>
            
            <div style="margin-bottom: 16px;">
                <div style="font-size: 0.85rem; font-weight: 500; color: #94a3b8; margin-bottom: 6px;">Call Participants (Tab Audio Track)</div>
                <audio id="tabAudio" controls preload="metadata" src="{tab_url}" style="width: 100%; height: 36px;"></audio>
            </div>

            <div>
                <div style="font-size: 0.85rem; font-weight: 500; color: #94a3b8; margin-bottom: 6px;">Your Voice (Microphone Audio Track)</div>
                <audio id="micAudio" controls preload="metadata" src="{mic_url}" style="width: 100%; height: 36px;"></audio>
            </div>
        </div>

        {summary_html}

        {decisions_html}

        {transcript_html}

        <div class="actions">
            <a href="/" class="btn btn-secondary">Back to Dashboard</a>
            <a href="{tab_url}" download="{meeting_id}_tab.wav" class="btn btn-secondary">Download Tab Audio (.wav)</a>
            <a href="{mic_url}" download="{meeting_id}_mic.wav" class="btn btn-secondary">Download Mic Audio (.wav)</a>
        </div>
    </div>

</body>
</html>"""
    return HTMLResponse(content=rendered_html)


# ── Meetings REST API (User Scoped) ───────────────────────

@router.get(
    "/api/meetings",
    response_model=MeetingListResponse,
    summary="List meetings for the current user",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def list_user_meetings(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingListResponse:
    meetings, total = await list_meetings(
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return MeetingListResponse(
        meetings=[MeetingResponse.model_validate(m) for m in meetings],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/api/meetings/{meeting_id}",
    response_model=MeetingResponse,
    summary="Get meeting details scoped to user",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def get_user_meeting(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return MeetingResponse.model_validate(meeting)


@router.patch(
    "/api/meetings/{meeting_id}",
    response_model=MeetingResponse,
    summary="Update meeting details (e.g. rename title)",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def update_user_meeting(
    request: Request,
    payload: MeetingUpdate,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    meeting = await update_meeting_title(
        db=db,
        meeting_id=meeting_id,
        user_id=current_user.id,
        title=payload.title.strip(),
    )
    if not meeting and settings.app_env == "development" and current_user.id == 1:
        m = await get_meeting_by_id(db, meeting_id)
        if m:
            m.title = payload.title.strip()
            await db.flush()
            await db.refresh(m)
            meeting = m
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    await log_audit_event(
        db=db,
        action="meeting_rename",
        user_id=current_user.id,
        target=f"meeting:{meeting_id}",
        request=request,
    )

    return MeetingResponse.model_validate(meeting)


@router.get(
    "/api/meetings/{meeting_id}/transcript",
    response_model=TranscriptResponse,
    summary="Get transcript segments for a meeting",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def get_meeting_transcript(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TranscriptResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting and settings.app_env == "development" and current_user.id == 1:
        meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    segments = await get_segments(db, meeting_id=meeting_id, user_id=meeting.user_id)
    return TranscriptResponse(
        meeting_id=meeting_id,
        segments=[TranscriptSegmentResponse.model_validate(s) for s in segments],
        total_segments=len(segments),
    )


@router.delete(
    "/api/meetings/{meeting_id}",
    summary="Delete a meeting scoped to user",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def delete_user_meeting(
    request: Request,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # 1. Clean up audio files using StorageService
    storage = get_storage_service(settings=settings)
    storage.delete_meeting_audio(
        user_id=current_user.id,
        meeting_id=meeting_id,
        audio_path=meeting.audio_path,
    )

    # 2. Clean up ChromaDB vector chunks
    try:
        vs = get_vector_store(settings=settings)
        vs.delete_meeting(meeting_id=meeting_id, user_id=current_user.id)
    except Exception:
        pass

    # 3. Delete database record
    await delete_meeting(db, meeting_id=meeting_id, user_id=current_user.id)

    # 4. Log audit event
    await log_audit_event(
        db=db,
        action="meeting_delete",
        user_id=current_user.id,
        target=f"meeting:{meeting_id}",
        request=request,
    )

    return {"status": "deleted", "meeting_id": str(meeting_id)}


# ── Meeting Exports (Plain Text and JSON) ──────────────────

@router.get(
    "/api/meetings/{meeting_id}/export/txt",
    summary="Export meeting transcript as plain text",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def export_transcript_txt(
    request: Request,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    segments = await get_segments(db, meeting_id=meeting_id, user_id=current_user.id)

    lines = [
        f"Meeting Title: {meeting.title}",
        f"Meeting Date: {meeting.created_at.isoformat() if meeting.created_at else 'Unknown'}",
        f"Duration: {meeting.duration_seconds or 0:.1f}s",
        "",
        "=== SUMMARY ===",
        meeting.summary or "No summary available.",
        "",
        "=== TRANSCRIPT ===",
    ]
    for s in segments:
        m = int(s.start_time // 60)
        sec = int(s.start_time % 60)
        speaker = s.speaker or "Speaker"
        lines.append(f"[{m:02d}:{sec:02d}] {speaker}: {s.text}")

    content = "\n".join(lines)

    await log_audit_event(
        db=db,
        action="export_txt",
        user_id=current_user.id,
        target=f"meeting:{meeting_id}",
        request=request,
    )

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="meeting_{meeting_id}_transcript.txt"'},
    )


@router.get(
    "/api/meetings/{meeting_id}/export/json",
    summary="Export meeting transcript and decisions as JSON",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def export_transcript_json(
    request: Request,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    segments = await get_segments(db, meeting_id=meeting_id, user_id=current_user.id)
    decisions = await list_decisions(db, meeting_id=meeting_id, user_id=current_user.id)

    export_data = {
        "meeting": {
            "id": meeting.id,
            "title": meeting.title,
            "status": meeting.status.value,
            "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
            "duration_seconds": meeting.duration_seconds,
            "summary": meeting.summary,
            "consent_recorded": meeting.consent_recorded,
        },
        "decisions": [DecisionResponse.model_validate(d).model_dump(mode="json") for d in decisions],
        "transcript": [TranscriptSegmentResponse.model_validate(s).model_dump(mode="json") for s in segments],
    }

    await log_audit_event(
        db=db,
        action="export_json",
        user_id=current_user.id,
        target=f"meeting:{meeting_id}",
        request=request,
    )

    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f'attachment; filename="meeting_{meeting_id}_export.json"'},
    )


# ── Decisions REST API (User Scoped) ──────────────────────

@router.get(
    "/api/meetings/{meeting_id}/decisions",
    response_model=DecisionListResponse,
    summary="List decisions and action items for a meeting",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def list_meeting_decisions(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionListResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    decisions = await list_decisions(db, meeting_id=meeting_id, user_id=current_user.id)
    return DecisionListResponse(
        meeting_id=meeting_id,
        decisions=[DecisionResponse.model_validate(d) for d in decisions],
        total=len(decisions),
    )


@router.post(
    "/api/meetings/{meeting_id}/decisions",
    response_model=DecisionResponse,
    summary="Add a decision or action item to a meeting",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def create_meeting_decision(
    payload: DecisionCreate,
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DecisionResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    decision = await create_decision(
        db=db,
        meeting_id=meeting_id,
        text=payload.text,
        type=payload.type,
        status=payload.status,
        timestamp_ms=payload.timestamp_ms,
    )
    return DecisionResponse.model_validate(decision)


@router.delete(
    "/api/meetings/{meeting_id}/decisions/{decision_id}",
    summary="Delete a decision",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def delete_meeting_decision(
    meeting_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer meeting ID"),
    decision_id: int = Path(..., ge=1, le=2_147_483_647, description="Positive integer decision ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    deleted = await delete_decision(db, decision_id=decision_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    return {"status": "deleted", "decision_id": str(decision_id)}


