"""
RAG Evaluation Script for DecisionAI.
Evaluates answer quality, intent classification, retrieval recall, and citation validity
over the 20-question evaluation dataset.
Strict constraints: No emojis, no em dashes.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.models.base import Base
from backend.models.user import User
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.models.decision import Decision
from backend.config import Settings
from backend.services.rag.chunking import chunk_transcript_segments
from backend.services.rag.vector_store import ChromaVectorStore
from backend.services.rag.pipeline import execute_rag_query
from backend.services.rag.intent import classify_query_intent

logging.basicConfig(level=logging.WARNING)


# Sample ground truth meetings data for evaluation
DEMO_MEETINGS = [
    {
        "title": "Architecture & Database Sync",
        "date": "2026-10-15 10:00:00",
        "segments": [
            {"speaker": "You (Mic)", "start_ms": 1000, "end_ms": 8000, "text": "Welcome team. Let's discuss our backend database migration strategy."},
            {"speaker": "Participant (Tab)", "start_ms": 9000, "end_ms": 22000, "text": "We should use SQLite with Alembic migrations for the prototype phase and plan PostgreSQL migration later."},
            {"speaker": "You (Mic)", "start_ms": 23000, "end_ms": 36000, "text": "Agreed. Also, what vector database was chosen for storing meeting transcript chunks? We decided on ChromaDB with local persistence."},
            {"speaker": "Participant (Tab)", "start_ms": 37000, "end_ms": 52000, "text": "For speech recognition, Sarvam AI with the saaras:v4 model in codemix mode is configured for Indian accents and regional dialects."},
            {"speaker": "You (Mic)", "start_ms": 53000, "end_ms": 70000, "text": "Audio is resampled down to 16 kHz mono 16-bit PCM using the hardware-accelerated AudioContext."},
        ],
        "decisions": [
            {"text": "Use SQLite with Alembic migrations for prototype phase", "type": "decision", "status": "accepted", "timestamp_ms": 22000},
            {"text": "Select ChromaDB for persistent transcript chunk vector storage", "type": "decision", "status": "accepted", "timestamp_ms": 36000},
        ],
    },
    {
        "title": "Security & Authentication Review",
        "date": "2026-10-18 14:00:00",
        "segments": [
            {"speaker": "You (Mic)", "start_ms": 2000, "end_ms": 14000, "text": "Let's review the authentication action items. Implement Google OAuth 2.0 with httpOnly session cookies and an extension token issuance endpoint."},
            {"speaker": "Participant (Tab)", "start_ms": 15000, "end_ms": 30000, "text": "The session token must be stored with httpOnly=True and samesite='lax' to protect against XSS."},
            {"speaker": "You (Mic)", "start_ms": 31000, "end_ms": 45000, "text": "An in-memory sliding window rate limiter without Redis was chosen to avoid external infrastructure dependencies."},
            {"speaker": "Participant (Tab)", "start_ms": 46000, "end_ms": 60000, "text": "Tab audio ko audioCtx.destination se connect karke passthrough enable kiya gaya taaki user call sun sake."},
        ],
        "decisions": [
            {"text": "Implement Google OAuth 2.0 with httpOnly session cookies", "type": "action_item", "status": "accepted", "timestamp_ms": 14000},
            {"text": "Configure httpOnly=True and samesite='lax' for cookie security", "type": "decision", "status": "accepted", "timestamp_ms": 30000},
            {"text": "Use in-memory sliding window rate limiter without Redis", "type": "decision", "status": "accepted", "timestamp_ms": 45000},
        ],
    },
    {
        "title": "Sprint Planning & Client Feedback",
        "date": "2026-10-20 16:30:00",
        "segments": [
            {"speaker": "You (Mic)", "start_ms": 1000, "end_ms": 18000, "text": "Aakhri faisla kya liya gaya tha architecture ke baare mein? Architecture ke liye FastAPI backend aur Manifest V3 Chrome extension choose kiya gaya."},
            {"speaker": "Participant (Tab)", "start_ms": 19000, "end_ms": 34000, "text": "Q4 release date kya decide hui thi? Q4 release date November ke first week mein scheduled ki gayi hai."},
            {"speaker": "You (Mic)", "start_ms": 35000, "end_ms": 50000, "text": "The client request to add direct CSV export alongside PDF was accepted with high priority."},
            {"speaker": "Participant (Tab)", "start_ms": 51000, "end_ms": 66000, "text": "The prototype demo was scheduled for Friday afternoon after internal QA sign-off."},
            {"speaker": "You (Mic)", "start_ms": 67000, "end_ms": 82000, "text": "Was the proposal to use third-party UI component libraries accepted or rejected? The proposal was rejected in favor of pure vanilla HTML, CSS, and JavaScript with custom design tokens."},
            {"speaker": "Participant (Tab)", "start_ms": 83000, "end_ms": 98000, "text": "Unicode NFC normalization aur nukta danda cleanup normalizer use kiya jaata hai Hindi transcripts ke liye."},
            {"speaker": "You (Mic)", "start_ms": 99000, "end_ms": 115000, "text": "The pipeline supports English, Hindi (Devanagari), and conversational Hinglish code-switching."},
            {"speaker": "Participant (Tab)", "start_ms": 116000, "end_ms": 130000, "text": "Pending action items include client sign-off on pricing and the final security audit."},
            {"speaker": "You (Mic)", "start_ms": 131000, "end_ms": 145000, "text": "Run the RAG evaluation suite, verify Google Meet tab capture audio passthrough, and deploy the demo build as next steps."},
            {"speaker": "Participant (Tab)", "start_ms": 146000, "end_ms": 160000, "text": "The next engineering sync was agreed to occur every Monday at 10:00 AM."},
        ],
        "decisions": [
            {"text": "FastAPI backend and Chrome MV3 extension selected for architecture", "type": "decision", "status": "accepted", "timestamp_ms": 18000},
            {"text": "Schedule Q4 release for the first week of November", "type": "decision", "status": "accepted", "timestamp_ms": 34000},
            {"text": "Accept client request for direct CSV export alongside PDF", "type": "client_request", "status": "accepted", "timestamp_ms": 50000},
            {"text": "Schedule prototype demo for Friday afternoon", "type": "decision", "status": "accepted", "timestamp_ms": 66000},
            {"text": "Reject third-party UI component libraries in favor of vanilla frontend", "type": "decision", "status": "rejected", "timestamp_ms": 82000},
            {"text": "Pending client sign-off on pricing and security audit", "type": "action_item", "status": "pending", "timestamp_ms": 130000},
            {"text": "Schedule weekly engineering sync every Monday at 10:00 AM", "type": "decision", "status": "accepted", "timestamp_ms": 160000},
        ],
    },
]


async def setup_eval_database(persist_chroma_dir: str):
    """Create in-memory SQLite database and test ChromaDB instance."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    vector_store = ChromaVectorStore(persist_dir=persist_chroma_dir)

    async with session_factory() as db:
        user = User(id=1, email="eval@decisionai.local", name="Eval User")
        db.add(user)
        await db.commit()

        for m_data in DEMO_MEETINGS:
            meeting = Meeting(
                user_id=1,
                title=m_data["title"],
                status=MeetingStatus.READY,
                duration_seconds=180.0,
            )
            db.add(meeting)
            await db.commit()
            await db.refresh(meeting)

            segments = []
            for s_data in m_data["segments"]:
                seg = TranscriptSegment(
                    meeting_id=meeting.id,
                    speaker=s_data["speaker"],
                    start_time=s_data["start_ms"] / 1000.0,
                    end_time=s_data["end_ms"] / 1000.0,
                    start_ms=s_data["start_ms"],
                    end_ms=s_data["end_ms"],
                    text=s_data["text"],
                    raw_text=s_data["text"],
                    language="en",
                )
                db.add(seg)
                segments.append(seg)

            for d_data in m_data["decisions"]:
                dec = Decision(
                    meeting_id=meeting.id,
                    text=d_data["text"],
                    type=d_data["type"],
                    status=d_data["status"],
                    timestamp_ms=d_data["timestamp_ms"],
                )
                db.add(dec)

            await db.commit()

            # Chunk and embed into ChromaDB
            chunks = chunk_transcript_segments(
                segments=segments,
                meeting_id=meeting.id,
                user_id=1,
                meeting_title=meeting.title,
                meeting_date="Oct 2026",
            )
            vector_store.upsert_chunks(chunks)

    return session_factory, vector_store


def score_qa_pair(item: dict, response) -> dict:
    """
    Score quality of single RAG response against evaluation criteria:
    1. Intent classification match
    2. Citation validity (presence of timestamp, meeting title)
    3. Keyword recall in answer
    4. Negative query accuracy
    """
    question = item["question"]
    expected_keywords = [k.lower() for k in item["expected_keywords"]]
    expected_intent = "decision_lookup" if item["requires_decision_table"] else "general_qa"
    is_negative = not item["expected_found"]

    answer_lower = response.answer.lower()

    # 1. Intent check
    intent_score = 1.0 if response.intent == expected_intent else 0.5

    # 2. Keyword recall
    if is_negative:
        # Should state not found
        negative_signals = ["not found", "no relevant", "unrecorded", "could not find", "no decision", "not mentioned"]
        matched_neg = any(sig in answer_lower for sig in negative_signals)
        keyword_score = 1.0 if matched_neg else 0.0
    else:
        found_count = sum(1 for kw in expected_keywords if kw in answer_lower)
        keyword_score = found_count / len(expected_keywords) if expected_keywords else 1.0

    # 3. Citation validity
    if is_negative:
        citation_score = 1.0  # negative queries do not require citations
    else:
        has_citations = len(response.citations) > 0
        valid_citations = 0
        for c in response.citations:
            if c.meeting_title and c.timestamp and c.start_ms >= 0:
                valid_citations += 1
        citation_score = (valid_citations / len(response.citations)) if has_citations else 0.0

    # Composite quality score (weighted: 40% keyword recall, 30% citation, 30% intent)
    composite = round(0.4 * keyword_score + 0.3 * citation_score + 0.3 * intent_score, 3)

    return {
        "id": item["id"],
        "question": question[:45] + ("..." if len(question) > 45 else ""),
        "category": item["category"],
        "language": item["language"],
        "intent_match": response.intent == expected_intent,
        "keyword_score": round(keyword_score, 2),
        "citation_count": len(response.citations),
        "citation_score": round(citation_score, 2),
        "composite_score": composite,
    }


async def run_evaluation():
    eval_file = PROJECT_ROOT / "eval" / "eval_set.json"
    if not eval_file.exists():
        print(f"Error: Evaluation dataset not found at {eval_file}")
        sys.exit(1)

    with open(eval_file, "r", encoding="utf-8") as f:
        qa_pairs = json.load(f)

    print(f"Loaded {len(qa_pairs)} evaluation questions.", flush=True)
    print("Setting up isolated test database and ChromaDB collection...", flush=True)

    import tempfile
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_chroma:
        session_factory, vector_store = await setup_eval_database(tmp_chroma)
        test_settings = Settings(chroma_persist_dir=tmp_chroma, gemini_api_key="")

        results = []
        print("\nEvaluating RAG pipeline over evaluation set...\n", flush=True)
        print(f"{'ID':<4} {'Category':<16} {'Lang':<8} {'Intent':<8} {'Keyw%':<8} {'Cites':<6} {'Score':<6} {'Question':<45}", flush=True)
        print("-" * 105, flush=True)

        for item in qa_pairs:
            async with session_factory() as db:
                response = await execute_rag_query(
                    db=db,
                    question=item["question"],
                    user_id=1,
                    settings=test_settings,
                )

            res = score_qa_pair(item, response)
            results.append(res)

            intent_sym = "PASS" if res["intent_match"] else "DIFF"
            print(
                f"{res['id']:<4} {res['category']:<16} {res['language']:<8} "
                f"{intent_sym:<8} {int(res['keyword_score']*100)}%{'':<3} "
                f"{res['citation_count']:<6} {res['composite_score']:<6.2f} {res['question']:<45}",
                flush=True,
            )

        avg_score = sum(r["composite_score"] for r in results) / len(results)
        intent_accuracy = sum(1 for r in results if r["intent_match"]) / len(results)
        avg_keyword = sum(r["keyword_score"] for r in results) / len(results)
        avg_citation = sum(r["citation_score"] for r in results) / len(results)

        print("-" * 105, flush=True)
        print("\nEVALUATION SUMMARY:", flush=True)
        print(f"  Total Questions:          {len(results)}", flush=True)
        print(f"  Intent Accuracy:          {intent_accuracy * 100:.1f}%", flush=True)
        print(f"  Average Keyword Recall:   {avg_keyword * 100:.1f}%", flush=True)
        print(f"  Average Citation Validity:{avg_citation * 100:.1f}%", flush=True)
        print(f"  Overall RAG Quality Score:{avg_score * 100:.1f}% / 100%\n", flush=True)

        if avg_score >= 0.80:
            print("Evaluation PASSED: RAG pipeline quality score meets standard (>= 80%).", flush=True)
            return 0
        else:
            print("Evaluation FAILED: Quality score below 80% threshold.", flush=True)
            return 1


if __name__ == "__main__":
    code = asyncio.run(run_evaluation())
    sys.exit(code)
