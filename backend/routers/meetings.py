"""
Meeting API routes — handles audio upload, meeting metadata, and transcript access.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import Settings, get_settings
from backend.crud.meetings import (
    create_meeting,
    delete_meeting,
    update_meeting_audio_path,
)
from backend.database import get_db
from backend.models.user import User
from backend.schemas.meeting import MeetingResponse
from backend.services.storage import (
    delete_audio_file,
    save_audio_file,
    validate_audio_file,
)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@router.post(
    "/upload",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload meeting audio recording",
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
    Accept an audio recording from the Chrome extension or dashboard.
    Stores the audio file on disk and initializes a Meeting record with status='uploading'.
    """
    actual_file = file or audio_file
    if actual_file is None:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail="An audio file must be provided under field 'file' or 'audio_file'",
        )

    clean_title = title.strip() if title and title.strip() else "Untitled Meeting"
    ext = validate_audio_file(actual_file)

    # 1. Create meeting record in DB (status=UPLOADING) to obtain the auto-increment id
    meeting = await create_meeting(
        db=db,
        user_id=current_user.id,
        title=clean_title,
    )

    # 2. Save audio file to disk with meeting.id in filename
    try:
        relative_path, _full_path, _bytes_written = await save_audio_file(
            file=actual_file,
            user_id=current_user.id,
            meeting_id=meeting.id,
            settings=settings,
            extension=ext,
        )
        # 3. Associate audio path with meeting
        await update_meeting_audio_path(db, meeting_id=meeting.id, audio_path=relative_path)
    except Exception:
        # Clean up database record if saving fails
        await delete_meeting(db, meeting_id=meeting.id, user_id=current_user.id)
        raise

    return MeetingResponse.model_validate(meeting)
