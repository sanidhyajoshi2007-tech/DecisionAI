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


async def create_user(
    db: AsyncSession,
    email: str,
    name: str,
    google_id: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """Create a new user. Caller must ensure email is unique."""
    user = User(
        email=email,
        name=name,
        google_id=google_id,
        avatar_url=avatar_url,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_or_create_google_user(
    db: AsyncSession,
    google_id: str,
    email: str,
    name: str,
    avatar_url: str | None = None,
) -> User:
    """
    Find or create a user from Google OAuth profile.
    Updates name/avatar_url on subsequent logins.
    """
    # 1. Look up by google_id
    if google_id:
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()
        if user:
            user.name = name or user.name
            user.avatar_url = avatar_url or user.avatar_url
            await db.flush()
            return user

    # 2. Look up by email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        user.google_id = google_id or user.google_id
        user.name = name or user.name
        user.avatar_url = avatar_url or user.avatar_url
        await db.flush()
        return user

    # 3. Create brand new user
    return await create_user(
        db=db,
        email=email,
        name=name,
        google_id=google_id,
        avatar_url=avatar_url,
    )
