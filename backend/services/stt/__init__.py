"""
Speech-to-Text (STT) services for DecisionAI.
Provides unified provider interfaces for faster-whisper, Google Gemini, Sarvam AI, and Deepgram.
"""

from typing import Type

from backend.config import Settings, get_settings
from backend.services.stt.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)
from backend.services.stt.deepgram_provider import DeepgramTranscriptionProvider
from backend.services.stt.gemini_provider import GeminiTranscriptionProvider
from backend.services.stt.normalizer import (
    WERResult,
    compute_word_error_rate,
    normalize_hindi,
    normalize_hinglish,
)
from backend.services.stt.pipeline import process_meeting_transcription
from backend.services.stt.sarvam_provider import SarvamTranscriptionProvider
from backend.services.stt.whisper_provider import FasterWhisperProvider

PROVIDERS: dict[str, Type[TranscriptionProvider]] = {
    "faster_whisper": FasterWhisperProvider,
    "whisper": FasterWhisperProvider,
    "gemini": GeminiTranscriptionProvider,
    "gemini_audio": GeminiTranscriptionProvider,
    "sarvam": SarvamTranscriptionProvider,
    "sarvam_ai": SarvamTranscriptionProvider,
    "deepgram": DeepgramTranscriptionProvider,
}


def get_transcription_provider(
    provider_name: str | None = None,
    settings: Settings | None = None,
) -> TranscriptionProvider:
    """
    Factory function to retrieve configured STT provider instance.

    Args:
        provider_name: Optional override. If omitted, uses settings.transcription_provider.
        settings: Application settings.

    Returns:
        Instance of TranscriptionProvider.
    """
    settings = settings or get_settings()
    name = (provider_name or settings.transcription_provider).lower().strip()

    provider_cls = PROVIDERS.get(name)
    if not provider_cls:
        valid_options = ", ".join(sorted(PROVIDERS.keys()))
        raise ValueError(
            f"Unknown transcription provider '{name}'. Valid options: {valid_options}"
        )

    return provider_cls(settings=settings)


__all__ = [
    "TranscriptionProvider",
    "TranscriptionSegment",
    "TranscriptionResult",
    "FasterWhisperProvider",
    "GeminiTranscriptionProvider",
    "SarvamTranscriptionProvider",
    "DeepgramTranscriptionProvider",
    "get_transcription_provider",
    "process_meeting_transcription",
    "normalize_hindi",
    "normalize_hinglish",
    "compute_word_error_rate",
    "WERResult",
]
