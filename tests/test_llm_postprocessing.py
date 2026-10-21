"""
Tests for LLM post-processing pipeline:
- (1) Transcript cleanup using domain glossary without changing meaning or inventing content, keeping raw_text
- (2) Executive summary generation
- (3) Structured extraction of decisions, action items, and client requests with status (accepted/rejected/pending)
- (4) Pluggable LLMProvider interface (Gemini default) with Pydantic validation
- (5) Hindi and Hinglish code-switching support
- (6) Rate limit retry & backoff behavior (HTTP 429, 503)
- (7) Meeting status lifecycle (recording -> processing -> ready / failed)
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import Settings
from backend.models.base import Base
from backend.models.decision import Decision
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.models.user import User
from backend.crud.users import create_user
from backend.crud.meetings import create_meeting, create_segments, get_segments_by_meeting_id
from backend.providers.llm import get_llm_provider
from backend.providers.llm.base import (
    CleanedTranscriptSegment,
    ExtractedDecision,
    LLMProvider,
    MeetingAIResult,
)
from backend.providers.llm.gemini import GeminiLLMProvider
from backend.services.stt.pipeline import process_meeting_transcription
from tests.conftest import get_test_settings


def test_pydantic_schema_validation():
    """Verify Pydantic models enforce valid types, statuses, and schema constraints."""
    # Valid model validation
    valid_data = {
        "cleaned_segments": [
            {"id": 1, "cleaned_text": "Hum FastAPI use karenge."}
        ],
        "summary": "Team agreed to use FastAPI for the backend architecture.",
        "decisions": [
            {
                "text": "Adopt FastAPI backend",
                "type": "decision",
                "status": "accepted",
                "timestamp_ms": 1500,
            },
            {
                "text": "Refactor database migrations",
                "type": "action_item",
                "status": "pending",
                "timestamp_ms": 3200,
            },
            {
                "text": "Client requested export in PDF",
                "type": "client_request",
                "status": "rejected",
                "timestamp_ms": 5000,
            },
        ],
    }

    result = MeetingAIResult.model_validate(valid_data)
    assert len(result.cleaned_segments) == 1
    assert result.cleaned_segments[0].cleaned_text == "Hum FastAPI use karenge."
    assert len(result.decisions) == 3
    assert result.decisions[0].type == "decision"
    assert result.decisions[0].status == "accepted"
    assert result.decisions[1].type == "action_item"
    assert result.decisions[1].status == "pending"
    assert result.decisions[2].type == "client_request"
    assert result.decisions[2].status == "rejected"

    # Invalid status should raise ValidationError
    with pytest.raises(Exception):
        MeetingAIResult.model_validate({
            "cleaned_segments": [],
            "summary": "Summary",
            "decisions": [
                {
                    "text": "Invalid status item",
                    "type": "decision",
                    "status": "in_progress",  # Not in accepted/rejected/pending
                    "timestamp_ms": 100,
                }
            ],
        })


def test_llm_provider_factory():
    """Factory should default to GeminiLLMProvider."""
    settings = get_test_settings()
    provider = get_llm_provider("gemini", settings=settings)
    assert isinstance(provider, GeminiLLMProvider)
    assert provider.name == "gemini"


@pytest.mark.asyncio
async def test_llm_offline_fallback_glossary_cleanup_and_extraction():
    """
    Test deterministic offline fallback processor when API key is unavailable:
    - Cleans glossary terms without altering raw text
    - Preserves Hindi/Hinglish code-switching
    - Generates summary
    - Extracts decisions, action items, and client requests
    """
    settings = Settings(
        gemini_api_key="",  # Force offline fallback mode
        glossary="FastAPI, PostgreSQL, WebSocket, DecisionAI",
    )
    provider = GeminiLLMProvider(settings=settings)
    assert not provider.is_available()

    input_segments = [
        {
            "id": 10,
            "text": "Arre yaar, fast api aur postgresql use karenge to latency kam hogi.",
            "raw_text": "Arre yaar, fast api aur postgresql use karenge to latency kam hogi.",
            "start_ms": 2500,
            "speaker": "You (Mic)",
        },
        {
            "id": 11,
            "text": "Client requested export feature for reports. We rejected the flash export.",
            "raw_text": "Client requested export feature for reports. We rejected the flash export.",
            "start_ms": 8200,
            "speaker": "Participant (Tab)",
        },
        {
            "id": 12,
            "text": "Action item for Rahul: implement websocket authentication before Friday.",
            "raw_text": "Action item for Rahul: implement websocket authentication before Friday.",
            "start_ms": 14000,
            "speaker": "You (Mic)",
        },
        {
            "id": 13,
            "text": "We decided to deploy DecisionAI on Google Cloud.",
            "raw_text": "We decided to deploy DecisionAI on Google Cloud.",
            "start_ms": 19500,
            "speaker": "Participant (Tab)",
        },
    ]

    result = await provider.process_meeting(
        segments=input_segments,
        glossary=["FastAPI", "PostgreSQL", "WebSocket", "DecisionAI"],
        meeting_title="Sprint Architecture Review",
    )

    # 1. Transcript Cleanup
    assert len(result.cleaned_segments) == 4
    seg10_cleaned = next(s for s in result.cleaned_segments if s.id == 10)
    # Fast api -> FastAPI, postgresql -> PostgreSQL
    assert "FastAPI" in seg10_cleaned.cleaned_text
    assert "PostgreSQL" in seg10_cleaned.cleaned_text
    # Meaning and Hinglish preserved
    assert "Arre yaar" in seg10_cleaned.cleaned_text
    assert "latency kam hogi" in seg10_cleaned.cleaned_text

    # 2. Summary
    assert len(result.summary) > 0
    assert "Sprint Architecture Review" in result.summary or "meeting" in result.summary.lower()

    # 3. Decisions & Action Items Extraction
    assert len(result.decisions) >= 3
    types_found = {d.type for d in result.decisions}
    assert "decision" in types_found
    assert "action_item" in types_found
    assert "client_request" in types_found

    statuses_found = {d.status for d in result.decisions}
    assert "accepted" in statuses_found or "pending" in statuses_found or "rejected" in statuses_found


@pytest.mark.asyncio
async def test_gemini_retry_exponential_backoff_on_429():
    """
    Verify GeminiLLMProvider handles HTTP 429 rate limits with exponential backoff
    and succeeds once rate limit clears.
    """
    settings = Settings(
        gemini_api_key="AIzaSyDummyKeyForMockingGeminiRateLimits",
        gemini_model="gemini-2.5-flash",
    )
    provider = GeminiLLMProvider(settings=settings)
    provider.initial_delay = 0.05  # Fast delays for test speed
    provider.max_delay = 0.2

    mock_success_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "cleaned_segments": [
                                    {"id": 1, "cleaned_text": "Sanidhiya said we will deploy FastAPI tomorrow."}
                                ],
                                "summary": "Team agreed to deploy FastAPI tomorrow.",
                                "decisions": [
                                    {
                                        "text": "Deploy FastAPI tomorrow",
                                        "type": "decision",
                                        "status": "accepted",
                                        "timestamp_ms": 1000,
                                    }
                                ],
                            })
                        }
                    ]
                }
            }
        ]
    }

    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.text = "Resource has been exhausted (rate limit)."

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = mock_success_response

    # First two attempts return 429, third attempt succeeds with 200
    call_counts = 0

    async def mock_post(url, **kwargs):
        nonlocal call_counts
        call_counts += 1
        if call_counts < 3:
            return mock_resp_429
        return mock_resp_200

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        result = await provider._call_gemini_with_backoff("Test prompt")
        assert "cleaned_segments" in result
        assert call_counts == 3  # Tried twice, backed off, succeeded on third try


@pytest.mark.asyncio
async def test_end_to_end_pipeline_postprocessing_and_status():
    """
    Full integration test of process_meeting_transcription background task:
    1. Status starts at RECORDING
    2. Changes to PROCESSING while transcribing & post-processing
    3. LLM cleans transcript while preserving raw_text
    4. Meeting summary is stored
    5. Decisions, action items, and client requests are bulk inserted into DB
    6. Meeting status finishes at READY
    """
    test_settings = get_test_settings()
    engine = create_async_engine(test_settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = await create_user(db, email="dev@decisionai.io", name="Dev User")
        meeting = await create_meeting(
            db,
            user_id=user.id,
            title="Q4 Product Roadmap Sync",
            status=MeetingStatus.RECORDING,
        )
        await db.commit()
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

    # Mock STT provider to return realistic multi-speaker segments
    from backend.services.stt.base import TranscriptionProvider, TranscriptionResult, TranscriptionSegment
    mock_stt = MagicMock(spec=TranscriptionProvider)
    mock_stt.name = "mock_stt"

    async def mock_transcribe(audio_path, speaker="speaker", **kwargs):
        if "tab" in str(audio_path).lower():
            return TranscriptionResult(
                segments=[
                    TranscriptionSegment(
                        speaker="Client (Tab)",
                        start_ms=1000,
                        end_ms=3500,
                        text="client requested dark mode support for mobile app.",
                        raw_text="client requested dark mode support for mobile app.",
                        language="en",
                    ),
                    TranscriptionSegment(
                        speaker="Client (Tab)",
                        start_ms=8000,
                        end_ms=11000,
                        text="Can we delay the release? No, we rejected the delay.",
                        raw_text="Can we delay the release? No, we rejected the delay.",
                        language="en",
                    ),
                ],
                duration_seconds=12.0,
            )
        else:
            return TranscriptionResult(
                segments=[
                    TranscriptionSegment(
                        speaker="You (Mic)",
                        start_ms=4000,
                        end_ms=7500,
                        text="haan bilkul, action item for team: we will deliver fast api backend first.",
                        raw_text="haan bilkul, action item for team: we will deliver fast api backend first.",
                        language="hi",
                    )
                ],
                duration_seconds=12.0,
            )

    mock_stt.transcribe = AsyncMock(side_effect=mock_transcribe)

    with patch("backend.services.stt.get_transcription_provider", return_value=mock_stt):
        await process_meeting_transcription(
            meeting_id=m_id,
            tab_path=tab_wav,
            mic_path=mic_wav,
            settings=test_settings,
            session_factory=session_factory,
            glossary=["FastAPI", "DecisionAI"],
        )

    # Verify DB assertions
    async with session_factory() as db:
        # 1. Meeting status is READY
        res = await db.execute(select(Meeting).where(Meeting.id == m_id))
        final_meeting = res.scalar_one()
        assert final_meeting.status == MeetingStatus.READY

        # 2. Executive summary is populated
        assert final_meeting.summary is not None
        assert len(final_meeting.summary) > 10

        # 3. Segments have cleaned text and intact raw_text
        seg_res = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m_id)
            .order_by(TranscriptSegment.start_ms)
        )
        saved_segs = list(seg_res.scalars().all())
        assert len(saved_segs) == 3

        # Check raw_text was preserved
        for seg in saved_segs:
            assert seg.raw_text is not None
            assert len(seg.raw_text) > 0

        # Check glossary term 'FastAPI' was cleaned in Mic segment
        mic_seg = next(s for s in saved_segs if "Mic" in s.speaker)
        assert "FastAPI" in mic_seg.text
        assert "fast api" in mic_seg.raw_text.lower()
        # Meaning and Hindi preserved
        assert "haan bilkul" in mic_seg.text

        # 4. Decisions, Action Items, and Client Requests are extracted in Decision table
        dec_res = await db.execute(
            select(Decision)
            .where(Decision.meeting_id == m_id)
            .order_by(Decision.timestamp_ms)
        )
        saved_decisions = list(dec_res.scalars().all())
        assert len(saved_decisions) >= 2

        dec_types = {d.type for d in saved_decisions}
        assert "action_item" in dec_types or "client_request" in dec_types or "decision" in dec_types

        # Timestamp references exist
        for d in saved_decisions:
            assert d.timestamp_ms >= 0
            assert d.status in ("accepted", "rejected", "pending")

    await engine.dispose()
