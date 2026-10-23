"""
Storage Service Interface.
Abstract base class defining storage operations for meeting audio files.
Allows swapping local filesystem storage for Cloudflare R2, AWS S3, or GCS later.
Strict constraints: No emojis, no em dashes.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from fastapi import UploadFile


class StorageService(ABC):
    """Abstract base class for audio storage operations."""

    @abstractmethod
    async def save_audio_file(
        self,
        file: UploadFile,
        user_id: int,
        meeting_id: int,
        extension: str = ".webm",
    ) -> tuple[str, Path, int]:
        """
        Stream-writes the uploaded file to storage.
        Returns tuple of (relative_path_str, absolute_file_path, bytes_written).
        """
        pass

    @abstractmethod
    def get_audio_file_path(self, audio_path: str) -> Path:
        """Resolve audio path to a local Path object."""
        pass

    @abstractmethod
    def delete_audio_file(self, audio_path: str | None) -> bool:
        """Delete a single audio file from storage."""
        pass

    @abstractmethod
    def delete_meeting_audio(
        self,
        user_id: int,
        meeting_id: int,
        audio_path: str | None = None,
    ) -> int:
        """
        Delete all audio files associated with a meeting:
        - {user_id}/{meeting_id}{ext}
        - {meeting_id}_tab.wav
        - {meeting_id}_mic.wav
        - optional custom audio_path
        Returns count of deleted files.
        """
        pass

    @abstractmethod
    def save_bytes(self, filename: str, data: bytes) -> Path:
        """Save raw bytes to storage."""
        pass

    @abstractmethod
    def file_exists(self, filename: str) -> bool:
        """Check if file exists in storage."""
        pass

    @abstractmethod
    def delete_file(self, filename: str) -> bool:
        """Delete file from storage."""
        pass

    @abstractmethod
    def delete_user_storage(self, user_id: int, meeting_ids: list[int] | None = None) -> int:
        """
        Delete all audio files and directories for a user.
        Returns count of deleted files.
        """
        pass
