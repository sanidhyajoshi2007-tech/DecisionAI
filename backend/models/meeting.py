"""
SQLAlchemy ORM models: Meeting and TranscriptSegment.

Meeting tracks the lifecycle: uploading → processing → ready → error.
TranscriptSegment stores individual chunks of transcribed text with
timestamps and speaker labels.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    Float,
    Integer,
    DateTime,
    ForeignKey,
    Enum,
    Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class MeetingStatus(str, enum.Enum):
    """
    Lifecycle states for a meeting.
    Using str mixin so it serializes to JSON as a string.
    """
    RECORDING = "recording"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    UPLOADING = "uploading"
    ERROR = "error"


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), default="Untitled Meeting")
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus),
        default=MeetingStatus.RECORDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True, default="en")

    # Relative path from AUDIO_STORAGE_DIR
    audio_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_recorded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optional error message when status == FAILED / ERROR
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="meetings")  # noqa: F821
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        order_by="TranscriptSegment.start_time",
    )
    decisions: Mapped[list["Decision"]] = relationship(  # noqa: F821
        back_populates="meeting",
        cascade="all, delete-orphan",
        order_by="Decision.timestamp_ms",
    )

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} status={self.status.value!r}>"


class TranscriptSegment(Base):
    """
    A single chunk of transcribed speech.

    start_time/end_time are seconds from the beginning of the recording.
    start_ms/end_ms are milliseconds for high-precision alignment.
    raw_text preserves the original unnormalized provider output.
    language is auto-detected by the transcription provider (e.g., 'hi', 'en').
    """
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        index=True,
    )
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Relationship
    meeting: Mapped["Meeting"] = relationship(back_populates="segments")

    def __init__(self, **kwargs):
        # Auto-synchronize seconds and milliseconds
        if "start_ms" in kwargs and kwargs.get("start_ms") is not None and "start_time" not in kwargs:
            kwargs["start_time"] = float(kwargs["start_ms"]) / 1000.0
        elif "start_time" in kwargs and kwargs.get("start_time") is not None and ("start_ms" not in kwargs or kwargs.get("start_ms") is None):
            kwargs["start_ms"] = int(kwargs["start_time"] * 1000)

        if "end_ms" in kwargs and kwargs.get("end_ms") is not None and "end_time" not in kwargs:
            kwargs["end_time"] = float(kwargs["end_ms"]) / 1000.0
        elif "end_time" in kwargs and kwargs.get("end_time") is not None and ("end_ms" not in kwargs or kwargs.get("end_ms") is None):
            kwargs["end_ms"] = int(kwargs["end_time"] * 1000)

        if "raw_text" not in kwargs and "text" in kwargs:
            kwargs["raw_text"] = kwargs["text"]

        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Segment id={self.id} [{self.start_time:.1f}-{self.end_time:.1f}]>"
