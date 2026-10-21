"""
Meeting Audio Transcription Pipeline.
Chunks audio files, transcribes each channel in parallel, merges by timestamp,
and persists segments to the database.
"""

import asyncio
import logging
from pathlib import Path
import tempfile
from typing import Any
import wave

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings, get_settings
from backend.crud.meetings import create_segments, update_meeting_status
from backend.database import async_session_factory
from backend.models.meeting import MeetingStatus
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)


def is_valid_wav(path: Path) -> bool:
    """Check if file exists and has audio frames beyond the 44-byte WAV header."""
    if not path or not path.exists():
        return False
    if path.stat().st_size <= 44:
        return False
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() > 0
    except Exception:
        return False


def chunk_wav(audio_path: Path, chunk_seconds: int = 60) -> list[tuple[Path, int]]:
    """
    Split a WAV file into smaller temporal chunks if duration exceeds chunk_seconds.
    Returns list of (chunk_wav_path, offset_ms).
    """
    try:
        with wave.open(str(audio_path), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
    except Exception as e:
        logger.warning(f"Could not open WAV for chunking: {e}. Using original file.")
        return [(audio_path, 0)]

    chunk_frames = int(chunk_seconds * framerate)
    if n_frames <= chunk_frames:
        return [(audio_path, 0)]

    chunks: list[tuple[Path, int]] = []
    temp_dir = Path(tempfile.gettempdir()) / "decisionai_stt_chunks"
    temp_dir.mkdir(parents=True, exist_ok=True)

    with wave.open(str(audio_path), "rb") as wf:
        frame_idx = 0
        chunk_idx = 0
        while frame_idx < n_frames:
            frames_to_read = min(chunk_frames, n_frames - frame_idx)
            frames_data = wf.readframes(frames_to_read)
            if not frames_data:
                break

            offset_ms = int((frame_idx / framerate) * 1000)
            chunk_file = temp_dir / f"{audio_path.stem}_chunk_{chunk_idx}.wav"

            with wave.open(str(chunk_file), "wb") as cwf:
                cwf.setnchannels(channels)
                cwf.setsampwidth(sampwidth)
                cwf.setframerate(framerate)
                cwf.writeframes(frames_data)

            chunks.append((chunk_file, offset_ms))
            frame_idx += frames_to_read
            chunk_idx += 1

    return chunks


async def transcribe_channel(
    provider: TranscriptionProvider,
    audio_path: Path,
    speaker: str,
    glossary: list[str] | None = None,
    chunk_seconds: int = 60,
) -> tuple[list[TranscriptionSegment], str | None]:
    """
    Transcribe a single audio channel in chunks and return (segments, error_msg).
    """
    if not is_valid_wav(audio_path):
        logger.info(f"Skipping channel '{speaker}': no valid audio frames found.")
        return [], None

    chunks = chunk_wav(audio_path, chunk_seconds=chunk_seconds)
    all_segments: list[TranscriptionSegment] = []
    error_msg: str | None = None

    for chunk_path, offset_ms in chunks:
        try:
            result = await provider.transcribe(
                chunk_path,
                speaker=speaker,
                glossary=glossary,
                chunk_offset_ms=offset_ms,
            )
            all_segments.extend(result.segments)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error transcribing {speaker} chunk {chunk_path}: {e}", exc_info=True)
        finally:
            # Clean up temp chunks (leave original file intact)
            if chunk_path != audio_path and chunk_path.exists():
                try:
                    chunk_path.unlink()
                except Exception:
                    pass

    return all_segments, error_msg


async def process_meeting_transcription(
    meeting_id: int,
    tab_path: Path,
    mic_path: Path,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    glossary: list[str] | None = None,
) -> None:
    """
    Background worker task:
    1. Sets meeting status to PROCESSING.
    2. Transcribes both Tab (Participants) and Mic (You) channels in chunks.
    3. Merges segments from both channels and sorts chronologically by start_ms.
    4. Persists segments to SQLite database.
    5. Sets meeting status to READY (or ERROR on failure).
    """
    settings = settings or get_settings()
    session_factory = session_factory or async_session_factory

    # Import here to avoid circular dependencies
    from backend.services.stt import get_transcription_provider

    provider = get_transcription_provider(settings=settings)
    logger.info(
        f"[STT Pipeline] Starting transcription for meeting #{meeting_id} using '{provider.name}'..."
    )

    async with session_factory() as db:
        await update_meeting_status(db, meeting_id, MeetingStatus.PROCESSING)
        await db.commit()

    try:
        # Transcribe both channels concurrently
        tab_task = transcribe_channel(
            provider=provider,
            audio_path=tab_path,
            speaker="Participants (Tab)",
            glossary=glossary,
        )
        mic_task = transcribe_channel(
            provider=provider,
            audio_path=mic_path,
            speaker="You (Mic)",
            glossary=glossary,
        )

        (tab_segments, tab_err), (mic_segments, mic_err) = await asyncio.gather(tab_task, mic_task)

        # Merge segments from both channels by timestamp (start_ms)
        merged_segments = tab_segments + mic_segments
        merged_segments.sort(key=lambda s: s.start_ms)

        logger.info(
            f"[STT Pipeline] Meeting #{meeting_id}: Captured {len(tab_segments)} tab segments, "
            f"{len(mic_segments)} mic segments. Total merged: {len(merged_segments)}"
        )

        # If zero segments were produced and an error occurred on either channel, record it
        if not merged_segments and (tab_err or mic_err):
            err_str = tab_err or mic_err or "Unknown transcription error"
            async with session_factory() as db:
                await update_meeting_status(db, meeting_id, MeetingStatus.ERROR, error_message=err_str)
                await db.commit()
            return

        # Convert to dict format for database insertion
        db_segments_data = [seg.to_dict() for seg in merged_segments]

        async with session_factory() as db:
            if db_segments_data:
                await create_segments(db, meeting_id, db_segments_data)
            await update_meeting_status(db, meeting_id, MeetingStatus.READY)
            await db.commit()

        logger.info(f"[STT Pipeline] Meeting #{meeting_id} transcription finished successfully.")

    except Exception as exc:
        err_msg = f"Transcription failed: {str(exc)}"
        logger.error(f"[STT Pipeline] Meeting #{meeting_id} error: {err_msg}", exc_info=True)
        async with session_factory() as db:
            await update_meeting_status(db, meeting_id, MeetingStatus.ERROR, error_message=err_msg)
            await db.commit()
