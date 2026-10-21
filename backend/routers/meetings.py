"""
Meeting API routes — handles audio upload, live audio streaming, and meeting viewer.
"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
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
    list_meetings,
    update_meeting_audio_path,
)
from backend.crud.users import create_user, get_user_by_id
from backend.database import async_session_maker, get_db
from backend.models.meeting import MeetingStatus
from backend.models.user import User
from backend.schemas.decision import DecisionCreate, DecisionListResponse, DecisionResponse
from backend.schemas.meeting import MeetingCreate, MeetingListResponse, MeetingResponse
from backend.services.audio_streamer import (
    finish_writer,
    get_or_create_writer,
    parse_audio_chunk,
)
from backend.services.auth import decode_token
from backend.services.rate_limiter import rate_limit
from backend.services.storage import (
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
    payload: MeetingCreate | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    """
    Create a meeting record ahead of live WebSocket streaming, scoped to current_user.
    """
    title = payload.title if payload and payload.title else "Google Meet Call"
    meeting = await create_meeting(
        db=db,
        user_id=current_user.id,
        title=title,
        status=MeetingStatus.RECORDING,
    )
    return MeetingResponse.model_validate(meeting)



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
    actual_file = file or audio_file
    if actual_file is None:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail="An audio file must be provided under field 'file' or 'audio_file'",
        )

    clean_title = title.strip() if title and title.strip() else "Untitled Meeting"
    ext = validate_audio_file(actual_file)

    meeting = await create_meeting(
        db=db,
        user_id=current_user.id,
        title=clean_title,
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
    elif settings.app_env == "development":
        user_id = 1
    else:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    # Verify meeting ownership if meeting already exists in database
    async with async_session_maker() as db:
        meeting = await get_meeting_by_id(db, meeting_id)
        if meeting and meeting.user_id != user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized meeting access")
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
    meeting_id: int,
    background_tasks: BackgroundTasks,
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to finish this meeting",
        )

    tab_path, mic_path, duration = finish_writer(meeting_id, settings)

    if not meeting:
        meeting = await create_meeting(db, user_id=current_user.id, title="Google Meet Call", status=MeetingStatus.PROCESSING)

    # Launch background transcription & LLM post-processing task if audio files exist
    if duration > 0.0 or (tab_path.exists() and tab_path.stat().st_size > 44):
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
    meeting_id: int,
    channel: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Serve recorded WAV file for a channel (tab or mic), scoped to current_user."""
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
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
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """HTML Viewer opened in a new tab when recording completes."""
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found or you do not have permission to view it",
        )
    title = meeting.title
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
        summary_html = f"""
        <div style="background: rgba(99, 102, 241, 0.08); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 14px; padding: 20px 22px; margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
                <span style="font-size: 1.2rem;">✨</span>
                <span style="font-size: 1rem; font-weight: 700; color: #a5b4fc;">Meeting Summary & Key Takeaways</span>
            </div>
            <div style="font-size: 0.92rem; color: #e2e8f0; line-height: 1.6; white-space: pre-wrap;">{meeting.summary}</div>
        </div>
        """

    decisions_html = ""
    if decisions:
        item_rows = []
        for d in decisions:
            type_label = (d.type or "decision").replace("_", " ").title()
            type_bg = "rgba(56, 189, 248, 0.15)" if d.type == "decision" else ("rgba(244, 114, 182, 0.15)" if d.type == "action_item" else "rgba(251, 191, 36, 0.15)")
            type_color = "#38bdf8" if d.type == "decision" else ("#f472b6" if d.type == "action_item" else "#fbbf24")

            status_val = d.status or "accepted"
            status_bg = "rgba(34, 197, 94, 0.15)" if status_val == "accepted" else ("rgba(239, 68, 68, 0.15)" if status_val == "rejected" else "rgba(245, 158, 11, 0.15)")
            status_color = "#4ade80" if status_val == "accepted" else ("#f87171" if status_val == "rejected" else "#fbbf24")

            ts_sec = (d.timestamp_ms or 0) / 1000.0
            ts_min = int(ts_sec // 60)
            ts_s = int(ts_sec % 60)
            time_str = f"{ts_min:02d}:{ts_s:02d}"

            item_rows.append(f"""
            <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 10px; padding: 12px 16px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; gap: 14px;">
                <div style="display: flex; flex-direction: column; gap: 4px; flex: 1;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 0.72rem; font-weight: 700; background: {type_bg}; color: {type_color}; padding: 2px 8px; border-radius: 6px; text-transform: uppercase;">{type_label}</span>
                        <span style="font-size: 0.72rem; font-weight: 600; background: {status_bg}; color: {status_color}; padding: 2px 8px; border-radius: 6px; text-transform: uppercase;">{status_val}</span>
                        <span style="font-size: 0.72rem; color: #64748b; font-family: monospace;">⏱️ {time_str}</span>
                    </div>
                    <div style="font-size: 0.9rem; color: #f1f5f9; line-height: 1.4; margin-top: 2px;">{d.text}</div>
                </div>
            </div>
            """)

        decisions_html = f"""
        <div style="margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 1.05rem; font-weight: 600; color: #f1f5f9; display: flex; align-items: center; gap: 8px;">
                    <span>🎯 Decisions, Action Items & Client Requests</span>
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

            segment_items.append(f"""
            <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 0.78rem; font-weight: 600; background: {speaker_bg}; color: {speaker_color}; border: 1px solid {speaker_border}; padding: 2px 8px; border-radius: 6px;">
                        {s.speaker or "Speaker"}
                    </span>
                    <span style="font-size: 0.75rem; color: #64748b; font-family: monospace;">{time_str}</span>
                </div>
                <div style="font-size: 0.92rem; color: #f1f5f9; line-height: 1.5;">{s.text}</div>
            </div>
            """)

        transcript_html = f"""
        <div style="margin-top: 24px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 1.05rem; font-weight: 600; color: #f1f5f9; display: flex; align-items: center; gap: 8px;">
                    <span>📝 Multi-Speaker Transcript</span>
                    <span style="font-size: 0.75rem; color: #94a3b8; font-weight: 400;">({len(segments)} segments merged)</span>
                </h3>
            </div>
            {"".join(segment_items)}
        </div>
        """
    elif meeting and meeting.status == MeetingStatus.RECORDING:
        transcript_html = """
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; color: #fca5a5; font-size: 0.88rem;">
            <span style="font-size: 1.4rem;">🔴</span>
            <div>
                <div style="font-weight: 600; color: #f87171; margin-bottom: 2px;">Live Audio Recording in Progress...</div>
                <div style="color: #94a3b8;">Capturing audio streams from Google Meet call. Click 'Stop Recording' in the extension popup when finished.</div>
            </div>
        </div>
        """
    elif meeting and meeting.status == MeetingStatus.PROCESSING:
        transcript_html = """
        <div style="background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; color: #7dd3fc; font-size: 0.88rem;">
            <span style="font-size: 1.4rem;">⏳</span>
            <div>
                <div style="font-weight: 600; color: #38bdf8; margin-bottom: 2px;">Processing Meeting (Transcription & AI Extraction)...</div>
                <div style="color: #94a3b8;">Running multi-speaker speech transcription, glossary cleanup, executive summary, and structured decisions extraction. Please refresh in a few moments.</div>
            </div>
        </div>
        """
    elif meeting and (meeting.status in (MeetingStatus.FAILED, MeetingStatus.ERROR) or meeting.error_message):
        err = meeting.error_message or "Meeting processing failed"
        transcript_html = f"""
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.28); border-radius: 12px; padding: 18px 20px; margin-top: 24px; margin-bottom: 24px; color: #fca5a5; font-size: 0.88rem;">
            <div style="font-weight: 600; color: #f87171; margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                <span>⚠️</span> <span>Processing Error</span>
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
                <span>⚠️</span> <span>No Audio Data Captured (0.0s)</span>
            </div>
            <div>The recording finished without capturing audio samples. Common reasons:</div>
            <ul style="margin: 6px 0 0 20px;">
                <li>Microphone permission was not yet granted in the extension popup.</li>
                <li>No participant was speaking or tab audio was muted during capture.</li>
                <li>Extension was updated and needs to be reloaded at <code>chrome://extensions</code>.</li>
            </ul>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — DecisionAI</title>
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

        <!-- Broadcast Vocal Clarity Suite Master Bar -->
        <div class="suite-banner">
            <div class="suite-header">
                <div>
                    <div class="suite-title">✨ Broadcast Vocal Clarity Suite</div>
                    <div class="suite-desc">
                        7-stage real-time DSP: removes room rumble & mud notch (-6dB), boosts consonant presence (+8 to +16dB), restores brilliance air, and evens out quiet speakers.
                    </div>
                </div>
            </div>
            <div class="suite-controls">
                <button class="btn-suite active" id="masterStudioBtn" onclick="setMasterPreset(1)" type="button">
                    <span>💎 Studio (+8dB)</span>
                </button>
                <button class="btn-suite" id="masterMaxBtn" onclick="setMasterPreset(2)" type="button">
                    <span>🚀 Max Clarity (+13dB)</span>
                </button>
                <button class="btn-suite" id="masterUltraBtn" onclick="setMasterPreset(3)" type="button">
                    <span>⚡ Ultra Punch (+16dB)</span>
                </button>
                <button class="btn-suite" id="masterBypassBtn" onclick="setMasterPreset(0)" type="button">
                    <span>🔈 Raw Bypass</span>
                </button>
                <button class="btn-suite btn-suite-ab" id="abCompareBtn" onclick="toggleABCompare()" type="button" title="Quickly switch between enhanced audio and original raw muffled audio to hear the difference">
                    <span>🎧 Instant A/B Compare</span>
                </button>
            </div>
        </div>

        <!-- Channel 1: Tab Audio (Participants) -->
        <div class="channel-card">
            <div class="channel-top">
                <div class="channel-name">
                    <span>🔊 Participant Voices (Google Meet Tab)</span>
                </div>
                <span id="tabBadge" class="channel-badge">💎 Studio (+8dB) Active</span>
            </div>
            <div class="pills-group" id="tabPills">
                <button class="pill active-studio" onclick="setChannelPreset('tabAudio', 1)" type="button">💎 Studio (+8dB)</button>
                <button class="pill" onclick="setChannelPreset('tabAudio', 2)" type="button">🚀 Max (+13dB)</button>
                <button class="pill" onclick="setChannelPreset('tabAudio', 3)" type="button">⚡ Ultra (+16dB)</button>
                <button class="pill" onclick="setChannelPreset('tabAudio', 0)" type="button">🔈 Raw (Muffled)</button>
            </div>
            <canvas id="tabVisualizer" class="visualizer-canvas" width="600" height="32"></canvas>
            <audio id="tabAudio" controls preload="metadata" src="{tab_url}"></audio>
            <div class="volume-row">
                <span>🔊 Vocal Level Boost:</span>
                <input type="range" id="tabVolume" min="0.5" max="2.5" step="0.05" value="1.0" oninput="setChannelVolume('tabAudio', this.value, 'tabVolVal')">
                <span id="tabVolVal" style="min-width: 48px; text-align: right;">100%</span>
            </div>
        </div>

        <!-- Channel 2: Microphone (Your Voice) -->
        <div class="channel-card">
            <div class="channel-top">
                <div class="channel-name">
                    <span>🎙️ Your Voice (Microphone Track)</span>
                </div>
                <span id="micBadge" class="channel-badge">💎 Studio (+8dB) Active</span>
            </div>
            <div class="pills-group" id="micPills">
                <button class="pill active-studio" onclick="setChannelPreset('micAudio', 1)" type="button">💎 Studio (+8dB)</button>
                <button class="pill" onclick="setChannelPreset('micAudio', 2)" type="button">🚀 Max (+13dB)</button>
                <button class="pill" onclick="setChannelPreset('micAudio', 3)" type="button">⚡ Ultra (+16dB)</button>
                <button class="pill" onclick="setChannelPreset('micAudio', 0)" type="button">🔈 Raw (Muffled)</button>
            </div>
            <canvas id="micVisualizer" class="visualizer-canvas" width="600" height="32"></canvas>
            <audio id="micAudio" controls preload="metadata" src="{mic_url}"></audio>
            <div class="volume-row">
                <span>🎙️ Vocal Level Boost:</span>
                <input type="range" id="micVolume" min="0.5" max="2.5" step="0.05" value="1.0" oninput="setChannelVolume('micAudio', this.value, 'micVolVal')">
                <span id="micVolVal" style="min-width: 48px; text-align: right;">100%</span>
            </div>
        </div>

        {summary_html}

        {decisions_html}

        {transcript_html}

        <div class="actions">
            <a href="/" class="btn btn-secondary">← Back to Dashboard</a>
            <a href="{tab_url}" download="{meeting_id}_tab.wav" class="btn btn-secondary">Download Tab Audio (.wav)</a>
            <a href="{mic_url}" download="{meeting_id}_mic.wav" class="btn btn-secondary">Download Mic Audio (.wav)</a>
        </div>
    </div>

    <script>
        // 7-Stage Broadcast Vocal Clarity Filter Presets
        const PRESETS = {{
            0: {{
                name: "Raw (Bypass)",
                badge: "🔈 Raw Bypass (Muffled)",
                lowCut: 20,
                mudGain: 0,
                nasalGain: 0,
                presenceGain: 0,
                airGain: 0,
                compRatio: 1,
                makeupGain: 1.0,
            }},
            1: {{
                name: "Studio (+8dB)",
                badge: "💎 Studio (+8dB) Active",
                lowCut: 130,       // Cuts AC hum, desk vibration, proximity rumble
                mudGain: -5.0,     // Eliminates cardboard box / closet muffling at 380Hz
                nasalGain: -2.5,   // Smooths nasal congestion at 850Hz
                presenceGain: 8.5, // Consonant articulation at 3.4kHz
                airGain: 5.5,      // Crisp brilliance at 5.2kHz
                compThreshold: -24,// Broadcast leveler
                compRatio: 3.5,
                makeupGain: 1.5,   // Output energy boost (+3.5dB)
            }},
            2: {{
                name: "Max (+13dB)",
                badge: "🚀 Max (+13dB) Active",
                lowCut: 160,
                mudGain: -7.5,
                nasalGain: -4.0,
                presenceGain: 13.0,
                airGain: 9.0,
                compThreshold: -28,
                compRatio: 5.0,
                makeupGain: 2.2,   // (+6.8dB)
            }},
            3: {{
                name: "Ultra (+16dB)",
                badge: "⚡ Ultra (+16dB) Active",
                lowCut: 190,
                mudGain: -10.0,
                nasalGain: -5.0,
                presenceGain: 16.0,
                airGain: 12.0,
                compThreshold: -32,
                compRatio: 7.0,
                makeupGain: 3.0,   // (+9.5dB)
            }}
        }};

        let webAudioCtx = null;
        const channels = new Map();
        let lastEnhancedPreset = 1;
        let isABRaw = false;

        function getOrCreateAudioContext() {{
            if (!webAudioCtx) {{
                webAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }}
            if (webAudioCtx.state === 'suspended') {{
                webAudioCtx.resume();
            }}
            return webAudioCtx;
        }}

        function getOrCreateChain(audioId) {{
            const ctx = getOrCreateAudioContext();
            if (channels.has(audioId)) {{
                return channels.get(audioId);
            }}

            const audio = document.getElementById(audioId);
            if (!audio) return null;

            try {{
                const source = ctx.createMediaElementSource(audio);

                // Stage 1: Subsonic & Rumble Filter
                const lowCut = ctx.createBiquadFilter();
                lowCut.type = 'highpass';
                lowCut.frequency.value = 20;
                lowCut.Q.value = 0.8;

                // Stage 2: Cardboard Box / Mud Notch (380 Hz)
                const mudNotch = ctx.createBiquadFilter();
                mudNotch.type = 'peaking';
                mudNotch.frequency.value = 380;
                mudNotch.Q.value = 1.2;
                mudNotch.gain.value = 0;

                // Stage 3: Nasal Resonance De-muffler (850 Hz)
                const nasalNotch = ctx.createBiquadFilter();
                nasalNotch.type = 'peaking';
                nasalNotch.frequency.value = 850;
                nasalNotch.Q.value = 1.4;
                nasalNotch.gain.value = 0;

                // Stage 4: Consonant Speech Articulation (3,400 Hz)
                const consonantBoost = ctx.createBiquadFilter();
                consonantBoost.type = 'peaking';
                consonantBoost.frequency.value = 3400;
                consonantBoost.Q.value = 1.1;
                consonantBoost.gain.value = 0;

                // Stage 5: Vocal Brilliance & Air Shelf (5,200 Hz)
                const airShelf = ctx.createBiquadFilter();
                airShelf.type = 'highshelf';
                airShelf.frequency.value = 5200;
                airShelf.gain.value = 0;

                // Stage 6: Broadcast Dynamic Leveler
                const leveler = ctx.createDynamicsCompressor();
                leveler.threshold.value = -24;
                leveler.knee.value = 10;
                leveler.ratio.value = 1;
                leveler.attack.value = 0.003;
                leveler.release.value = 0.15;

                // Stage 7: Makeup Gain Node
                const makeupGain = ctx.createGain();
                makeupGain.gain.value = 1.0;

                // User Volume Slider Gain Node
                const userGain = ctx.createGain();
                userGain.gain.value = 1.0;

                // Analyser Node for Live Visualizer
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 64;
                analyser.smoothingTimeConstant = 0.8;

                // Connect Chain: source -> lowCut -> mudNotch -> nasalNotch -> consonantBoost -> airShelf -> leveler -> makeupGain -> userGain -> analyser -> destination
                source.connect(lowCut);
                lowCut.connect(mudNotch);
                mudNotch.connect(nasalNotch);
                nasalNotch.connect(consonantBoost);
                consonantBoost.connect(airShelf);
                airShelf.connect(leveler);
                leveler.connect(makeupGain);
                makeupGain.connect(userGain);
                userGain.connect(analyser);
                analyser.connect(ctx.destination);

                const chain = {{
                    audioId,
                    audio,
                    source,
                    lowCut,
                    mudNotch,
                    nasalNotch,
                    consonantBoost,
                    airShelf,
                    leveler,
                    makeupGain,
                    userGain,
                    analyser,
                    mode: 1, // Default to Studio Mode
                }};
                channels.set(audioId, chain);

                // Start visualizer loop for this channel
                startVisualizer(audioId, analyser);

                // Apply initial Studio preset
                applyPresetToChain(chain, 1);
                return chain;
            }} catch (e) {{
                console.warn("Could not setup audio clarity chain for", audioId, e);
                return null;
            }}
        }}

        function applyPresetToChain(chain, mode) {{
            if (!chain) return;
            const p = PRESETS[mode];
            if (!p) return;
            chain.mode = mode;

            const t = webAudioCtx ? webAudioCtx.currentTime : 0;
            const ramptime = 0.04;

            chain.lowCut.frequency.setTargetAtTime(p.lowCut, t, ramptime);
            chain.mudNotch.gain.setTargetAtTime(p.mudGain, t, ramptime);
            chain.nasalNotch.gain.setTargetAtTime(p.nasalGain, t, ramptime);
            chain.consonantBoost.gain.setTargetAtTime(p.presenceGain, t, ramptime);
            chain.airShelf.gain.setTargetAtTime(p.airGain, t, ramptime);

            if (mode === 0) {{
                chain.leveler.ratio.setTargetAtTime(1, t, ramptime);
            }} else {{
                chain.leveler.threshold.setTargetAtTime(p.compThreshold, t, ramptime);
                chain.leveler.ratio.setTargetAtTime(p.compRatio, t, ramptime);
            }}
            chain.makeupGain.gain.setTargetAtTime(p.makeupGain, t, ramptime);
        }}

        function setChannelPreset(audioId, mode) {{
            getOrCreateAudioContext();
            const chain = getOrCreateChain(audioId);
            if (chain) {{
                applyPresetToChain(chain, mode);
            }}
            if (mode > 0) {{
                lastEnhancedPreset = mode;
                isABRaw = false;
            }}
            updateChannelUI(audioId, mode);
        }}

        function updateChannelUI(audioId, mode) {{
            const isTab = audioId === 'tabAudio';
            const badge = document.getElementById(isTab ? 'tabBadge' : 'micBadge');
            const pillsContainer = document.getElementById(isTab ? 'tabPills' : 'micPills');
            const p = PRESETS[mode];

            if (badge && p) {{
                badge.innerText = p.badge;
                badge.style.color = mode === 1 ? '#a5b4fc' : (mode === 2 ? '#f472b6' : (mode === 3 ? '#ec4899' : '#94a3b8'));
            }}

            if (pillsContainer) {{
                const pills = pillsContainer.querySelectorAll('.pill');
                const classes = ['active-studio', 'active-max', 'active-ultra', 'active-bypass'];
                pills.forEach((pill, idx) => {{
                    classes.forEach(c => pill.classList.remove(c));
                    const pillMode = idx === 0 ? 1 : (idx === 1 ? 2 : (idx === 2 ? 3 : 0));
                    if (pillMode === mode) {{
                        if (mode === 1) pill.classList.add('active-studio');
                        else if (mode === 2) pill.classList.add('active-max');
                        else if (mode === 3) pill.classList.add('active-ultra');
                        else pill.classList.add('active-bypass');
                    }}
                }});
            }}
        }}

        function setMasterPreset(mode) {{
            getOrCreateAudioContext();
            setChannelPreset('tabAudio', mode);
            setChannelPreset('micAudio', mode);

            // Update master buttons
            ['masterStudioBtn', 'masterMaxBtn', 'masterUltraBtn', 'masterBypassBtn'].forEach((id, idx) => {{
                const btn = document.getElementById(id);
                if (btn) {{
                    const btnMode = idx === 0 ? 1 : (idx === 1 ? 2 : (idx === 2 ? 3 : 0));
                    if (btnMode === mode) btn.classList.add('active');
                    else btn.classList.remove('active');
                }}
            }});
        }}

        function toggleABCompare() {{
            getOrCreateAudioContext();
            const abBtn = document.getElementById('abCompareBtn');
            if (isABRaw) {{
                // Switch back to enhanced
                setMasterPreset(lastEnhancedPreset);
                isABRaw = false;
                if (abBtn) {{
                    abBtn.innerHTML = '<span>🎧 Instant A/B: Enhanced Active</span>';
                    abBtn.style.borderColor = '#10b981';
                    abBtn.style.color = '#34d399';
                }}
            }} else {{
                // Switch to raw muffled
                setMasterPreset(0);
                isABRaw = true;
                if (abBtn) {{
                    abBtn.innerHTML = '<span>🔈 Instant A/B: Raw Muffled Active</span>';
                    abBtn.style.borderColor = '#ef4444';
                    abBtn.style.color = '#f87171';
                }}
            }}
        }}

        function setChannelVolume(audioId, val, labelId) {{
            const chain = getOrCreateChain(audioId);
            const num = parseFloat(val);
            if (chain && webAudioCtx) {{
                chain.userGain.gain.setTargetAtTime(num, webAudioCtx.currentTime, 0.02);
            }}
            const label = document.getElementById(labelId);
            if (label) {{
                const pct = Math.round(num * 100);
                label.innerText = pct + '%';
            }}
        }}

        function startVisualizer(audioId, analyser) {{
            const canvasId = audioId === 'tabAudio' ? 'tabVisualizer' : 'micVisualizer';
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            const bufferLength = analyser.frequencyBinCount;
            const dataArray = new Uint8Array(bufferLength);

            function render() {{
                requestAnimationFrame(render);
                analyser.getByteFrequencyData(dataArray);

                ctx.clearRect(0, 0, canvas.width, canvas.height);
                const barCount = 24;
                const barWidth = (canvas.width / barCount) - 2;

                for (let i = 0; i < barCount; i++) {{
                    const binIdx = Math.floor((i / barCount) * (bufferLength / 2));
                    const val = dataArray[binIdx] || 0;
                    const percent = val / 255;
                    const barHeight = Math.max(2, percent * canvas.height);
                    const x = i * (barWidth + 2);
                    const y = canvas.height - barHeight;

                    // Neon gradient based on channel
                    const grad = ctx.createLinearGradient(0, y, 0, canvas.height);
                    if (audioId === 'tabAudio') {{
                        grad.addColorStop(0, '#38bdf8');
                        grad.addColorStop(1, '#6366f1');
                    }} else {{
                        grad.addColorStop(0, '#f472b6');
                        grad.addColorStop(1, '#a855f7');
                    }}

                    ctx.fillStyle = grad;
                    ctx.fillRect(x, y, barWidth, barHeight);
                }}
            }}
            render();
        }}

        // Auto-initialize when user presses play on either audio element or clicks on page
        ['tabAudio', 'micAudio'].forEach(id => {{
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('play', () => {{
                getOrCreateAudioContext();
                getOrCreateChain(id);
            }});
        }});

        window.addEventListener('click', () => {{
            if (webAudioCtx && webAudioCtx.state === 'suspended') {{
                webAudioCtx.resume();
            }}
        }}, {{ once: true }});
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)


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
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingResponse:
    meeting = await get_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return MeetingResponse.model_validate(meeting)


@router.delete(
    "/api/meetings/{meeting_id}",
    summary="Delete a meeting scoped to user",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def delete_user_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    deleted = await delete_meeting(db, meeting_id=meeting_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return {"status": "deleted", "meeting_id": str(meeting_id)}


# ── Decisions REST API (User Scoped) ──────────────────────

@router.get(
    "/api/meetings/{meeting_id}/decisions",
    response_model=DecisionListResponse,
    summary="List decisions and action items for a meeting",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def list_meeting_decisions(
    meeting_id: int,
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
    meeting_id: int,
    payload: DecisionCreate,
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
    meeting_id: int,
    decision_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    deleted = await delete_decision(db, decision_id=decision_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    return {"status": "deleted", "decision_id": str(decision_id)}

