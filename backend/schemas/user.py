"""
Pydantic schemas for User API requests/responses.

Schemas are separate from ORM models because:
1. We control exactly what's exposed in the API (no accidental leaks)
2. Validation rules can differ from DB constraints
3. Request vs Response shapes are often different
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    """What's needed to create a user."""
    email: str  # Using str instead of EmailStr to avoid extra dep for now
    name: str


class UserResponse(BaseModel):
    """What we return when a user is requested."""
    id: int
    email: str
    name: str
    avatar_url: str | None = None
    google_id: str | None = None
    retention_days: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExtensionTokenResponse(BaseModel):
    """Returned when extension requests a short-lived session token."""
    token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserResponse


class UserSettingsResponse(BaseModel):
    """User privacy and retention settings response."""
    retention_days: int | None = None


class UserSettingsUpdate(BaseModel):
    """Payload to update retention setting."""
    retention_days: int | None = None

    def validate_retention(self) -> int | None:
        allowed = (None, 0, 30, 90, 365)
        if self.retention_days not in allowed:
            raise ValueError("retention_days must be one of: null, 0 (forever), 30, 90, 365")
        return self.retention_days


class DeleteAllDataRequest(BaseModel):
    """Typed confirmation payload to permanently erase all user data."""
    confirmation: str

