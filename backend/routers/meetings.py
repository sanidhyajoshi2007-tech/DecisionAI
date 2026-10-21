"""
Meeting API routes — handles audio upload, live audio streaming, and meeting viewer.
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import Settings, get_settings
from backend.crud.meetings import (
    create_meeting,
    delete_meeting,
    get_meeting_by_id,
    update_meeting_audio_path,
)
from backend.crud.users import create_user, get_user_by_id
from backend.database import get_db
from backend.models.meeting import MeetingStatus
from backend.models.user import User
from backend.schemas.meeting import MeetingCreate, MeetingResponse
from backend.services.audio_streamer import (
    finish_writer,
    get_or_create_writer,
    parse_audio_chunk,
)
from backend.services.storage import (
    save_audio_file,
    validate_audio_file,
)

router = APIRouter(tags=["meetings"])


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Helper to ensure a dev user exists when auth is skipped."""
    user = await get_user_by_id(db, 1)
    if user is None:
        user = await create_user(db, email="dev@decisionai.local", name="Dev User")
    return user


# ── Create Meeting (Live Streaming) ───────────────────────

@router.post(
    "/meetings",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a new meeting for recording",
)
@router.post(
    "/api/meetings",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def create_new_meeting(
    payload: MeetingCreate | None = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeetingResponse:
    """
    Create a meeting record ahead of live WebSocket streaming.
    Skips authentication for prototype/extension ease of use.
    """
    dev_user = await _get_or_create_dev_user(db)
    title = payload.title if payload and payload.title else "Google Meet Call"
    meeting = await create_meeting(
        db=db,
        user_id=dev_user.id,
        title=title,
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
    Saves tab and mic audio to separate WAV files in audio_storage.
    """
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
)
@router.post(
    "/api/meetings/{meeting_id}/finish",
    include_in_schema=False,
)
async def finish_meeting_recording(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Called when recording stops. Closes writers and updates meeting status to 'ready'.
    """
    tab_path, mic_path, duration = finish_writer(meeting_id, settings)

    meeting = await get_meeting_by_id(db, meeting_id)
    if not meeting:
        dev_user = await _get_or_create_dev_user(db)
        meeting = await create_meeting(db, user_id=dev_user.id, title="Google Meet Call")

    meeting.status = MeetingStatus.READY
    meeting.duration_seconds = duration
    meeting.audio_path = f"{meeting_id}_tab.wav"
    await db.flush()
    await db.refresh(meeting)

    return {
        "status": "ready",
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
    settings: Settings = Depends(get_settings),
):
    """Serve recorded WAV file for a channel (tab or mic)."""
    channel_clean = channel.lower()
    filename = f"{meeting_id}_{channel_clean}.wav"
    file_path = settings.audio_storage_path / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio file for meeting {meeting_id} channel '{channel}' not found",
        )
    return FileResponse(path=file_path, media_type="audio/wav", filename=filename)


# ── Meeting Viewer Page ───────────────────────────────────

@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
async def view_meeting_page(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """HTML Viewer opened in a new tab when recording completes."""
    meeting = await get_meeting_by_id(db, meeting_id)
    title = meeting.title if meeting else f"Meeting #{meeting_id}"
    duration_str = f"{meeting.duration_seconds:.1f}s" if meeting and meeting.duration_seconds else "Recorded"
    status_str = meeting.status.value if meeting else "ready"

    tab_url = f"/meetings/{meeting_id}/audio/tab"
    mic_url = f"/meetings/{meeting_id}/audio/mic"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — DecisionAI</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', system-ui, sans-serif;
            background: #0b0c10;
            color: #e2e8f0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 720px;
            width: 100%;
            background: rgba(22, 27, 34, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 36px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding-bottom: 20px;
        }}
        h1 {{
            font-size: 1.6rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a78bfa, #38bdf8);
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
            font-size: 0.85rem;
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
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 28px;
        }}
        .meta-card {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            padding: 14px 18px;
        }}
        .meta-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94a3b8;
            margin-bottom: 4px;
        }}
        .meta-value {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #f1f5f9;
        }}
        .section-title {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            color: #cbd5e1;
        }}
        .channel-card {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
            padding: 18px;
            margin-bottom: 16px;
        }}
        .channel-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
            font-weight: 500;
        }}
        .channel-desc {{
            font-size: 0.85rem;
            color: #94a3b8;
        }}
        audio {{
            width: 100%;
            height: 38px;
            margin-top: 8px;
            border-radius: 8px;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            margin-top: 28px;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 10px 18px;
            border-radius: 8px;
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.08);
            color: #cbd5e1;
        }}
        .btn-secondary:hover {{ background: rgba(255, 255, 255, 0.12); color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>{title}</h1>
                <p style="color: #64748b; font-size: 0.85rem; margin-top: 4px;">Meeting ID: #{meeting_id}</p>
            </div>
            <span class="badge">{status_str.upper()}</span>
        </div>

        <div class="meta">
            <div class="meta-card">
                <div class="meta-label">Recorded Duration</div>
                <div class="meta-value">{duration_str}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Channels Captured</div>
                <div class="meta-value">2 (Tab + Mic)</div>
            </div>
        </div>

        <h2 class="section-title">Audio Streams (16 kHz 16-bit PCM WAV)</h2>

        <div class="channel-card">
            <div class="channel-header">
                <span>🔊 Tab Audio (Other Participants)</span>
                <span class="channel-desc">Channel 1 · 16 kHz Mono</span>
            </div>
            <audio controls preload="metadata" src="{tab_url}"></audio>
        </div>

        <div class="channel-card">
            <div class="channel-header">
                <span>🎙️ Microphone (Your Voice)</span>
                <span class="channel-desc">Channel 2 · 16 kHz Mono</span>
            </div>
            <audio controls preload="metadata" src="{mic_url}"></audio>
        </div>

        <div class="actions">
            <a href="/" class="btn btn-secondary">← Back to Dashboard</a>
            <a href="{tab_url}" download="{meeting_id}_tab.wav" class="btn btn-secondary">Download Tab WAV</a>
            <a href="{mic_url}" download="{meeting_id}_mic.wav" class="btn btn-secondary">Download Mic WAV</a>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)
