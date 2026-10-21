"""
Pydantic schemas for Decision API requests and responses.
"""

from datetime import datetime
from pydantic import BaseModel


class DecisionCreate(BaseModel):
    """Payload to create a decision or action item."""
    text: str
    type: str = "decision"
    timestamp_ms: int = 0


class DecisionResponse(BaseModel):
    """Decision or action item detail returned by API."""
    id: int
    meeting_id: int
    text: str
    type: str
    timestamp_ms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionListResponse(BaseModel):
    """List of decisions for a meeting."""
    meeting_id: int
    decisions: list[DecisionResponse]
    total: int
