"""
Base interfaces and Pydantic models for LLM providers.
"""

from abc import ABC, abstractmethod
from typing import Literal
from pydantic import BaseModel, Field


class CleanedTranscriptSegment(BaseModel):
    """Cleaned segment text mapped back to its segment ID."""
    id: int
    cleaned_text: str


class ExtractedDecision(BaseModel):
    """
    Structured extraction of a decision, action item, or client request.
    Includes status (accepted/rejected/pending) and a timestamp reference.
    """
    text: str = Field(description="Description of the decision, action item, or client request")
    type: Literal["decision", "action_item", "client_request"] = Field(
        default="decision",
        description="Category of the item: decision, action_item, or client_request",
    )
    status: Literal["accepted", "rejected", "pending"] = Field(
        default="accepted",
        description="Status of the item: accepted, rejected, or pending",
    )
    timestamp_ms: int = Field(
        default=0,
        description="Timestamp offset in milliseconds from start of recording",
    )


class MeetingAIResult(BaseModel):
    """Aggregated output from LLM meeting processing."""
    cleaned_segments: list[CleanedTranscriptSegment] = Field(default_factory=list)
    summary: str = Field(default="")
    decisions: list[ExtractedDecision] = Field(default_factory=list)


class LLMProvider(ABC):
    """Abstract base class for LLM providers (Gemini, Claude, OpenAI, Local)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'gemini')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider API keys and requirements are configured."""
        pass

    @abstractmethod
    async def cleanup_transcript(
        self,
        segments: list[dict],
        glossary: list[str],
    ) -> list[CleanedTranscriptSegment]:
        """
        Clean phonetic errors using the user's glossary without altering meaning
        or hallucinating content. Handles Hindi/Hinglish and English.
        """
        pass

    @abstractmethod
    async def generate_summary(
        self,
        transcript_text: str,
        meeting_title: str | None = None,
    ) -> str:
        """Generate structured executive summary from meeting transcript."""
        pass

    @abstractmethod
    async def extract_decisions(
        self,
        segments: list[dict],
    ) -> list[ExtractedDecision]:
        """
        Extract decisions, action items, and client requests with status
        (accepted/rejected/pending) and timestamp references.
        """
        pass

    @abstractmethod
    async def process_meeting(
        self,
        segments: list[dict],
        glossary: list[str],
        meeting_title: str | None = None,
    ) -> MeetingAIResult:
        """
        Run the complete meeting AI pipeline:
        1. Glossary cleanup
        2. Meeting summary
        3. Structured decisions, action items & client requests extraction
        """
        pass
