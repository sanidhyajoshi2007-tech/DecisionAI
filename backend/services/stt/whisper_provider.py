"""
faster-whisper local STT provider using CTranslate2.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from backend.config import Settings, get_settings
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)


class FasterWhisperProvider(TranscriptionProvider):
    """
    Local speech-to-text provider running faster-whisper on CPU/GPU.
    Zero cloud API costs and complete offline privacy.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.model_size = self.settings.whisper_model_size
        self.device = self.settings.whisper_device
        self.compute_type = self.settings.whisper_compute_type
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "faster_whisper"

    def is_available(self) -> bool:
        """Check if faster-whisper is installed in current Python environment."""
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_model(self) -> Any:
        """Synchronously load or return the cached WhisperModel."""
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper is not installed. Install it with: "
                "pip install faster-whisper>=1.0.0"
            ) from e

        logger.info(
            f"Loading faster-whisper model '{self.model_size}' on {self.device} "
            f"({self.compute_type})..."
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model

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

        # Combine configured glossary hints with any custom passed hints
        effective_glossary = list(self.settings.glossary_list)
        if glossary:
            effective_glossary.extend(glossary)
        # Deduplicate while preserving order
        effective_glossary = list(dict.fromkeys(effective_glossary))
        initial_prompt = ", ".join(effective_glossary) if effective_glossary else None

        def _run_transcribe():
            model = self._get_model()
            kwargs: dict[str, Any] = {
                "beam_size": 5,
                "vad_filter": True,
                "vad_parameters": dict(min_silence_duration_ms=500),
            }
            if language:
                kwargs["language"] = language
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt

            whisper_segments, info = model.transcribe(str(audio_path), **kwargs)

            segments: list[TranscriptionSegment] = []
            for seg in whisper_segments:
                start_ms = chunk_offset_ms + int(seg.start * 1000)
                end_ms = chunk_offset_ms + int(seg.end * 1000)
                text = seg.text.strip()
                if text:
                    segments.append(
                        TranscriptionSegment(
                            speaker=speaker,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            text=text,
                            raw_text=seg.text,
                            language=info.language if info else language,
                        )
                    )

            duration = info.duration if info else 0.0
            detected_lang = info.language if info else language
            return segments, duration, detected_lang

        # Run CPU-bound transcription in threadpool to avoid blocking async event loop
        segments, duration, detected_lang = await asyncio.to_thread(_run_transcribe)

        return TranscriptionResult(
            segments=segments,
            duration_seconds=duration,
            language=detected_lang,
            provider_name=self.name,
        )
