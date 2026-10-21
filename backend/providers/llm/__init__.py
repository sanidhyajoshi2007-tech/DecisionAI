"""
LLM Provider package — exposes pluggable LLM interfaces (Gemini default).
"""

from typing import Type

from backend.config import Settings, get_settings
from backend.providers.llm.base import (
    CleanedTranscriptSegment,
    ExtractedDecision,
    LLMProvider,
    MeetingAIResult,
)
from backend.providers.llm.gemini import GeminiLLMProvider

LLM_PROVIDERS: dict[str, Type[LLMProvider]] = {
    "gemini": GeminiLLMProvider,
    "google": GeminiLLMProvider,
    "google_gemini": GeminiLLMProvider,
}


def get_llm_provider(
    provider_name: str | None = None,
    settings: Settings | None = None,
) -> LLMProvider:
    """
    Factory function to retrieve configured LLM provider instance.
    Defaults to Gemini.
    """
    settings = settings or get_settings()
    name = (provider_name or getattr(settings, "llm_provider", "gemini")).lower().strip()

    provider_cls = LLM_PROVIDERS.get(name)
    if not provider_cls:
        # Default back to Gemini
        provider_cls = GeminiLLMProvider

    return provider_cls(settings=settings)


__all__ = [
    "LLMProvider",
    "GeminiLLMProvider",
    "CleanedTranscriptSegment",
    "ExtractedDecision",
    "MeetingAIResult",
    "get_llm_provider",
]
