"""
Tests for RAG pipeline, transcript chunking, ChromaDB vector store,
hybrid search, intent classification, and /ask endpoint.
Strict constraints: No emojis, no em dashes.
"""

import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings
from backend.models.decision import Decision
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.models.user import User
from backend.services.rag.chunking import TranscriptChunk, chunk_transcript_segments, estimate_tokens
from backend.services.rag.hybrid_search import reciprocal_rank_fusion, run_hybrid_search, tokenize_for_bm25
from backend.services.rag.intent import (
    classify_query_intent,
    is_advisory_query,
    is_decision_query,
)
from backend.services.rag.pipeline import execute_rag_query, index_meeting_transcript
from backend.services.rag.vector_store import ChromaVectorStore, MultilingualEmbeddingFunction
from backend.services.auth import create_session_token



def test_estimate_tokens():
    """Verify multilingual token estimation."""
    assert estimate_tokens("") == 0
    en_text = "This is a standard English meeting dialogue line."
    assert estimate_tokens(en_text) > 5

    hi_text = "यह एक हिंदी मीटिंग का संवाद है।"
    assert estimate_tokens(hi_text) > 4


def test_transcript_chunking_speaker_turns_and_tokens():
    """Verify that transcript segments are aggregated by speaker turns up to target tokens."""
    # Create synthetic segments
    segments = []
    for i in range(12):
        speaker = "You (Mic)" if i % 2 == 0 else "Participant (Tab)"
        text = f"Discussion point number {i}. We are evaluating the architecture choices for the project."
        segments.append(
            TranscriptSegment(
                id=i + 1,
                speaker=speaker,
                start_ms=i * 5000,
                end_ms=(i * 5000) + 4000,
                start_time=float(i * 5),
                end_time=float((i * 5) + 4),
                text=text,
                raw_text=text,
            )
        )

    chunks = chunk_transcript_segments(
        segments=segments,
        meeting_id=10,
        user_id=1,
        meeting_title="Q4 Architecture Review",
        meeting_date="Oct 23, 2026",
        min_tokens=50,
        max_tokens=150,
    )

    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, TranscriptChunk)
        assert c.meeting_id == 10
        assert c.user_id == 1
        assert c.meeting_title == "Q4 Architecture Review"
        assert c.date == "Oct 23, 2026"
        assert c.start_ms >= 0
        assert c.end_ms >= c.start_ms
        assert len(c.text) > 0


def test_multilingual_embedding_fallback():
    """Verify that MultilingualEmbeddingFunction produces normalized vectors."""
    fn = MultilingualEmbeddingFunction(model_name="dummy-nonexistent-model")
    vectors = fn(["Hello world", "नमस्ते भारत", "Project deployment status"])
    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 384
        # Check normalized magnitude approximately 1.0
        magnitude = sum(x * x for x in vec) ** 0.5
        assert 0.95 <= magnitude <= 1.05


def test_chroma_vector_store_user_scoping(tmp_path):
    """Verify ChromaDB stores chunks and enforces user_id isolation."""
    store = ChromaVectorStore(persist_dir=str(tmp_path / "chroma_test"))

    chunk_u1 = TranscriptChunk(
        chunk_id="1_0",
        meeting_id=1,
        user_id=100,
        meeting_title="User 100 Meeting",
        date="Oct 20, 2026",
        speakers="You (Mic)",
        start_ms=0,
        end_ms=5000,
        chunk_index=0,
        text="Confidential project details for user 100.",
        token_count=10,
    )
    chunk_u2 = TranscriptChunk(
        chunk_id="2_0",
        meeting_id=2,
        user_id=200,
        meeting_title="User 200 Meeting",
        date="Oct 21, 2026",
        speakers="Participant (Tab)",
        start_ms=0,
        end_ms=6000,
        chunk_index=0,
        text="Confidential project details for user 200.",
        token_count=10,
    )

    store.upsert_chunks([chunk_u1, chunk_u2])

    # Search as User 100: must only get User 100 chunks
    hits_u1 = store.search("Confidential", user_id=100)
    assert len(hits_u1) == 1
    assert hits_u1[0]["metadata"]["user_id"] == 100
    assert hits_u1[0]["metadata"]["meeting_id"] == 1

    # Search as User 200: must only get User 200 chunks
    hits_u2 = store.search("Confidential", user_id=200)
    assert len(hits_u2) == 1
    assert hits_u2[0]["metadata"]["user_id"] == 200
    assert hits_u2[0]["metadata"]["meeting_id"] == 2


def test_hybrid_search_bm25_and_rrf():
    """Verify BM25 tokenization and Reciprocal Rank Fusion."""
    tokens = tokenize_for_bm25("Hello World! नमस्ते 123 project.")
    assert "hello" in tokens
    assert "world" in tokens
    assert "नमस्ते" in tokens

    vector_hits = [
        {"chunk_id": "c1", "text": "general overview of the system"},
        {"chunk_id": "c2", "text": "database migration instructions"},
    ]
    bm25_hits = [
        {"chunk_id": "c2", "text": "database migration instructions"},
        {"chunk_id": "c3", "text": "database configuration settings"},
    ]

    fused = reciprocal_rank_fusion(vector_hits, bm25_hits)
    assert len(fused) == 3
    # c2 was present in both, so it should rank highest
    assert fused[0]["chunk_id"] == "c2"


def test_intent_classification():
    """Verify intent classifier on English, Hindi, and Hinglish queries."""
    # Decision queries
    assert is_decision_query("What was the final decision on the database?") is True
    assert is_decision_query("What are the latest decisions made?") is True
    assert is_decision_query("List all pending action items") is True
    assert is_decision_query("Aakhri faisla kya hua meeting mein?") is True
    assert is_decision_query("kya decide hua tha kal?") is True
    assert classify_query_intent("What did we decide about Q4?") == "decision_lookup"

    # General queries
    assert is_decision_query("Hello how are you?") is False
    assert is_decision_query("Tell me about the project timeline.") is False
    assert classify_query_intent("Who spoke in the first 5 minutes?") == "general_qa"

    # Advisory and consultative queries
    assert is_advisory_query("do u think we can add anything else vaulable to iit") is True
    assert is_advisory_query("how can we improve this architecture?") is True
    assert is_advisory_query("is there anything missing from our plan?") is True
    assert is_advisory_query("kya isme kuch aur add kar sakte hain?") is True
    assert is_advisory_query("What was the final decision?") is False



@pytest.mark.asyncio
async def test_post_ask_endpoint_json(client: AsyncClient, test_session_factory):
    """Test POST /api/ask and POST /ask with JSON response."""
    # Seed a test meeting with decisions
    async with test_session_factory() as db:
        meeting = Meeting(
            id=99,
            user_id=1,
            title="API Design Sync",
            status=MeetingStatus.READY,
        )
        db.add(meeting)
        await db.commit()

        dec = Decision(
            meeting_id=99,
            text="Adopt REST API with SSE streaming for RAG",
            type="decision",
            status="accepted",
            timestamp_ms=15000,
        )
        db.add(dec)
        await db.commit()

    # Query without stream
    resp = await client.post(
        "/api/ask",
        json={"question": "What was the latest decision?", "meeting_id": 99, "stream": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert data["intent"] == "decision_lookup"
    assert len(data["decisions"]) >= 1
    assert data["decisions"][0]["meeting_id"] == 99


@pytest.mark.asyncio
async def test_post_ask_streaming_sse(client: AsyncClient, test_session_factory):
    """Test POST /api/ask with SSE stream."""
    async with test_session_factory() as db:
        meeting = Meeting(
            id=101,
            user_id=1,
            title="Streaming Test Meeting",
            status=MeetingStatus.READY,
        )
        db.add(meeting)
        await db.commit()

    resp = await client.post(
        "/api/ask",
        json={"question": "Tell me about the meeting", "meeting_id": 101, "stream": True},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "data: " in text


@pytest.mark.asyncio
async def test_user_isolation_on_ask(client: AsyncClient, test_session_factory):
    """Verify that User A cannot query User B's meeting via meeting_id scope."""
    async with test_session_factory() as db:
        # Create meeting for foreign user (id=999)
        meeting = Meeting(
            id=888,
            user_id=999,
            title="Foreign User Meeting",
            status=MeetingStatus.READY,
        )
        db.add(meeting)
        await db.commit()

    # Client is authenticated as dev user (id=1)
    resp = await client.post(
        "/api/ask",
        json={"question": "What happened?", "meeting_id": 888},
    )
    # Must reject with 404
    assert resp.status_code == 404
    assert "does not belong to you" in resp.json()["detail"]
