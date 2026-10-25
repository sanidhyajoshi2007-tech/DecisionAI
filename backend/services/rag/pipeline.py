"""
RAG Pipeline Module for DecisionAI.
Orchestrates:
1. Intent classification
2. Decision table queries (for last/latest/final decisions)
3. Hybrid (BM25 + ChromaDB Vector) retrieval
4. LLMProvider context synthesis with citations
5. Multi-tenant user_id filtering at every step
Strict constraints: No emojis, no em dashes.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, AsyncGenerator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.models.decision import Decision
from backend.models.meeting import Meeting, TranscriptSegment
from backend.providers.llm import get_llm_provider
from backend.schemas.rag import AskResponse, Citation, DecisionItem
from backend.services.rag.chunking import chunk_transcript_segments, format_timestamp_ms
from backend.services.rag.hybrid_search import run_hybrid_search
from backend.services.rag.intent import classify_query_intent, is_decision_query
from backend.services.rag.vector_store import get_vector_store

logger = logging.getLogger(__name__)


def format_meeting_date(dt: datetime | None) -> str:
    """Format datetime into standard readable date string."""
    if not dt:
        return "Unknown Date"
    return dt.strftime("%b %d, %Y")


async def index_meeting_transcript(
    db: AsyncSession,
    meeting_id: int,
    user_id: int,
    settings: Settings | None = None,
) -> int:
    """
    Chunk meeting transcript segments and embed into ChromaDB.
    Enforces user_id metadata attribution.
    """
    # Fetch meeting
    m_res = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    meeting = m_res.scalar_one_or_none()
    if not meeting:
        logger.warning(f"Meeting #{meeting_id} not found for indexing (user {user_id}).")
        return 0

    # Fetch segments
    s_res = await db.execute(
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == meeting_id)
        .order_by(TranscriptSegment.start_ms.asc())
    )
    segments = list(s_res.scalars().all())
    if not segments:
        logger.info(f"Meeting #{meeting_id} has no transcript segments to index.")
        return 0

    date_str = format_meeting_date(meeting.started_at or meeting.created_at)

    # Chunk transcript segments (300-500 tokens)
    chunks = chunk_transcript_segments(
        segments=segments,
        meeting_id=meeting.id,
        user_id=user_id,
        meeting_title=meeting.title or f"Meeting #{meeting.id}",
        meeting_date=date_str,
    )

    if not chunks:
        return 0

    # Upsert into ChromaDB
    vs = get_vector_store(settings=settings)
    count = vs.upsert_chunks(chunks)
    logger.info(f"Indexed {count} chunks for meeting #{meeting_id} into ChromaDB.")
    return count


async def fetch_decisions_for_query(
    db: AsyncSession,
    user_id: int,
    meeting_id: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Query the Decision table sorted by date/timestamp, strictly scoped to user_id.
    """
    stmt = (
        select(Decision, Meeting)
        .join(Meeting, Decision.meeting_id == Meeting.id)
        .where(Meeting.user_id == user_id)
    )
    if meeting_id is not None:
        stmt = stmt.where(Meeting.id == meeting_id)

    # Sort latest meetings and timestamps first
    stmt = stmt.order_by(Meeting.created_at.desc(), Decision.timestamp_ms.desc()).limit(limit)

    res = await db.execute(stmt)
    rows = res.all()

    items = []
    for dec, mtg in rows:
        date_str = format_meeting_date(mtg.started_at or mtg.created_at)
        time_str = format_timestamp_ms(dec.timestamp_ms)
        items.append({
            "id": dec.id,
            "meeting_id": mtg.id,
            "meeting_title": mtg.title or f"Meeting #{mtg.id}",
            "text": dec.text,
            "type": dec.type or "decision",
            "status": dec.status or "accepted",
            "timestamp_ms": dec.timestamp_ms,
            "timestamp": time_str,
            "date": date_str,
        })
    return items


def build_rag_prompt(
    question: str,
    intent: str,
    decisions: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> str:
    """
    Construct prompt for the LLM containing retrieved context and conversational instructions.
    """
    context_sections = []

    if decisions:
        dec_lines = []
        for d in decisions:
            dec_lines.append(
                f"- Recorded {d['type'].upper()} (Status: {d['status']}): {d['text']}"
            )
        context_sections.append("RECORDED DECISIONS AND ACTION ITEMS:\n" + "\n".join(dec_lines))

    if chunks:
        chunk_lines = []
        for c in chunks:
            meta = c.get("metadata", {})
            title = meta.get("meeting_title", "Meeting")
            date_str = meta.get("date", "")
            chunk_lines.append(
                f"--- Excerpt from '{title}' ({date_str}) ---\n"
                f"{c.get('text', '')}"
            )
        context_sections.append("TRANSCRIPT DISCUSSION EXCERPTS:\n" + "\n\n".join(chunk_lines))

    context_str = "\n\n".join(context_sections) if context_sections else "No relevant records found."

    prompt = f"""You are DecisionAI, an executive AI meeting assistant.
Answer the user's question directly, clearly, and conversationally, just like a helpful chatbot.

RULES:
1. Synthesize your answer in natural, fluent prose using ONLY the context provided below.
2. DO NOT quote raw transcript lines, timestamps (e.g. [00:03]), or dump verbatim bracketed dialogue.
3. DO NOT repeat the meeting title, date, or timestamps in your narrative response body; citation chips are automatically attached below the response.
4. PARTICIPANT ATTRIBUTION:
   - ONLY mention who said or proposed something if the user explicitly asks 'who said this', 'who mentioned', or asks about speakers/participants.
   - For general questions (such as 'what was this meeting about?', 'explain the project/architecture', 'what was discussed?'), synthesize the facts and concepts naturally without attributing every sentence to a speaker.
5. If the transcript contains overlapping audio (e.g., microphone audio explaining a project alongside background tab audio), focus on the primary subject matter and answer the user's inquiry accurately.
6. Support English, Hindi, and Hinglish dialogue naturally.
7. If the answer cannot be determined from the recorded context, state directly that the topic was not found in recorded meetings.

CONTEXT:
{context_str}

USER QUESTION:
{question}
"""
    return prompt


def generate_fallback_answer(
    question: str,
    intent: str,
    decisions: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> str:
    """
    Deterministic synthesis for offline testing or when external LLM API is unavailable.
    Combines relevant decisions and hybrid transcript excerpts matching the query.
    Synthesizes natural language responses instead of raw bracketed transcript dumps.
    """
    from backend.services.rag.hybrid_search import tokenize_for_bm25

    q_lower = question.lower()
    asks_speaker = any(w in q_lower for w in ["who", "speaker", "participant", "kisne", "kisme"])

    q_tokens = set(tokenize_for_bm25(question))
    stopwords = {"what", "was", "were", "the", "did", "we", "is", "are", "about", "for", "in", "to", "of", "and", "a", "an", "on", "with", "kya", "tha", "thi", "ke", "hai", "kaunsa", "ko", "kaise"}
    content_tokens = q_tokens - stopwords

    def compute_overlap(tokens_a: set[str], tokens_b: set[str]) -> int:
        score = 0
        for a in tokens_a:
            for b in tokens_b:
                if a == b:
                    score += 2
                    break
                elif len(a) >= 4 and len(b) >= 4 and (a.startswith(b[:4]) or b.startswith(a[:4])):
                    score += 1
                    break
        return score

    # Score decisions by content keyword overlap
    scored_decisions = []
    for d in decisions:
        d_tokens = set(tokenize_for_bm25(f"{d['text']} {d.get('meeting_title', '')}"))
        overlap = compute_overlap(content_tokens, d_tokens)
        scored_decisions.append((overlap, d))

    scored_decisions.sort(key=lambda x: (x[0], x[1]["timestamp_ms"]), reverse=True)
    matching_decisions = [d for score, d in scored_decisions if score > 0]

    # Score chunks by content keyword overlap
    scored_chunks = []
    for c in chunks:
        c_text = c.get("text", "")
        c_tokens = set(tokenize_for_bm25(c_text))
        overlap = compute_overlap(content_tokens, c_tokens)
        scored_chunks.append((overlap, c))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    matching_chunks = [c for score, c in scored_chunks if score > 0]

    # Negative control check: if question has specific keywords and neither matching decisions nor chunks exist
    if content_tokens and not matching_decisions and not matching_chunks:
        return f"No relevant meeting discussions or recorded decisions were found matching '{question}' in the recorded meetings archive."

    parts = []

    # If matching decisions found, highlight the most relevant decision in natural language
    if matching_decisions:
        best_d = matching_decisions[0]
        parts.append(
            f"The recorded {best_d['type']} was to {best_d['text']} (Status: {best_d['status']})."
        )
        if len(matching_decisions) > 1:
            more_items = [
                f"- {d['text']} (Status: {d['status']})"
                for d in matching_decisions[1:3]
            ]
            parts.append("Additional related items:\n" + "\n".join(more_items))
    elif intent == "decision_lookup" and decisions:
        latest = decisions[0]
        parts.append(
            f"The latest recorded {latest['type']} was: {latest['text']} (Status: {latest['status']})."
        )

    # Append dialogue context from matching or top chunks
    chunks_to_use = matching_chunks if matching_chunks else chunks
    if chunks_to_use:
        top_c = chunks_to_use[0]
        lines = [line.strip() for line in top_c.get("text", "").split("\n") if line.strip()]
        matched_lines = [
            l for l in lines
            if compute_overlap(content_tokens, set(tokenize_for_bm25(l))) > 0
        ]
        chosen_lines = matched_lines if matched_lines else lines[:3]

        cleaned_statements = []
        for l in chosen_lines:
            m = re.match(r"^\[\d{2}:\d{2}\]\s*(.*?):\s*(.*)$", l)
            if m:
                spk, speech = m.group(1), m.group(2).strip()
                if asks_speaker:
                    cleaned_statements.append(f"{spk} mentioned that {speech.rstrip('.')}.")
                else:
                    cleaned_statements.append(speech.rstrip("."))
            else:
                cleaned_text = re.sub(r"^\[\d{2}:\d{2}\]\s*", "", l).strip()
                cleaned_statements.append(cleaned_text.rstrip("."))

        if cleaned_statements:
            if asks_speaker:
                discussion_summary = " ".join(cleaned_statements)
            else:
                discussion_summary = ". ".join(cleaned_statements) + "."

            if not matching_decisions or intent != "decision_lookup":
                parts.append(f"Regarding this topic: {discussion_summary}")
            elif discussion_summary:
                parts.append(f"Context: {discussion_summary}")

    if not parts:
        return "No relevant meeting discussions or recorded decisions were found matching your question."

    return "\n\n".join(parts)



async def execute_rag_query(
    db: AsyncSession,
    question: str,
    user_id: int,
    meeting_id: int | None = None,
    settings: Settings | None = None,
) -> AskResponse:
    """
    Execute full RAG retrieval pipeline and return complete AskResponse.
    """
    settings = settings or get_settings()

    # 1. Classify intent
    intent = classify_query_intent(question)
    is_dec = is_decision_query(question)

    # 2. Query Decision table if decision query or generally
    decisions_data: list[dict[str, Any]] = []
    if is_dec:
        decisions_data = await fetch_decisions_for_query(
            db=db,
            user_id=user_id,
            meeting_id=meeting_id,
            limit=10,
        )

    # 3. Hybrid retrieval (ChromaDB Vector + BM25)
    vs = get_vector_store(settings=settings)
    vector_hits = vs.search(
        query=question,
        user_id=user_id,
        meeting_id=meeting_id,
        top_k=8,
    )
    user_chunks = vs.get_user_chunks(
        user_id=user_id,
        meeting_id=meeting_id,
        limit=500,
    )
    fused_chunks = run_hybrid_search(
        query=question,
        vector_hits=vector_hits,
        all_user_chunks=user_chunks,
        top_k=6,
    )

    # 4. Build citations
    citations: list[Citation] = []
    seen_citations = set()

    # Decisions citations
    for d in decisions_data:
        key = (d["meeting_id"], d["timestamp_ms"], d["text"][:30])
        if key not in seen_citations:
            seen_citations.add(key)
            citations.append(
                Citation(
                    meeting_id=d["meeting_id"],
                    meeting_title=d["meeting_title"],
                    date=d["date"],
                    timestamp=d["timestamp"],
                    start_ms=d["timestamp_ms"],
                    text=d["text"],
                    source_type="decision",
                )
            )

    # Transcript chunk citations
    for c in fused_chunks:
        meta = c.get("metadata", {})
        mid = meta.get("meeting_id", 0)
        start_ms = meta.get("start_ms", 0)
        title = meta.get("meeting_title", f"Meeting #{mid}")
        date_str = meta.get("date", "")
        time_str = format_timestamp_ms(start_ms)
        text_snippet = c.get("text", "")[:160]

        key = (mid, start_ms)
        if key not in seen_citations:
            seen_citations.add(key)
            citations.append(
                Citation(
                    meeting_id=mid,
                    meeting_title=title,
                    date=date_str,
                    timestamp=time_str,
                    start_ms=start_ms,
                    text=text_snippet,
                    source_type="transcript",
                )
            )

    # 5. LLM Synthesis
    prompt = build_rag_prompt(
        question=question,
        intent=intent,
        decisions=decisions_data,
        chunks=fused_chunks,
    )

    answer_text = ""
    llm = get_llm_provider(settings=settings)
    if llm.is_available():
        candidate_models = ["gemini-3.5-flash", "gemini-flash-latest"]
        if llm.model not in candidate_models:
            candidate_models.insert(0, llm.model)

        for cand_model in candidate_models:
            try:
                url = f"{llm.base_url}/models/{cand_model}:generateContent?key={llm.api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2},
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.post(url, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts and "text" in parts[0]:
                                answer_text = parts[0]["text"].strip()
                                if answer_text:
                                    break
            except Exception as e:
                logger.warning(f"Gemini RAG call error on {cand_model}: {e}. Trying next candidate.")

    if not answer_text:
        answer_text = generate_fallback_answer(
            question=question,
            intent=intent,
            decisions=decisions_data,
            chunks=fused_chunks,
        )

    decision_items = [
        DecisionItem(
            id=d["id"],
            meeting_id=d["meeting_id"],
            meeting_title=d["meeting_title"],
            text=d["text"],
            type=d["type"],
            status=d["status"],
            timestamp_ms=d["timestamp_ms"],
            date=d["date"],
        )
        for d in decisions_data
    ]

    return AskResponse(
        answer=answer_text,
        intent=intent,
        citations=citations[:8],
        decisions=decision_items[:5],
        retrieved_chunks_count=len(fused_chunks),
    )


async def stream_rag_query(
    db: AsyncSession,
    question: str,
    user_id: int,
    meeting_id: int | None = None,
    settings: Settings | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream RAG response tokens and final citations using Server-Sent Events (SSE).
    """
    settings = settings or get_settings()

    # Fetch response
    response = await execute_rag_query(
        db=db,
        question=question,
        user_id=user_id,
        meeting_id=meeting_id,
        settings=settings,
    )

    # Stream answer in word chunks to simulate smooth delivery
    words = response.answer.split(" ")
    buffer = []
    for word in words:
        buffer.append(word)
        if len(buffer) >= 3:
            token_chunk = " ".join(buffer) + " "
            yield f"data: {json.dumps({'token': token_chunk})}\n\n"
            buffer = []

    if buffer:
        token_chunk = " ".join(buffer)
        yield f"data: {json.dumps({'token': token_chunk})}\n\n"

    # Emit final structured payload with citations and intent
    final_payload = {
        "intent": response.intent,
        "citations": [c.model_dump() for c in response.citations],
        "decisions": [d.model_dump() for d in response.decisions],
        "done": True,
    }
    yield f"data: {json.dumps(final_payload)}\n\n"
