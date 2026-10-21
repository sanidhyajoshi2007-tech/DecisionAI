"""
Audio streaming service — handles live WebSocket audio chunks from Google Meet.

Saves each channel (tab and mic) to its own 16 kHz 16-bit mono WAV file:
    {audio_storage_dir}/{meeting_id}_tab.wav
    {audio_storage_dir}/{meeting_id}_mic.wav
"""

import base64
import json
from pathlib import Path
import wave
from typing import BinaryIO

from backend.config import Settings


class MeetingAudioWriter:
    """Manages separate WAV file writers for tab and mic audio streams."""

    def __init__(self, meeting_id: int, storage_dir: Path):
        self.meeting_id = meeting_id
        self.storage_dir = storage_dir
        self.tab_path = storage_dir / f"{meeting_id}_tab.wav"
        self.mic_path = storage_dir / f"{meeting_id}_mic.wav"

        self.tab_file: wave.Wave_write | None = None
        self.mic_file: wave.Wave_write | None = None
        self.tab_frames: int = 0
        self.mic_frames: int = 0

    def _ensure_wav(self, channel: str) -> wave.Wave_write:
        """Initialize or return the wave writer for the given channel."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if channel == "tab":
            if self.tab_file is None:
                # If file already exists (e.g. on reconnect), append by reading existing or create fresh
                self.tab_file = self._open_wav(self.tab_path)
            return self.tab_file
        else:
            if self.mic_file is None:
                self.mic_file = self._open_wav(self.mic_path)
            return self.mic_file

    def _open_wav(self, path: Path) -> wave.Wave_write:
        """Create and configure a mono 16 kHz 16-bit WAV file."""
        wf = wave.open(str(path), "wb")
        wf.setnchannels(1)       # Mono
        wf.setsampwidth(2)       # 16-bit PCM (2 bytes/sample)
        wf.setframerate(16000)   # 16 kHz
        return wf

    def write_chunk(self, channel: str, pcm_bytes: bytes) -> None:
        """Append raw 16-bit PCM chunk to the appropriate channel's WAV file."""
        channel_lower = channel.lower()
        if channel_lower in ("tab", "other", "remote"):
            wf = self._ensure_wav("tab")
            wf.writeframes(pcm_bytes)
            self.tab_frames += len(pcm_bytes) // 2
        elif channel_lower in ("mic", "user", "local"):
            wf = self._ensure_wav("mic")
            wf.writeframes(pcm_bytes)
            self.mic_frames += len(pcm_bytes) // 2

    def close(self) -> None:
        """Flush and close all open WAV files."""
        if self.tab_file is not None:
            try:
                self.tab_file.close()
            except Exception:
                pass
            finally:
                self.tab_file = None

        if self.mic_file is not None:
            try:
                self.mic_file.close()
            except Exception:
                pass
            finally:
                self.mic_file = None


# In-memory registry of active streaming sessions
_active_writers: dict[int, MeetingAudioWriter] = {}


def get_or_create_writer(meeting_id: int, settings: Settings) -> MeetingAudioWriter:
    """Retrieve existing writer or create a new one for this meeting."""
    if meeting_id not in _active_writers:
        _active_writers[meeting_id] = MeetingAudioWriter(
            meeting_id=meeting_id,
            storage_dir=settings.audio_storage_path,
        )
    return _active_writers[meeting_id]


def finish_writer(meeting_id: int, settings: Settings) -> tuple[Path, Path, float]:
    """
    Close writers for a meeting and return (tab_path, mic_path, duration_seconds).
    Ensures both WAV files exist on disk with valid headers even if empty.
    """
    writer = _active_writers.pop(meeting_id, None)
    if writer:
        writer.close()

    settings.audio_storage_path.mkdir(parents=True, exist_ok=True)
    tab_path = settings.audio_storage_path / f"{meeting_id}_tab.wav"
    mic_path = settings.audio_storage_path / f"{meeting_id}_mic.wav"

    # Guarantee both WAV files exist so audio players never 404
    for path in (tab_path, mic_path):
        if not path.exists():
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)

    tab_duration = get_wav_duration(tab_path)
    mic_duration = get_wav_duration(mic_path)
    max_duration = max(tab_duration, mic_duration)

    return tab_path, mic_path, max_duration



def get_wav_duration(path: Path) -> float:
    """Calculate duration in seconds of a WAV file."""
    if not path.exists():
        return 0.0
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else 0.0
    except Exception:
        return 0.0


def parse_audio_chunk(message: bytes | str) -> tuple[str, bytes] | None:
    """
    Parse an incoming WebSocket message into (channel, pcm_bytes).
    Supported protocols:
    1. Binary with 4-byte ASCII prefix:
       b"TAB:" + pcm_data
       b"MIC:" + pcm_data
    2. Binary with 1-byte prefix:
       0x00 + pcm_data -> tab
       0x01 + pcm_data -> mic
    3. Text/JSON:
       {"channel": "tab"|"mic", "data": "<base64_encoded_pcm>"}
    """
    if isinstance(message, bytes):
        if len(message) >= 4 and message[:4] == b"TAB:":
            return "tab", message[4:]
        if len(message) >= 4 and message[:4] == b"MIC:":
            return "mic", message[4:]
        if len(message) >= 1 and message[0] == 0:
            return "tab", message[1:]
        if len(message) >= 1 and message[0] == 1:
            return "mic", message[1:]
        # Fallback if binary without header: default to tab
        return "tab", message

    if isinstance(message, str):
        try:
            payload = json.loads(message)
            channel = payload.get("channel", "tab")
            b64_data = payload.get("data", "")
            pcm_data = base64.b64decode(b64_data)
            return channel, pcm_data
        except Exception:
            return None

    return None
