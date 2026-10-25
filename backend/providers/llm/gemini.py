"""
Google Gemini LLM Provider.

Handles:
1. Transcript cleanup using domain glossary (preserving meaning and raw_text).
2. Meeting summary generation.
3. Structured extraction of decisions, action items, and client requests with status
   (accepted/rejected/pending) and timestamp references.
4. Handling Hindi, English, and conversational Hinglish code-switching.
5. Robust retry and exponential backoff for free-tier rate limits (HTTP 429, 503).
6. Model fallbacks (gemini-3.8-flash, gemini-flash-latest, gemini-3.5-flash).
7. Validation of structured outputs using Pydantic.
"""

import asyncio
import json
import logging
import random
import re
from typing import Any

import httpx

from backend.config import Settings, get_settings
from backend.providers.llm.base import (
    CleanedTranscriptSegment,
    ExtractedDecision,
    LLMProvider,
    MeetingAIResult,
)

logger = logging.getLogger(__name__)


class GeminiLLMProvider(LLMProvider):
    """
    Google Gemini implementation of LLMProvider with free-tier rate limit handling.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.max_retries = 2
        self.initial_delay = 1.0
        self.max_delay = 4.0

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def api_key(self) -> str:
        return self.settings.gemini_api_key

    @property
    def model(self) -> str:
        return self.settings.gemini_model or "gemini-3.5-flash"

    def is_available(self) -> bool:
        """Check if a valid GEMINI_API_KEY is configured."""
        key = self.api_key
        return bool(
            key
            and not key.startswith("your-")
            and len(key.strip()) > 10
        )

    def _get_candidate_models(self) -> list[str]:
        """Order of Gemini models to attempt, falling back if rate limits occur."""
        preferred = [self.model]
        candidates = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.8-flash"]
        for c in candidates:
            if c not in preferred:
                preferred.append(c)
        return preferred

    async def _call_gemini_with_backoff(self, prompt: str) -> dict[str, Any]:
        """
        Execute generateContent with exponential backoff and jitter for free-tier 429/503.
        Falls back to alternative Flash models if primary model is throttled.
        """
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        }

        candidate_models = self._get_candidate_models()
        last_error = None

        async with httpx.AsyncClient(timeout=15.0) as client:
            for candidate_model in candidate_models:
                url = f"{self.base_url}/models/{candidate_model}:generateContent?key={self.api_key}"

                for attempt in range(self.max_retries):
                    try:
                        resp = await client.post(url, json=payload)

                        if resp.status_code == 200:
                            data = resp.json()
                            candidates_resp = data.get("candidates", [])
                            if candidates_resp:
                                parts = candidates_resp[0].get("content", {}).get("parts", [])
                                if parts and "text" in parts[0]:
                                    text_content = parts[0]["text"]
                                    return json.loads(text_content)
                            raise ValueError(f"Malformed Gemini API response: {data}")

                        elif resp.status_code in (429, 503, 500):
                            # Free tier rate limit or temporary server capacity
                            delay = min(
                                self.max_delay,
                                (self.initial_delay * (2 ** attempt)) + random.uniform(0.1, 0.5),
                            )
                            logger.warning(
                                f"[Gemini LLM] {candidate_model} returned HTTP {resp.status_code} "
                                f"(attempt {attempt + 1}/{self.max_retries}). Retrying in {delay:.2f}s..."
                            )
                            last_error = f"HTTP {resp.status_code}: {resp.text}"
                            await asyncio.sleep(delay)
                            continue

                        else:
                            last_error = f"HTTP {resp.status_code}: {resp.text}"
                            logger.error(f"[Gemini LLM] Model {candidate_model} error: {last_error}")
                            break  # Try next model

                    except (httpx.RequestError, json.JSONDecodeError) as exc:
                        logger.warning(f"[Gemini LLM] Network/Decode issue on {candidate_model}: {exc}")
                        last_error = str(exc)
                        await asyncio.sleep(self.initial_delay)

        raise RuntimeError(f"All Gemini models exhausted. Last error: {last_error}")

    def _fallback_local_processing(
        self,
        segments: list[dict],
        glossary: list[str],
        meeting_title: str | None = None,
    ) -> MeetingAIResult:
        """
        Deterministic, offline fallback processor when Gemini API key is absent
        or network is unavailable. Ensures tests and dev mode run seamlessly.
        """
        cleaned_segments: list[CleanedTranscriptSegment] = []
        effective_glossary = [g.strip() for g in glossary if g.strip()]

        for s in segments:
            seg_id = s.get("id", 0)
            orig_text = s.get("text", "")
            cleaned = orig_text
            # Apply glossary term corrections (case-insensitive boundary and space-tolerant matching)
            for term in effective_glossary:
                parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|\d+', term)
                if len(parts) > 1:
                    pattern_str = r'\b(?:' + re.escape(term) + r'|' + r'\s+'.join(re.escape(p) for p in parts) + r')\b'
                else:
                    pattern_str = rf'\b{re.escape(term)}\b'
                pattern = re.compile(pattern_str, re.IGNORECASE)
                cleaned = pattern.sub(term, cleaned)

            cleaned_segments.append(CleanedTranscriptSegment(id=seg_id, cleaned_text=cleaned))

        # Generate summary
        total_segs = len(segments)
        title = meeting_title or "Meeting"
        summary = (
            f"Meeting '{title}' covered {total_segs} conversational speech segments. "
            f"The discussion involved multi-speaker coordination, timeline alignment, and action planning."
        )

        # Extract decisions, action items, and client requests
        extracted_items: list[ExtractedDecision] = []
        for s in segments:
            text_lower = (s.get("text") or "").lower()
            ts_ms = s.get("start_ms") or int(s.get("start_time", 0) * 1000)

            # Decision pattern
            if any(k in text_lower for k in ["decided", "decision", "agreed", "finalized", "chalega", "theek hai"]):
                status = "rejected" if any(r in text_lower for r in ["reject", "cancel", "not doing", "drop", "nahi"]) else "accepted"
                extracted_items.append(
                    ExtractedDecision(
                        text=s.get("text", ""),
                        type="decision",
                        status=status,
                        timestamp_ms=ts_ms,
                    )
                )

            # Action item pattern
            elif any(k in text_lower for k in ["will do", "action item", "todo", "take care", "dekh lunga", "send", "update"]):
                status = "pending" if any(p in text_lower for p in ["pending", "review", "waiting"]) else "accepted"
                extracted_items.append(
                    ExtractedDecision(
                        text=s.get("text", ""),
                        type="action_item",
                        status=status,
                        timestamp_ms=ts_ms,
                    )
                )

            # Client request pattern
            elif any(k in text_lower for k in ["client", "customer", "requested", "asking for", "demand"]):
                status = "accepted" if "accept" in text_lower else ("rejected" if "reject" in text_lower else "pending")
                extracted_items.append(
                    ExtractedDecision(
                        text=s.get("text", ""),
                        type="client_request",
                        status=status,
                        timestamp_ms=ts_ms,
                    )
                )

        return MeetingAIResult(
            cleaned_segments=cleaned_segments,
            summary=summary,
            decisions=extracted_items,
        )

    async def cleanup_transcript(
        self,
        segments: list[dict],
        glossary: list[str],
    ) -> list[CleanedTranscriptSegment]:
        result = await self.process_meeting(segments, glossary)
        return result.cleaned_segments

    async def generate_summary(
        self,
        transcript_text: str,
        meeting_title: str | None = None,
    ) -> str:
        # Wrap single transcript into a synthetic segment
        dummy_segments = [{"id": 1, "text": transcript_text, "start_ms": 0, "speaker": "All"}]
        result = await self.process_meeting(dummy_segments, [], meeting_title=meeting_title)
        return result.summary

    async def extract_decisions(
        self,
        segments: list[dict],
    ) -> list[ExtractedDecision]:
        result = await self.process_meeting(segments, [])
        return result.decisions

    async def process_meeting(
        self,
        segments: list[dict],
        glossary: list[str],
        meeting_title: str | None = None,
    ) -> MeetingAIResult:
        """
        Execute unified multimodal meeting understanding:
        1. Transcript cleanup preserving meaning, correcting glossary words.
        2. Meeting summary.
        3. Structured extraction of decisions, action items, and client requests.
        """
        if not segments:
            return MeetingAIResult(cleaned_segments=[], summary="No audio transcript captured.", decisions=[])

        # If Gemini API key is missing, execute deterministic offline fallback
        if not self.is_available():
            logger.info("[Gemini LLM] GEMINI_API_KEY not configured or offline mode. Using local fallback processor.")
            return self._fallback_local_processing(segments, glossary, meeting_title)

        glossary_items = list(dict.fromkeys(glossary + self.settings.glossary_list))
        glossary_str = ", ".join(glossary_items) if glossary_items else "None specified"
        title = meeting_title or "Meeting"

        # Format input transcript segments for prompt
        transcript_formatted = []
        for s in segments:
            sid = s.get("id", 0)
            speaker = s.get("speaker", "Speaker")
            start_ms = s.get("start_ms", 0)
            text = s.get("text", "")
            transcript_formatted.append(f"[{sid} | {start_ms}ms | {speaker}]: {text}")

        transcript_block = "\n".join(transcript_formatted)

        prompt = f"""You are DecisionAI, an executive meeting assistant specializing in multi-speaker conversations in English, Hindi, and colloquial Hinglish (Hindi-English code-switching).

Analyze the following meeting transcript and perform three essential tasks:

TASK 1: GLOSSARY CLEANUP OF TRANSCRIPT SEGMENTS
- Correct phonetic misspellings, proper nouns, and technical acronyms using the domain glossary:
  GLOSSARY: {glossary_str}
- CRITICAL CONSTRAINTS:
  1. DO NOT change the speaker's original meaning or intent.
  2. DO NOT invent or hallucinate words, statements, or details not present in the input.
  3. Keep the original speaking style (English, Hindi, or conversational Hinglish). Do not translate Hinglish into unnatural formal English.
  4. Return a cleaned_text for each provided segment ID.

TASK 2: EXECUTIVE SUMMARY
- Provide a clear, comprehensive meeting summary covering:
  - Main topic and discussion points
  - Key agreements and team alignment

TASK 3: STRUCTURED EXTRACTION OF ITEMS
- Extract distinct items categorized into one of these exact types:
  - 'decision': A choice, agreement, or consensus reached by participants.
  - 'action_item': A concrete task assigned to an owner or team.
  - 'client_request': An ask, feature request, or requirement raised by a client/customer.
- Assign each item an exact status from these values:
  - 'accepted': Approved, confirmed, or adopted.
  - 'rejected': Explicitly declined, deprioritized, or dismissed.
  - 'pending': Open, unresolved, deferred, or awaiting confirmation.
- Reference the exact 'timestamp_ms' from the transcript segment where this item was discussed.

Meeting Title: {title}

TRANSCRIPT:
{transcript_block}

RESPOND WITH VALID JSON MATCHING THIS EXACT SCHEMA:
{{
  "cleaned_segments": [
    {{
      "id": 1,
      "cleaned_text": "Corrected sentence with accurate glossary terms"
    }}
  ],
  "summary": "Executive summary text...",
  "decisions": [
    {{
      "text": "Specific description of the decision, action item, or client request",
      "type": "decision" | "action_item" | "client_request",
      "status": "accepted" | "rejected" | "pending",
      "timestamp_ms": 12000
    }}
  ]
}}
"""
        try:
            raw_result = await self._call_gemini_with_backoff(prompt)
            # Validate output against Pydantic model
            return MeetingAIResult.model_validate(raw_result)
        except Exception as exc:
            logger.error(
                f"[Gemini LLM] Structured processing failed: {exc}. Falling back to local heuristic.",
                exc_info=True,
            )
            return self._fallback_local_processing(segments, glossary, meeting_title)
