"""
Tests for Phase 2 — audio upload, storage, and validation.
"""

from pathlib import Path
import pytest
from httpx import AsyncClient

from tests.conftest import get_test_settings
from backend.config import Settings, get_settings
from backend.main import app


@pytest.mark.asyncio
async def test_upload_audio_success(client: AsyncClient):
    """Uploading a valid audio file creates a meeting and saves the file to disk."""
    test_settings = get_test_settings()
    audio_content = b"\x1aE\xdf\xa3" + b"dummy-webm-audio-data" * 20

    response = await client.post(
        "/api/meetings/upload",
        files={"file": ("recording.webm", audio_content, "audio/webm")},
        data={"title": "Q3 Planning Meeting"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] > 0
    assert data["title"] == "Q3 Planning Meeting"
    assert data["status"] == "uploading"
    assert data["user_id"] == 1  # Dev user
    assert data["audio_path"] == f"1/{data['id']}.webm"
    assert data["duration_seconds"] is None
    assert data["error_message"] is None

    # Check that the file was actually written to disk
    stored_file = Path(test_settings.audio_storage_dir) / data["audio_path"]
    assert stored_file.exists()
    assert stored_file.read_bytes() == audio_content


@pytest.mark.asyncio
async def test_upload_audio_default_title(client: AsyncClient):
    """Uploading without a title sets the default 'Untitled Meeting'."""
    audio_content = b"\x1aE\xdf\xa3" + b"webm-stream"

    response = await client.post(
        "/api/meetings/upload",
        files={"file": ("meeting.webm", audio_content, "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Untitled Meeting"


@pytest.mark.asyncio
async def test_upload_audio_alternative_field_name(client: AsyncClient):
    """Can upload using 'audio_file' field instead of 'file'."""
    audio_content = b"\x1aE\xdf\xa3" + b"webm-audio-blob"

    response = await client.post(
        "/api/meetings/upload",
        files={"audio_file": ("recording.webm", audio_content, "audio/webm")},
        data={"title": "Team Sync"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Team Sync"


@pytest.mark.asyncio
async def test_upload_different_audio_formats(client: AsyncClient):
    """Supports wav and mp3 formats and retains appropriate extensions."""
    test_settings = get_test_settings()

    # WAV
    wav_content = b"RIFF" + b"\x00" * 36 + b"WAVEfmt " + b"data"
    response_wav = await client.post(
        "/api/meetings/upload",
        files={"file": ("call.wav", wav_content, "audio/wav")},
        data={"title": "WAV Call"},
    )
    assert response_wav.status_code == 200
    data_wav = response_wav.json()
    assert data_wav["audio_path"].endswith(".wav")
    assert (Path(test_settings.audio_storage_dir) / data_wav["audio_path"]).exists()

    # MP3
    mp3_content = b"\xff\xfb\x90\x44" + b"mp3data" * 10
    response_mp3 = await client.post(
        "/api/meetings/upload",
        files={"file": ("interview.mp3", mp3_content, "audio/mpeg")},
        data={"title": "MP3 Interview"},
    )
    assert response_mp3.status_code == 200
    data_mp3 = response_mp3.json()
    assert data_mp3["audio_path"].endswith(".mp3")
    assert (Path(test_settings.audio_storage_dir) / data_mp3["audio_path"]).exists()


@pytest.mark.asyncio
async def test_upload_reject_non_audio(client: AsyncClient):
    """Non-audio files (e.g. text/plain, image/png) are rejected with 400 Bad Request."""
    test_settings = get_test_settings()
    text_content = b"This is a text document, not an audio file."

    response = await client.post(
        "/api/meetings/upload",
        files={"file": ("notes.txt", text_content, "text/plain")},
        data={"title": "Text Upload"},
    )

    assert response.status_code == 400
    assert "Unsupported audio type" in response.json()["detail"]

    # Verify no file was saved in audio storage
    storage_dir = Path(test_settings.audio_storage_dir)
    if storage_dir.exists():
        files = list(storage_dir.rglob("*.txt"))
        assert len(files) == 0


@pytest.mark.asyncio
async def test_upload_reject_empty_file(client: AsyncClient):
    """Empty (0-byte) files are rejected with 400 Bad Request."""
    response = await client.post(
        "/api/meetings/upload",
        files={"file": ("empty.webm", b"", "audio/webm")},
        data={"title": "Empty Call"},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_missing_file(client: AsyncClient):
    """Uploading without a file returns 422 Unprocessable Entity."""
    response = await client.post(
        "/api/meetings/upload",
        data={"title": "No File Provided"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_exceeds_max_size(client: AsyncClient):
    """Files exceeding max_upload_size_mb are rejected with 413 and cleaned up."""
    test_settings = get_test_settings()

    # Create custom settings with a tiny limit (1 MB)
    class TinySettings(Settings):
        max_upload_size_mb: int = 1
        database_url: str = "sqlite+aiosqlite:///:memory:"
        debug: bool = False
        audio_storage_dir: str = "test_audio_storage"

        @property
        def max_upload_bytes(self) -> int:
            return 50  # 50 bytes max for testing

    tiny_settings = TinySettings()
    app.dependency_overrides[get_settings] = lambda: tiny_settings

    try:
        # Send 200 bytes, which exceeds the 50 bytes limit
        oversized_content = b"\x1aE\xdf\xa3" + b"X" * 200

        response = await client.post(
            "/api/meetings/upload",
            files={"file": ("large.webm", oversized_content, "audio/webm")},
            data={"title": "Too Large"},
        )

        assert response.status_code == 413
        assert "exceeds maximum limit" in response.json()["detail"]

        # Verify no audio file was left on disk
        storage_dir = Path(tiny_settings.audio_storage_dir)
        if storage_dir.exists():
            files = list(storage_dir.rglob("*.webm"))
            assert len(files) == 0
    finally:
        # Restore normal test settings override
        app.dependency_overrides[get_settings] = lambda: test_settings
