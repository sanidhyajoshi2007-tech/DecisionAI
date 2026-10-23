"""
Storage service package.
Provides StorageService abstract interface and LocalStorageService implementation.
Exposes get_storage_service() factory and backwards-compatible helper functions.
Strict constraints: No emojis, no em dashes.
"""

from pathlib import Path
from fastapi import UploadFile

from backend.config import Settings, get_settings
from backend.services.storage.base import StorageService
from backend.services.storage.local import (
    ALLOWED_AUDIO_MIME_TYPES,
    ALLOWED_EXTENSIONS,
    CHUNK_SIZE,
    LocalStorageService,
    get_file_extension,
    validate_audio_file,
)


def get_storage_service(settings: Settings | None = None) -> StorageService:
    """Factory returning configured storage service backend (default LocalStorageService)."""
    cfg = settings or get_settings()
    return LocalStorageService(settings=cfg)


async def save_audio_file(
    file: UploadFile,
    user_id: int,
    meeting_id: int,
    settings: Settings,
    extension: str = ".webm",
) -> tuple[str, Path, int]:
    """Backwards-compatible helper for saving audio."""
    svc = LocalStorageService(settings)
    return await svc.save_audio_file(file, user_id=user_id, meeting_id=meeting_id, extension=extension)


def get_audio_file_path(audio_path: str, settings: Settings) -> Path:
    """Backwards-compatible helper for resolving audio file path."""
    svc = LocalStorageService(settings)
    return svc.get_audio_file_path(audio_path)


def delete_audio_file(audio_path: str | None, settings: Settings) -> bool:
    """Backwards-compatible helper for deleting audio file."""
    svc = LocalStorageService(settings)
    return svc.delete_audio_file(audio_path)


__all__ = [
    "StorageService",
    "LocalStorageService",
    "get_storage_service",
    "save_audio_file",
    "validate_audio_file",
    "get_audio_file_path",
    "delete_audio_file",
    "get_file_extension",
    "ALLOWED_AUDIO_MIME_TYPES",
    "ALLOWED_EXTENSIONS",
    "CHUNK_SIZE",
]
