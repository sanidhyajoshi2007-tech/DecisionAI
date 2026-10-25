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
from backend.crud.decisions import bulk_create_decisions
from backend.crud.meetings import (
    create_segments,
    get_meeting_by_id,
    update_meeting_status,
    update_meeting_summary,
    update_segment_text,
)
from backend.database import async_session_factory
from backend.models.meeting import MeetingStatus
from backend.providers.llm import get_llm_provider
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)


logger = logging.getLogger(__name__)


import struct

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


def is_silent_wav(path: Path, min_rms: float = 20.0) -> bool:
    """
    Check if a WAV file is pure silence or near-zero background energy.
    Prevents STT models (Gemini, Whisper) from hallucinating text on silent channels
    when only one participant is present in a call.
    """
    if not path or not path.exists() or path.stat().st_size <= 44:
        return True
    try:
        with wave.open(str(path), "rb") as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return True
            sampwidth = wf.getsampwidth()
            if sampwidth != 2:
                return False
            # Read up to 5 seconds of audio to measure RMS energy
            max_read = min(n_frames, 16000 * 5)
            frames_data = wf.readframes(max_read)
            if not frames_data:
                return True
            num_samples = len(frames_data) // 2
            samples = struct.unpack(f"<{num_samples}h", frames_data)
            rms = (sum(s * s for s in samples) / num_samples) ** 0.5
            return rms < min_rms
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
        created_segments = []

        async with session_factory() as db:
            if db_segments_data:
                created_segments = await create_segments(db, meeting_id, db_segments_data)
            meeting = await get_meeting_by_id(db, meeting_id)
            meeting_title = meeting.title if meeting else None
            await db.commit()

        # LLM Post-Processing: (1) Cleanup with glossary (2) Summary (3) Decisions/Action items
        if created_segments:
            try:
                llm_provider = get_llm_provider(settings=settings)
                logger.info(
                    f"[LLM Pipeline] Running post-processing for meeting #{meeting_id} "
                    f"using '{llm_provider.name}'..."
                )
                segment_dicts = [
                    {
                        "id": s.id,
                        "text": s.text,
                        "raw_text": s.raw_text,
                        "start_ms": s.start_ms,
                        "speaker": s.speaker,
                    }
                    for s in created_segments
                ]
                effective_glossary = glossary or settings.glossary_list
                ai_result = await llm_provider.process_meeting(
                    segments=segment_dicts,
                    glossary=effective_glossary,
                    meeting_title=meeting_title,
                )

                async with session_factory() as db:
                    # (1) Update cleaned transcript text, keeping raw_text intact
                    for cleaned in ai_result.cleaned_segments:
                        await update_segment_text(db, cleaned.id, cleaned.cleaned_text)

                    # (2) Save meeting summary
                    if ai_result.summary:
                        await update_meeting_summary(db, meeting_id, ai_result.summary)

                    # (3) Save structured decisions, action items, and client requests
                    if ai_result.decisions:
                        decision_dicts = [d.model_dump() for d in ai_result.decisions]
                        await bulk_create_decisions(db, meeting_id, decision_dicts)

                    await db.commit()

                logger.info(
                    f"[LLM Pipeline] Meeting #{meeting_id}: {len(ai_result.cleaned_segments)} segments cleaned, "
                    f"summary generated, {len(ai_result.decisions)} items extracted."
                )
            except Exception as llm_err:
                logger.error(
                    f"[LLM Pipeline] Meeting #{meeting_id} post-processing warning: {llm_err}",
                    exc_info=True,
                )

        # Embed transcript chunks into ChromaDB for RAG
        try:
            from backend.services.rag.pipeline import index_meeting_transcript
            async with session_factory() as db:
                m_lookup = await get_meeting_by_id(db, meeting_id)
                u_id = m_lookup.user_id if m_lookup else 1
                await index_meeting_transcript(
                    db=db,
                    meeting_id=meeting_id,
                    user_id=u_id,
                    settings=settings,
                )
            logger.info(f"[RAG Pipeline] Meeting #{meeting_id} chunks indexed to ChromaDB.")
        except Exception as rag_err:
            logger.warning(
                f"[RAG Pipeline] Meeting #{meeting_id} indexing warning: {rag_err}",
                exc_info=True,
            )

        # Mark meeting status as READY
        async with session_factory() as db:
            await update_meeting_status(db, meeting_id, MeetingStatus.READY)
            await db.commit()

        logger.info(f"[STT Pipeline] Meeting #{meeting_id} pipeline finished successfully.")

    except Exception as exc:
        err_msg = f"Meeting pipeline failed: {str(exc)}"
        logger.error(f"[STT Pipeline] Meeting #{meeting_id} error: {err_msg}", exc_info=True)
        async with session_factory() as db:
            await update_meeting_status(db, meeting_id, MeetingStatus.FAILED, error_message=err_msg)
            await db.commit()

