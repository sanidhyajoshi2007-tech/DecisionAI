"""
Authentication dependency — dev stub.

Until Google OAuth is added, this injects a hardcoded dev user (id=1).
The dependency auto-creates the dev user on first request if it
doesn't exist yet.

When OAuth is added, replace `get_current_user` with real token
validation. Every route that uses Depends(get_current_user) will
automatically pick up the change.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.crud.users import create_user, get_user_by_id
from backend.models.user import User

# Hardcoded dev user credentials
DEV_USER_ID = 1
DEV_USER_EMAIL = "dev@decisionai.local"
DEV_USER_NAME = "Dev User"


async def get_current_user(
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Returns the current authenticated user.

    Stub implementation: returns the dev user, creating it if needed.
    """
    user = await get_user_by_id(db, DEV_USER_ID)
    if user is None:
        user = await create_user(db, email=DEV_USER_EMAIL, name=DEV_USER_NAME)
    return user
