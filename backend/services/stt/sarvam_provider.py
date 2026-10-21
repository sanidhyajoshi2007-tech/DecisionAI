"""
Sarvam AI Speech-to-Text Provider.
State-of-the-art transcription for 22 Indian languages, Indian English, and Hinglish.
"""

import logging
from pathlib import Path
import wave

import httpx

from backend.config import Settings, get_settings
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)


class SarvamTranscriptionProvider(TranscriptionProvider):
    """
    Cloud speech-to-text provider powered by Sarvam AI (saaras:v4).
    Purpose-built for Indian accents, regional dialects, and code-mixed Hinglish.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.endpoint = "https://api.sarvam.ai/speech-to-text"

    @property
    def api_key(self) -> str:
        return self.settings.sarvam_api_key

    @property
    def model(self) -> str:
        return self.settings.sarvam_model or "saaras:v4"

    @property
    def mode(self) -> str:
        return self.settings.sarvam_mode or "codemix"

    @property
    def name(self) -> str:
        return "sarvam"

    def is_available(self) -> bool:
        """Available if a valid SARVAM_API_KEY is configured."""
        key = self.api_key
        return bool(
            key
            and not key.startswith("your-")
            and len(key.strip()) > 8
        )

    def _get_audio_duration_seconds(self, audio_path: Path) -> float:
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
                "SARVAM_API_KEY is not set or invalid in .env file. "
                "Get an API key from https://sarvam.ai with ₹100 free credits."
            )

        duration = self._get_audio_duration_seconds(audio_path)
        lang_code = language or "hi-IN"

        headers = {
            "api-subscription-key": self.api_key,
        }

        # Sarvam expects multipart/form-data
        data = {
            "model": self.model,
            "mode": self.mode,
            "language_code": lang_code,
            "with_diarization": "false",
        }

        # Include glossary / keyterms if provided
        effective_glossary = list(self.settings.glossary_list)
        if glossary:
            effective_glossary.extend(glossary)
        effective_glossary = list(dict.fromkeys(effective_glossary))
        if effective_glossary:
            data["prompt"] = ", ".join(effective_glossary)

        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(audio_path, "rb") as f:
                files = {
                    "file": (audio_path.name, f, "audio/wav"),
                }
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    data=data,
                    files=files,
                )

        if resp.status_code != 200:
            logger.error(f"Sarvam AI error ({resp.status_code}): {resp.text}")
            raise RuntimeError(f"Sarvam AI error ({resp.status_code}): {resp.text}")

        res_data = resp.json()
        transcript = res_data.get("transcript", "").strip()
        detected_lang = res_data.get("language_code", lang_code)

        segments: list[TranscriptionSegment] = []

        # Check if word-level or utterance timestamps are returned
        words_info = res_data.get("timestamps", {}).get("words", [])
        if words_info:
            current_words: list[str] = []
            cur_start: int | None = None
            cur_end: int = 0

            for w in words_info:
                word_str = w.get("word", "").strip()
                w_start = chunk_offset_ms + int(float(w.get("start_time", 0.0)) * 1000)
                w_end = chunk_offset_ms + int(float(w.get("end_time", 0.0)) * 1000)

                if cur_start is None:
                    cur_start = w_start
                cur_end = w_end
                current_words.append(word_str)

                # Split into natural sentence-level segments every ~5-8 seconds or punctuation
                if (w_end - cur_start >= 5000) or any(word_str.endswith(p) for p in [".", "?", "!", "।"]):
                    text_chunk = " ".join(current_words).strip()
                    if text_chunk:
                        segments.append(
                            TranscriptionSegment(
                                speaker=speaker,
                                start_ms=cur_start,
                                end_ms=cur_end,
                                text=text_chunk,
                                raw_text=text_chunk,
                                language=detected_lang,
                            )
                        )
                    current_words = []
                    cur_start = None

            if current_words and cur_start is not None:
                text_chunk = " ".join(current_words).strip()
                if text_chunk:
                    segments.append(
                        TranscriptionSegment(
                            speaker=speaker,
                            start_ms=cur_start,
                            end_ms=cur_end,
                            text=text_chunk,
                            raw_text=text_chunk,
                            language=detected_lang,
                        )
                    )

        if not segments and transcript:
            # Fallback to single segment spanning duration
            segments.append(
                TranscriptionSegment(
                    speaker=speaker,
                    start_ms=chunk_offset_ms,
                    end_ms=chunk_offset_ms + int(duration * 1000),
                    text=transcript,
                    raw_text=transcript,
                    language=detected_lang,
                )
            )

        return TranscriptionResult(
            segments=segments,
            duration_seconds=duration,
            language=detected_lang,
            provider_name=self.name,
        )
