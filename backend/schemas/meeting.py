"""
Pydantic schemas for Meeting and TranscriptSegment API requests/responses.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from backend.models.meeting import MeetingStatus


# -- Meeting ------------------------------------------------

class MeetingCreate(BaseModel):
    """Metadata sent along with meeting creation."""
    title: str = Field(default="Untitled Meeting", min_length=1, max_length=200)
    consent_recorded: bool = Field(default=False, description="Explicit recording consent from call participants")


class MeetingUpdate(BaseModel):
    """Payload to update meeting details such as title."""
    title: str = Field(..., min_length=1, max_length=200)


class MeetingResponse(BaseModel):
    """Full meeting detail returned by the API."""
    id: int
    user_id: int
    title: str
    status: MeetingStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    language: str | None = "en"
    audio_path: str | None = None
    duration_seconds: float | None = None
    summary: str | None = None
    consent_recorded: bool = False
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    """Paginated list of meetings."""
    meetings: list[MeetingResponse]
    total: int
    page: int
    page_size: int


# ── Transcript ───────────────────────────────────────────

class TranscriptSegmentResponse(BaseModel):
    """A single transcript segment."""
    id: int
    meeting_id: int
    speaker: str | None = None
    text: str
    raw_text: str | None = None
    start_time: float
    end_time: float
    start_ms: int | None = None
    end_ms: int | None = None
    language: str | None = None

    model_config = {"from_attributes": True}


class TranscriptResponse(BaseModel):
    """Full transcript for a meeting."""
    meeting_id: int
    segments: list[TranscriptSegmentResponse]
    total_segments: int


# ── Ask AI ───────────────────────────────────────────────

class AskRequest(BaseModel):
    """User's question about a meeting."""
    question: str


class SourceSegment(BaseModel):
    """A transcript segment cited as a source in the AI answer."""
    text: str
    start_time: float
    end_time: float
    speaker: str | None = None


class AskResponse(BaseModel):
    """AI-generated answer with source citations."""
    answer: str
    sources: list[SourceSegment]
