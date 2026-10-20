"""
SQLAlchemy ORM models — Meeting and TranscriptSegment.

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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class MeetingStatus(str, enum.Enum):
    """
    Lifecycle states for a meeting.
    Using str mixin so it serializes to JSON as a string.
    """
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
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
        default=MeetingStatus.UPLOADING,
    )
    # Relative path from AUDIO_STORAGE_DIR
    audio_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Optional error message when status == ERROR
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

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} status={self.status.value!r}>"


class TranscriptSegment(Base):
    """
    A single chunk of transcribed speech.

    start_time/end_time are seconds from the beginning of the recording.
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
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Relationship
    meeting: Mapped["Meeting"] = relationship(back_populates="segments")

    def __repr__(self) -> str:
        return f"<Segment id={self.id} [{self.start_time:.1f}-{self.end_time:.1f}]>"
