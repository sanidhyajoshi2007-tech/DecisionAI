"""
Deepgram Speech-to-Text Provider (Nova-3 / Nova-2).
High-speed multilingual transcription with keyword boosting and smart formatting.
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


class DeepgramTranscriptionProvider(TranscriptionProvider):
    """
    Cloud speech-to-text provider powered by Deepgram Nova-3.
    Features ultra-low latency, smart formatting, and keyword phoneme boosting.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.endpoint = "https://api.deepgram.com/v1/listen"

    @property
    def api_key(self) -> str:
        return self.settings.deepgram_api_key

    @property
    def model(self) -> str:
        return self.settings.deepgram_model or "nova-3"

    @property
    def name(self) -> str:
        return "deepgram"

    def is_available(self) -> bool:
        """Available if a valid DEEPGRAM_API_KEY is configured."""
        key = self.api_key
        return bool(
            key
            and not key.startswith("your-")
            and len(key.strip()) > 10
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
                "DEEPGRAM_API_KEY is not set or invalid in .env file. "
                "Get an API key from https://deepgram.com with $200 free credits."
            )

        duration = self._get_audio_duration_seconds(audio_path)
        audio_bytes = audio_path.read_bytes()

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }

        # Query params
        params: dict[str, Any] = {
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
        }

        if language:
            params["language"] = language
        else:
            params["language"] = "hi"

        # Keywords / glossary hints with boost factor
        effective_glossary = list(self.settings.glossary_list)
        if glossary:
            effective_glossary.extend(glossary)
        effective_glossary = list(dict.fromkeys(effective_glossary))
        if effective_glossary:
            # Deepgram supports keywords query params: keywords=Term:2
            params["keywords"] = [f"{term}:2" for term in effective_glossary]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self.endpoint,
                headers=headers,
                params=params,
                content=audio_bytes,
            )

        if resp.status_code != 200:
            logger.error(f"Deepgram API error ({resp.status_code}): {resp.text}")
            raise RuntimeError(f"Deepgram API error ({resp.status_code}): {resp.text}")

        res_data = resp.json()
        results = res_data.get("results", {})
        utterances = results.get("utterances", [])

        segments: list[TranscriptionSegment] = []
        detected_lang = language

        if utterances:
            for utt in utterances:
                u_text = utt.get("transcript", "").strip()
                if not u_text:
                    continue
                start_ms = chunk_offset_ms + int(float(utt.get("start", 0.0)) * 1000)
                end_ms = chunk_offset_ms + int(float(utt.get("end", 0.0)) * 1000)
                segments.append(
                    TranscriptionSegment(
                        speaker=speaker,
                        start_ms=start_ms,
                        end_ms=max(end_ms, start_ms + 100),
                        text=u_text,
                        raw_text=u_text,
                        language=detected_lang,
                    )
                )

        if not segments:
            # Fallback to channel transcript
            channels = results.get("channels", [])
            if channels:
                alts = channels[0].get("alternatives", [])
                if alts:
                    alt_text = alts[0].get("transcript", "").strip()
                    if alt_text:
                        segments.append(
                            TranscriptionSegment(
                                speaker=speaker,
                                start_ms=chunk_offset_ms,
                                end_ms=chunk_offset_ms + int(duration * 1000),
                                text=alt_text,
                                raw_text=alt_text,
                                language=detected_lang,
                            )
                        )

        return TranscriptionResult(
            segments=segments,
            duration_seconds=duration,
            language=detected_lang,
            provider_name=self.name,
        )
