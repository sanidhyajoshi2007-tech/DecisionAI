"""
Google Gemini Audio Transcription Provider.
Supports Gemini 3.8 Flash, Gemini 3.5 Flash, and Gemini 2.5 Flash.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Any
import wave

import httpx

from backend.config import Settings, get_settings
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)


class GeminiTranscriptionProvider(TranscriptionProvider):
    """
    Cloud speech-to-text provider powered by Google Gemini API.
    Excels at multilingual audio understanding, Hinglish code-switching,
    contextual punctuation, and domain vocabulary.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def api_key(self) -> str:
        return self.settings.gemini_api_key

    @property
    def model(self) -> str:
        return self.settings.gemini_transcription_model or "gemini-3.8-flash"

    @property
    def name(self) -> str:
        return "gemini"

    def is_available(self) -> bool:
        """Available if a non-placeholder GEMINI_API_KEY is configured."""
        key = self.api_key
        return bool(
            key
            and not key.startswith("your-")
            and len(key.strip()) > 10
        )

    def _get_audio_duration_seconds(self, audio_path: Path) -> float:
        """Calculate WAV audio duration from header."""
        try:
            with wave.open(str(audio_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            pass
        return 0.0

    async def transcribe(
        self,
        audio_path: Path,
        speaker: str = "speaker",
        language: str | None = None,
        glossary: list[str] | None = None,
        chunk_offset_ms: int = 0,
    ) -> TranscriptionResult:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.is_available():
            raise RuntimeError(
                "GEMINI_API_KEY is not set or invalid in .env file. "
                "Set GEMINI_API_KEY=AIza... to use Gemini audio transcription."
            )

        duration = self._get_audio_duration_seconds(audio_path)
        audio_bytes = audio_path.read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        # Compile glossary terms
        effective_glossary = list(self.settings.glossary_list)
        if glossary:
            effective_glossary.extend(glossary)
        effective_glossary = list(dict.fromkeys(effective_glossary))
        glossary_hint = (
            f"Pay special attention to correctly spelling these domain terms and proper names: "
            f"{', '.join(effective_glossary)}."
            if effective_glossary
            else ""
        )

        lang_instruction = (
            f"The primary expected language is '{language}'."
            if language
            else "Automatically detect the spoken language (Hindi, English, or mixed Hinglish)."
        )

        system_prompt = (
            "You are an expert multilingual audio transcriber specializing in Hindi, English, and conversational Hinglish. "
            "Accurately transcribe all speech in the audio file.\n"
            f"{lang_instruction}\n"
            f"{glossary_hint}\n\n"
            "Output MUST be valid JSON with this exact structure:\n"
            "{\n"
            '  "detected_language": "hi" | "en" | "hinglish",\n'
            '  "segments": [\n'
            '    {\n'
            '      "start_ms": 0,\n'
            '      "end_ms": 3500,\n'
            '      "text": "Transcribed text with accurate punctuation and casing",\n'
            '      "raw_text": "verbatim text with any spoken fillers",\n'
            '      "language": "hi"\n'
            '    }\n'
            "  ]\n"
            "}\n"
            "Rules:\n"
            "1. start_ms and end_ms must be integer millisecond offsets relative to the start of this audio clip.\n"
            "2. If speakers code-switch between Hindi and English, capture the words accurately.\n"
            "3. Return ONLY the JSON object. Do not wrap in markdown or explanation."
        )

        models_to_try = [self.model]
        for fallback in ["gemini-3.5-flash", "gemini-3.5-transcribe", "gemini-3.8-flash"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": system_prompt},
                        {
                            "inline_data": {
                                "mime_type": "audio/wav",
                                "data": audio_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        }

        data = None
        last_error = None
        async with httpx.AsyncClient(timeout=90.0) as client:
            for candidate_model in models_to_try:
                url = f"{self.base_url}/models/{candidate_model}:generateContent?key={self.api_key}"
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        break
                    elif resp.status_code in (503, 429, 404):
                        logger.warning(
                            f"Model '{candidate_model}' returned {resp.status_code}. "
                            f"Trying fallback model..."
                        )
                        last_error = f"Gemini API error ({resp.status_code}): {resp.text}"
                        continue
                    else:
                        resp_text = resp.text
                        logger.error(f"Gemini API error ({resp.status_code}): {resp_text}")
                        raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp_text}")
                except httpx.RequestError as req_err:
                    last_error = f"Request error: {req_err}"
                    continue

        if data is None:
            raise RuntimeError(last_error or "All candidate Gemini models failed")

        # Extract generated content text
        raw_text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for p in parts:
                if "text" in p:
                    raw_text += p["text"]

        # Parse JSON output
        segments: list[TranscriptionSegment] = []
        detected_language = language

        try:
            parsed = json.loads(raw_text)
            detected_language = parsed.get("detected_language", language)
            raw_segments = parsed.get("segments", [])
            for seg in raw_segments:
                s_ms = chunk_offset_ms + int(seg.get("start_ms", 0))
                e_ms = chunk_offset_ms + int(seg.get("end_ms", s_ms + 1000))
                txt = seg.get("text", "").strip()
                if txt:
                    segments.append(
                        TranscriptionSegment(
                            speaker=speaker,
                            start_ms=s_ms,
                            end_ms=max(e_ms, s_ms + 100),
                            text=txt,
                            raw_text=seg.get("raw_text", txt),
                            language=seg.get("language", detected_language),
                        )
                    )
        except Exception as e:
            logger.warning(f"Could not parse Gemini JSON response as segments: {e}. Raw: {raw_text[:200]}")
            # Fallback: treat entire response text as a single segment
            cleaned = raw_text.strip()
            if cleaned:
                segments.append(
                    TranscriptionSegment(
                        speaker=speaker,
                        start_ms=chunk_offset_ms,
                        end_ms=chunk_offset_ms + int(duration * 1000),
                        text=cleaned,
                        raw_text=cleaned,
                        language=language,
                    )
                )

        return TranscriptionResult(
            segments=segments,
            duration_seconds=duration,
            language=detected_language,
            provider_name=self.name,
        )
