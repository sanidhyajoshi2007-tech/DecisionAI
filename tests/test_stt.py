"""
Unit and integration tests for DecisionAI Speech-to-Text (STT) services.
Covers provider interface, normalizer, WER, factory, and background pipeline.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import Settings
from backend.models.base import Base
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.services.stt import (
    DeepgramTranscriptionProvider,
    FasterWhisperProvider,
    GeminiTranscriptionProvider,
    SarvamTranscriptionProvider,
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
    compute_word_error_rate,
    get_transcription_provider,
    normalize_hindi,
    normalize_hinglish,
    process_meeting_transcription,
)
from tests.conftest import get_test_settings


def test_transcription_segment_properties():
    """TranscriptionSegment handles timestamps, seconds conversion, and raw_text."""
    seg = TranscriptionSegment(
        speaker="You (Mic)",
        start_ms=1500,
        end_ms=4200,
        text="Hello world",
        language="en",
    )
    assert seg.start_ms == 1500
    assert seg.end_ms == 4200
    assert seg.start_time == 1.5
    assert seg.end_time == 4.2
    assert seg.raw_text == "Hello world"
    assert seg.speaker == "You (Mic)"

    d = seg.to_dict()
    assert d["start_ms"] == 1500
    assert d["end_ms"] == 4200
    assert d["start_time"] == 1.5
    assert d["end_time"] == 4.2


def test_provider_factory():
    """get_transcription_provider instantiates the correct class."""
    settings = get_test_settings()

    whisper_p = get_transcription_provider("faster_whisper", settings=settings)
    assert isinstance(whisper_p, FasterWhisperProvider)
    assert whisper_p.name == "faster_whisper"

    gemini_p = get_transcription_provider("gemini", settings=settings)
    assert isinstance(gemini_p, GeminiTranscriptionProvider)
    assert gemini_p.name == "gemini"

    sarvam_p = get_transcription_provider("sarvam", settings=settings)
    assert isinstance(sarvam_p, SarvamTranscriptionProvider)
    assert sarvam_p.name == "sarvam"

    deepgram_p = get_transcription_provider("deepgram", settings=settings)
    assert isinstance(deepgram_p, DeepgramTranscriptionProvider)
    assert deepgram_p.name == "deepgram"

    with pytest.raises(ValueError):
        get_transcription_provider("unsupported_provider", settings=settings)


def test_provider_availability():
    """Provider availability checks report status based on credentials and dependencies."""
    settings = get_test_settings()

    # Empty API keys should not be available
    settings.gemini_api_key = ""
    gemini_p = GeminiTranscriptionProvider(settings)
    assert gemini_p.is_available() is False

    # Valid key should make it available
    settings.gemini_api_key = "AIzaSyD-dummy-valid-key-for-testing"
    assert gemini_p.is_available() is True

    settings.sarvam_api_key = ""
    sarvam_p = SarvamTranscriptionProvider(settings)
    assert sarvam_p.is_available() is False

    settings.sarvam_api_key = "sarvam-dummy-key"
    assert sarvam_p.is_available() is True

    settings.deepgram_api_key = ""
    deepgram_p = DeepgramTranscriptionProvider(settings)
    assert deepgram_p.is_available() is False

    settings.deepgram_api_key = "deepgram-dummy-token-12345"
    assert deepgram_p.is_available() is True


def test_hindi_normalizer():
    """Hindi normalizer handles nuktas, zero-width characters, and dandas."""
    # क़िस्सा -> किस्सा (nukta removal)
    raw = "क़िस्सा।\u200D"
    normalized = normalize_hindi(raw)
    assert "क़" not in normalized
    assert "क" in normalized
    assert "।" not in normalized
    assert "\u200D" not in normalized


def test_hinglish_normalizer():
    """Hinglish normalizer handles case, punctuation, and common phonetic variants."""
    text = "Bhai kal ka deployment smoothly ho gya thaa, sooooo cool!"
    norm = normalize_hinglish(text)
    assert "bhai" in norm
    assert "so" in norm
    assert "!" not in norm
    assert "," not in norm


def test_wer_computation():
    """Word Error Rate computes correct metrics for exact, substitute, and deleted words."""
    # 1. Exact match
    res_exact = compute_word_error_rate("namaste meeting shuru karein", "namaste meeting shuru karein", mode="hinglish")
    assert res_exact.wer == 0.0
    assert res_exact.substitutions == 0
    assert res_exact.deletions == 0
    assert res_exact.insertions == 0

    # 2. Substitution
    res_sub = compute_word_error_rate("namaste meeting shuru karein", "namaste call shuru karein", mode="hinglish")
    assert res_sub.substitutions == 1
    assert res_sub.wer == 0.25

    # 3. Insertion
    res_ins = compute_word_error_rate("namaste meeting", "namaste call meeting", mode="hinglish")
    assert res_ins.insertions == 1

    # 4. Deletion
    res_del = compute_word_error_rate("namaste call meeting", "namaste meeting", mode="hinglish")
    assert res_del.deletions == 1


@pytest.mark.asyncio
async def test_two_channel_merging_and_pipeline():
    """Pipeline transcribes tab and mic, merges chronologically by start_ms, and saves to DB."""
    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Mock provider returning simulated segments for each channel
    mock_provider = MagicMock(spec=TranscriptionProvider)
    mock_provider.name = "mock_stt"

    async def mock_transcribe(audio_path, speaker="speaker", **kwargs):
        if "tab" in str(audio_path).lower():
            # Participant spoken at 2000ms - 4000ms
            return TranscriptionResult(
                segments=[
                    TranscriptionSegment(
                        speaker=speaker,
                        start_ms=2000,
                        end_ms=4000,
                        text="Participant question here",
                        raw_text="Participant question here",
                        language="en",
                    )
                ],
                duration_seconds=4.0,
            )
        else:
            # User spoken earlier at 500ms - 1800ms
            return TranscriptionResult(
                segments=[
                    TranscriptionSegment(
                        speaker=speaker,
                        start_ms=500,
                        end_ms=1800,
                        text="Welcome to the call",
                        raw_text="Welcome to the call",
                        language="en",
                    )
                ],
                duration_seconds=2.0,
            )

    mock_provider.transcribe = AsyncMock(side_effect=mock_transcribe)

    async with session_factory() as db:
        meeting = Meeting(title="Channel Merge Test", user_id=1, status=MeetingStatus.UPLOADING)
        db.add(meeting)
        await db.commit()
        await db.refresh(meeting)
        m_id = meeting.id

    # Create dummy wav files
    storage = Path(test_settings.audio_storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    tab_wav = storage / f"{m_id}_tab.wav"
    mic_wav = storage / f"{m_id}_mic.wav"

    import wave
    for p in (tab_wav, mic_wav):
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)

    with patch("backend.services.stt.get_transcription_provider", return_value=mock_provider):
        await process_meeting_transcription(
            meeting_id=m_id,
            tab_path=tab_wav,
            mic_path=mic_wav,
            settings=test_settings,
            session_factory=session_factory,
        )

    # Verify meeting status is READY and segments are sorted by start_ms
    async with session_factory() as db:
        from sqlalchemy import select
        res = await db.execute(select(Meeting).where(Meeting.id == m_id))
        updated_meeting = res.scalar_one()
        assert updated_meeting.status == MeetingStatus.READY

        seg_res = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m_id)
            .order_by(TranscriptSegment.start_ms)
        )
        saved_segs = list(seg_res.scalars().all())

        assert len(saved_segs) == 2
        # First segment should be user's welcome at 500ms
        assert saved_segs[0].start_ms == 500
        assert saved_segs[0].text == "Welcome to the call"
        assert saved_segs[0].speaker == "You (Mic)"

        # Second segment should be participant's question at 2000ms
        assert saved_segs[1].start_ms == 2000
        assert saved_segs[1].text == "Participant question here"
        assert saved_segs[1].speaker == "Participants (Tab)"

    await engine.dispose()
