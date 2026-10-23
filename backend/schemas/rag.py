"""
Pydantic schemas for RAG query request, response, and citations.
Strict constraints: No emojis, no em dashes.
"""

from typing import Literal
from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """User question submission for RAG meeting intelligence."""
    question: str = Field(..., min_length=2, max_length=1000, description="The user query or question")
    meeting_id: int | None = Field(default=None, description="Optional meeting ID to restrict retrieval scope")
    stream: bool = Field(default=False, description="Whether to stream response tokens via Server-Sent Events")


class Citation(BaseModel):
    """Citation referencing a specific transcript excerpt or extracted decision."""
    meeting_id: int
    meeting_title: str
    date: str
    timestamp: str = Field(description="Formatted MM:SS timestamp")
    start_ms: int = Field(default=0, description="Start offset in milliseconds")
    text: str = Field(default="", description="Snippet of dialogue or decision text")
    source_type: Literal["transcript", "decision"] = "transcript"


class DecisionItem(BaseModel):
    """Extracted decision or action item matched by RAG pipeline."""
    id: int
    meeting_id: int
    meeting_title: str
    text: str
    type: str = "decision"
    status: str = "accepted"
    timestamp_ms: int = 0
    date: str = ""


class AskResponse(BaseModel):
    """Complete structured response for non-streaming RAG queries."""
    answer: str
    intent: str
    citations: list[Citation] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
    retrieved_chunks_count: int = 0
