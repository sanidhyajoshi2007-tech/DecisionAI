r"""
DecisionAI - LLM Pipeline Interactive Test Script.

This script simulates a completed meeting with multi-speaker Hinglish conversation,
triggers the LLM cleanup, summary, and structured decisions extraction,
and prints the resulting data with a link to inspect in the web UI.

Usage:
    .\venv\Scripts\python scripts/test_pipeline.py
"""

import asyncio
import logging
import os
import sys

# Suppress noisy SQL logs
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# Configure safe stdout/stderr encoding on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure repository root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from backend.config import get_settings
from backend.database import async_session_factory, engine
from backend.models.base import Base
from backend.models.decision import Decision
from backend.models.meeting import Meeting, MeetingStatus, TranscriptSegment
from backend.crud.users import create_user, get_user_by_email
from backend.crud.meetings import (
    create_meeting,
    create_segments,
    update_meeting_summary,
    update_segment_text,
)
from backend.crud.decisions import bulk_create_decisions
from backend.providers.llm import get_llm_provider


async def main():
    print("=" * 65)
    print("  [DecisionAI] LLM Post-Processing Pipeline Test & Demo")
    print("=" * 65)

    settings = get_settings()
    llm = get_llm_provider(settings=settings)
    print(f"[*] Provider:        {llm.name.upper()}")
    print(f"[*] API Key Status:  {'Configured' if llm.is_available() else 'Offline / Fallback mode'}")
    print(f"[*] Target Model:    {getattr(llm, 'model', 'default')}")
    print()

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1. Create or fetch demo user and meeting in 'recording' state
    from backend.auth import _get_or_create_dev_user
    async with async_session_factory() as db:
        user = await _get_or_create_dev_user(db)
        meeting = await create_meeting(
            db=db,
            user_id=user.id,
            title="Q4 Sprint & Product Architecture Review",
            status=MeetingStatus.RECORDING,
        )
        await db.commit()
        meeting_id = meeting.id

    print(f"[+] Created Meeting #{meeting_id}: '{meeting.title}'")
    print(f"    Initial Status: [{meeting.status.value.upper()}]")

    # 2. Simulate multi-speaker Hindi/English/Hinglish conversation segments
    sample_segments = [
        {
            "speaker": "You (Mic)",
            "start_time": 2.0,
            "end_time": 6.5,
            "start_ms": 2000,
            "end_ms": 6500,
            "text": "arre yaar, fast api aur postgresql use karenge to real-time latency kam hogi.",
            "raw_text": "arre yaar, fast api aur postgresql use karenge to real-time latency kam hogi.",
            "language": "hi",
        },
        {
            "speaker": "Client (Tab)",
            "start_time": 8.0,
            "end_time": 13.2,
            "start_ms": 8000,
            "end_ms": 13200,
            "text": "We need automated PDF export for weekly client reports before next Monday.",
            "raw_text": "We need automated PDF export for weekly client reports before next Monday.",
            "language": "en",
        },
        {
            "speaker": "You (Mic)",
            "start_time": 15.0,
            "end_time": 19.5,
            "start_ms": 15000,
            "end_ms": 19500,
            "text": "haan theek hai, we accepted the client request for PDF exports.",
            "raw_text": "haan theek hai, we accepted the client request for PDF exports.",
            "language": "hi",
        },
        {
            "speaker": "Engineering Lead (Tab)",
            "start_time": 21.0,
            "end_time": 26.0,
            "start_ms": 21000,
            "end_ms": 26000,
            "text": "Can we delay the websocket migration? No, team rejected the delay. We proceed as planned.",
            "raw_text": "Can we delay the websocket migration? No, team rejected the delay. We proceed as planned.",
            "language": "en",
        },
        {
            "speaker": "You (Mic)",
            "start_time": 28.0,
            "end_time": 33.0,
            "start_ms": 28000,
            "end_ms": 33000,
            "text": "Action item for Rahul: implement DecisionAI session authentication by Friday 5 PM.",
            "raw_text": "Action item for Rahul: implement DecisionAI session authentication by Friday 5 PM.",
            "language": "en",
        },
    ]

    print(f"\n[*] Storing {len(sample_segments)} conversation segments (Hindi + English + Hinglish)...")
    async with async_session_factory() as db:
        created_segs = await create_segments(db, meeting_id, sample_segments)
        await db.commit()

    # 3. Simulate meeting finish -> Trigger LLM processing
    print(f"[*] Simulating meeting completion -> Setting status to [PROCESSING]...")
    async with async_session_factory() as db:
        res = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
        m = res.scalar_one()
        m.status = MeetingStatus.PROCESSING
        await db.commit()

    print("[*] Running LLM post-processing pipeline:")
    print("    - Task 1: Domain Glossary cleanup (preserving raw_text and conversational Hinglish)")
    print("    - Task 2: Executive meeting summary generation")
    print("    - Task 3: Structured extraction of decisions, action items, and client requests")

    segment_dicts = [
        {
            "id": s.id,
            "text": s.text,
            "raw_text": s.raw_text,
            "start_ms": s.start_ms,
            "speaker": s.speaker,
        }
        for s in created_segs
    ]

    glossary = ["FastAPI", "PostgreSQL", "WebSocket", "DecisionAI"]
    ai_result = await llm.process_meeting(
        segments=segment_dicts,
        glossary=glossary,
        meeting_title="Q4 Sprint & Product Architecture Review",
    )

    # 4. Save results to Database
    async with async_session_factory() as db:
        for cleaned in ai_result.cleaned_segments:
            await update_segment_text(db, cleaned.id, cleaned.cleaned_text)

        if ai_result.summary:
            await update_meeting_summary(db, meeting_id, ai_result.summary)

        if ai_result.decisions:
            d_dicts = [d.model_dump() for d in ai_result.decisions]
            await bulk_create_decisions(db, meeting_id, d_dicts)

        # Mark meeting ready
        res = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
        m = res.scalar_one()
        m.status = MeetingStatus.READY
        await db.commit()

    # 5. Print Results
    print("\n" + "=" * 65)
    print("  RESULTS SUMMARY")
    print("=" * 65)

    print("\n[EXECUTIVE SUMMARY]")
    print("-" * 50)
    print(ai_result.summary)

    print("\n[EXTRACTED DECISIONS, ACTION ITEMS & CLIENT REQUESTS]")
    print("-" * 50)
    for idx, d in enumerate(ai_result.decisions, 1):
        ts_sec = d.timestamp_ms / 1000.0
        time_str = f"{int(ts_sec // 60):02d}:{int(ts_sec % 60):02d}"
        status_tag = f"[{d.status.upper()}]"
        print(f" {idx}. [{d.type.upper()}] {status_tag} (at {time_str})")
        print(f"    \"{d.text}\"")

    print("\n[GLOSSARY-CLEANED TRANSCRIPT (RAW vs CLEANED)]")
    print("-" * 50)
    async with async_session_factory() as db:
        seg_res = await db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == meeting_id)
            .order_by(TranscriptSegment.start_ms)
        )
        for s in seg_res.scalars().all():
            print(f"[{s.speaker}]:")
            print(f"  * Raw:     {s.raw_text}")
            print(f"  * Cleaned: {s.text}")

    print("\n" + "=" * 65)
    print(f"  Final Meeting Status: [READY]")
    print(f"  View in Browser:      http://localhost:8000/meetings/{meeting_id}")
    print("=" * 65)
    print("  TIP: If your browser shows 'Site can't be reached', start the")
    print("       backend server in another terminal:")
    print("       .\\venv\\Scripts\\uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000")
    print("=" * 65)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
