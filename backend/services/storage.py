"""
Audio storage service — validates and saves uploaded audio files to disk.

Storage layout:
    {audio_storage_dir}/{user_id}/{meeting_id}{ext}
"""

from pathlib import Path
from fastapi import HTTPException, UploadFile, status

from backend.config import Settings

ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "video/webm",  # Chrome sometimes tags audio/webm as video/webm
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
    "audio/aac",
    "audio/flac",
    "audio/opus",
}

ALLOWED_EXTENSIONS = {
    ".webm",
    ".wav",
    ".mp3",
    ".mp4",
    ".m4a",
    ".ogg",
    ".aac",
    ".flac",
    ".opus",
}

CHUNK_SIZE = 64 * 1024  # 64 KB chunks for streaming write


def get_file_extension(filename: str | None, content_type: str | None) -> str:
    """Extract or determine the file extension, defaulting to .webm."""
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in ALLOWED_EXTENSIONS:
            return ext
    if content_type:
        base_type = content_type.split(";")[0].strip().lower()
        if base_type in ("audio/wav", "audio/x-wav", "audio/wave"):
            return ".wav"
        if base_type in ("audio/mpeg", "audio/mp3"):
            return ".mp3"
        if base_type in ("audio/mp4", "audio/m4a", "audio/x-m4a"):
            return ".m4a"
        if base_type == "audio/ogg":
            return ".ogg"
        if base_type == "audio/flac":
            return ".flac"
        if base_type == "audio/aac":
            return ".aac"
    return ".webm"


def validate_audio_file(file: UploadFile) -> str:
    """
    Validate that the uploaded file is a supported audio format.
    Returns the resolved file extension (e.g., '.webm').
    Raises HTTPException(400) if validation fails.
    """
    content_type = (
        file.content_type.split(";")[0].strip().lower()
        if file.content_type
        else ""
    )
    filename = file.filename or ""
    ext = Path(filename).suffix.lower() if filename else ""

    is_valid_mime = (
        content_type in ALLOWED_AUDIO_MIME_TYPES
        or content_type.startswith("audio/")
    )
    is_valid_ext = ext in ALLOWED_EXTENSIONS

    if not is_valid_mime and not is_valid_ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported audio type '{file.content_type}'. "
                f"Allowed formats: webm, wav, mp3, m4a, ogg, aac, flac."
            ),
        )
    return get_file_extension(filename, file.content_type)


async def save_audio_file(
    file: UploadFile,
    user_id: int,
    meeting_id: int,
    settings: Settings,
    extension: str = ".webm",
) -> tuple[str, Path, int]:
    """
    Stream-writes the uploaded file to disk at:
        {audio_storage_path}/{user_id}/{meeting_id}{extension}

    Enforces settings.max_upload_bytes. If the limit is exceeded,
    cleans up the partial file and raises HTTPException(413).
    If the file is 0 bytes, raises HTTPException(400).

    Returns:
        tuple of (relative_path_str, absolute_file_path, total_bytes_written)
    """
    user_dir = settings.audio_storage_path / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{meeting_id}{extension}"
    target_path = user_dir / filename
    relative_path = f"{user_id}/{filename}"

    max_bytes = settings.max_upload_bytes
    bytes_written = 0

    try:
        with open(target_path, "wb") as f:
            while chunk := await file.read(CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    f.close()
                    if target_path.exists():
                        target_path.unlink()
                    raise HTTPException(
                        status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                        detail=(
                            f"File size exceeds maximum limit of "
                            f"{settings.max_upload_size_mb} MB"
                        ),
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save audio file: {e}",
        ) from e

    if bytes_written == 0:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded audio file is empty",
        )

    return relative_path, target_path, bytes_written


def get_audio_file_path(audio_path: str, settings: Settings) -> Path:
    """Resolve stored audio_path (relative or absolute) to a Path object."""
    path = Path(audio_path)
    if path.is_absolute():
        return path
    return settings.audio_storage_path / audio_path


def delete_audio_file(audio_path: str | None, settings: Settings) -> bool:
    """Delete audio file from disk if it exists."""
    if not audio_path:
        return False
    path = get_audio_file_path(audio_path, settings)
    if path.exists():
        path.unlink()
        return True
    return False
