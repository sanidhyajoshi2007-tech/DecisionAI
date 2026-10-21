"""
Tests for live recording streaming endpoints:
- POST /meetings
- WebSocket /ws/meetings/{id}
- POST /meetings/{id}/finish
- GET /meetings/{id} (HTML Viewer)
- GET /meetings/{id}/audio/{channel}
"""

import base64
from pathlib import Path
import shutil
import wave
import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from tests.conftest import get_test_settings
from backend.config import get_settings
from backend.main import app


@pytest.mark.asyncio
async def test_create_meeting_endpoint(client: AsyncClient):
    """POST /meetings creates a new meeting record without requiring authentication."""
    response = await client.post(
        "/meetings",
        json={"title": "Client Discovery Call"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] > 0
    assert data["title"] == "Client Discovery Call"
    assert data["status"] == "uploading"
    assert data["user_id"] == 1


@pytest.mark.asyncio
async def test_create_meeting_default_title(client: AsyncClient):
    """POST /meetings with no payload sets a default title."""
    response = await client.post("/meetings")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Google Meet Call"


def test_websocket_audio_streaming_and_finish():
    """
    Test streaming audio chunks over WebSocket and finishing the recording.
    Uses Starlette TestClient which supports WebSocket testing synchronously.
    """
    test_settings = get_test_settings()
    app.dependency_overrides[get_settings] = lambda: test_settings

    try:
        with TestClient(app) as sync_client:
            # 1. Create meeting
            create_res = sync_client.post("/meetings", json={"title": "Weekly Engineering Sync"})
            assert create_res.status_code == 200
            meeting_id = create_res.json()["id"]

            # 2. Simulate 1-second chunks: 16000 samples * 2 bytes = 32000 bytes
            one_sec_tab_pcm = b"\x01\x00" * 16000
            one_sec_mic_pcm = b"\x02\x00" * 16000

            # Connect WebSocket
            with sync_client.websocket_connect(f"/ws/meetings/{meeting_id}") as ws:
                # Send binary frame for TAB
                ws.send_bytes(b"TAB:" + one_sec_tab_pcm)
                # Send binary frame for MIC
                ws.send_bytes(b"MIC:" + one_sec_mic_pcm)

                # Send another 1-second chunk via JSON base64 format
                b64_tab = base64.b64encode(one_sec_tab_pcm).decode("ascii")
                ws.send_text(f'{{"channel": "tab", "data": "{b64_tab}"}}')

                b64_mic = base64.b64encode(one_sec_mic_pcm).decode("ascii")
                ws.send_text(f'{{"channel": "mic", "data": "{b64_mic}"}}')

            # 3. Finish recording
            finish_res = sync_client.post(f"/meetings/{meeting_id}/finish")
            assert finish_res.status_code == 200
            finish_data = finish_res.json()
            assert finish_data["status"] == "ready"
            assert finish_data["meeting_id"] == meeting_id
            # We sent 2 seconds of audio
            assert finish_data["duration_seconds"] >= 2.0

            # 4. Assert WAV files on disk
            storage_path = Path(test_settings.audio_storage_dir)
            tab_wav = storage_path / f"{meeting_id}_tab.wav"
            mic_wav = storage_path / f"{meeting_id}_mic.wav"

            assert tab_wav.exists()
            assert mic_wav.exists()

            with wave.open(str(tab_wav), "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2
                assert wf.getframerate() == 16000
                assert wf.getnframes() == 32000  # 2 seconds = 32000 frames

            with wave.open(str(mic_wav), "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2
                assert wf.getframerate() == 16000
                assert wf.getnframes() == 32000

            # 5. Test audio playback endpoint
            tab_audio_res = sync_client.get(f"/meetings/{meeting_id}/audio/tab")
            assert tab_audio_res.status_code == 200
            assert "audio/wav" in tab_audio_res.headers["content-type"]

            mic_audio_res = sync_client.get(f"/meetings/{meeting_id}/audio/mic")
            assert mic_audio_res.status_code == 200
            assert "audio/wav" in mic_audio_res.headers["content-type"]

            # 6. Test HTML viewer page
            view_res = sync_client.get(f"/meetings/{meeting_id}")
            assert view_res.status_code == 200
            assert "text/html" in view_res.headers["content-type"]
            assert "Weekly Engineering Sync" in view_res.text
            assert f"/meetings/{meeting_id}/audio/tab" in view_res.text
    finally:
        app.dependency_overrides.pop(get_settings, None)
        if Path(test_settings.audio_storage_dir).exists():
            shutil.rmtree(test_settings.audio_storage_dir, ignore_errors=True)


def test_live_recording_empty_session():
    """
    Test finishing recording when no audio was captured (e.g. 0 chunks).
    Verifies WAV files are created with valid headers, no 404s, and duration is 0.0s.
    """
    test_settings = get_test_settings()
    app.dependency_overrides[get_settings] = lambda: test_settings

    try:
        with TestClient(app) as sync_client:
            create_res = sync_client.post("/meetings", json={"title": "Empty Call"})
            assert create_res.status_code == 200
            meeting_id = create_res.json()["id"]

            finish_res = sync_client.post(f"/meetings/{meeting_id}/finish")
            assert finish_res.status_code == 200
            finish_data = finish_res.json()
            assert finish_data["status"] == "ready"
            assert finish_data["duration_seconds"] == 0.0

            # Tab and mic files should still exist with valid headers so playback never 404s
            tab_audio_res = sync_client.get(f"/meetings/{meeting_id}/audio/tab")
            assert tab_audio_res.status_code == 200
            assert "audio/wav" in tab_audio_res.headers["content-type"]

            mic_audio_res = sync_client.get(f"/meetings/{meeting_id}/audio/mic")
            assert mic_audio_res.status_code == 200
            assert "audio/wav" in mic_audio_res.headers["content-type"]

            # Viewer page shows duration and warning notice
            view_res = sync_client.get(f"/meetings/{meeting_id}")
            assert view_res.status_code == 200
            assert "0.0s" in view_res.text
            assert "No Audio Data Captured" in view_res.text
    finally:
        app.dependency_overrides.pop(get_settings, None)
        if Path(test_settings.audio_storage_dir).exists():
            shutil.rmtree(test_settings.audio_storage_dir, ignore_errors=True)
