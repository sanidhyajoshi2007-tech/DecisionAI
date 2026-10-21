"""
SQLAlchemy ORM model — Decision.

Stores decisions, action items, risks, and conclusions extracted from
a meeting transcript, tied to a timestamp offset (timestamp_ms).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    Integer,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class Decision(Base):
    """
    Extracted decision or action item from a meeting.
    
    Fields:
      meeting_id: FK referencing meetings.id
      text: The decision or action item content
      type: Category (e.g. 'decision', 'action_item', 'risk')
      timestamp_ms: Millisecond offset within the meeting recording where decision was made
    """
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(50), default="decision", index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    meeting: Mapped["Meeting"] = relationship(back_populates="decisions")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Decision id={self.id} type={self.type!r} [{self.timestamp_ms}ms]>"
