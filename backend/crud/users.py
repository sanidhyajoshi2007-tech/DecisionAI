"""
CRUD operations for users.

Every query is scoped by user_id where applicable.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.user import User


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """Fetch a single user by primary key."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Fetch a user by email (for login/signup flows)."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, email: str, name: str) -> User:
    """Create a new user. Caller must ensure email is unique."""
    user = User(email=email, name=name)
    db.add(user)
    # Flush to get the auto-generated id without committing.
    # The session's commit happens in get_db's context manager.
    await db.flush()
    await db.refresh(user)
    return user
