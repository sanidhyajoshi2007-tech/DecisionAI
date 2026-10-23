"""
Local filesystem storage service implementation.
Stores meeting audio on disk under configured audio_storage_dir.
Strict constraints: No emojis, no em dashes.
"""

from pathlib import Path
import shutil
from fastapi import HTTPException, UploadFile, status

from backend.config import Settings
from backend.services.storage.base import StorageService

ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "video/webm",
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

CHUNK_SIZE = 64 * 1024


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


class LocalStorageService(StorageService):
    """Local disk implementation of storage service."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def save_audio_file(
        self,
        file: UploadFile,
        user_id: int,
        meeting_id: int,
        extension: str = ".webm",
    ) -> tuple[str, Path, int]:
        """
        Stream-writes the uploaded file to disk at:
            {audio_storage_path}/{user_id}/{meeting_id}{extension}
        Enforces settings.max_upload_bytes.
        """
        user_dir = self.settings.audio_storage_path / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{meeting_id}{extension}"
        target_path = user_dir / filename
        relative_path = f"{user_id}/{filename}"

        max_bytes = self.settings.max_upload_bytes
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
                                f"{self.settings.max_upload_size_mb} MB"
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

    def get_audio_file_path(self, audio_path: str) -> Path:
        """Resolve stored audio_path (relative or absolute) to a Path object."""
        path = Path(audio_path)
        if path.is_absolute():
            return path
        return self.settings.audio_storage_path / audio_path

    def delete_audio_file(self, audio_path: str | None) -> bool:
        """Delete audio file from disk if it exists."""
        if not audio_path:
            return False
        path = self.get_audio_file_path(audio_path)
        if path.exists():
            path.unlink()
            return True
        return False

    def delete_meeting_audio(
        self,
        user_id: int,
        meeting_id: int,
        audio_path: str | None = None,
    ) -> int:
        """
        Delete all audio files associated with a meeting:
        - Files under {audio_storage_path}/{user_id}/{meeting_id}.*
        - Tab audio: {audio_storage_path}/{meeting_id}_tab.wav
        - Mic audio: {audio_storage_path}/{meeting_id}_mic.wav
        - Explicit audio_path if provided
        """
        deleted_count = 0
        user_dir = self.settings.audio_storage_path / str(user_id)
        if user_dir.exists():
            for p in user_dir.glob(f"{meeting_id}.*"):
                try:
                    p.unlink()
                    deleted_count += 1
                except OSError:
                    pass

        # Also delete WebSocket stream WAVs
        for suffix in ("_tab.wav", "_mic.wav"):
            wav_path = self.settings.audio_storage_path / f"{meeting_id}{suffix}"
            if wav_path.exists():
                try:
                    wav_path.unlink()
                    deleted_count += 1
                except OSError:
                    pass

        # If custom audio_path was passed and exists, delete it too
        if audio_path:
            target = self.get_audio_file_path(audio_path)
            if target.exists() and target.is_file():
                try:
                    target.unlink()
                    deleted_count += 1
                except OSError:
                    pass

        return deleted_count

    def save_bytes(self, filename: str, data: bytes) -> Path:
        """Save raw bytes to local storage."""
        path = self.get_audio_file_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def file_exists(self, filename: str) -> bool:
        """Check if file exists in local storage."""
        return self.get_audio_file_path(filename).exists()

    def delete_file(self, filename: str) -> bool:
        """Delete file from local storage."""
        return self.delete_audio_file(filename)

    def delete_user_storage(self, user_id: int, meeting_ids: list[int] | None = None) -> int:
        """
        Delete all audio files and storage folder for a user.
        Also deletes associated stream files for provided meeting_ids.
        """
        deleted_count = 0
        user_dir = self.settings.audio_storage_path / str(user_id)
        if user_dir.exists():
            for p in user_dir.rglob("*"):
                if p.is_file():
                    deleted_count += 1
            try:
                shutil.rmtree(user_dir, ignore_errors=True)
            except OSError:
                pass

        if meeting_ids:
            for m_id in meeting_ids:
                for suffix in ("_tab.wav", "_mic.wav"):
                    wav_path = self.settings.audio_storage_path / f"{m_id}{suffix}"
                    if wav_path.exists():
                        try:
                            wav_path.unlink()
                            deleted_count += 1
                        except OSError:
                            pass

        return deleted_count
