"""
CRUD operations for users.

Every query is scoped by user_id where applicable.
"""

import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.user import User


def extract_name_from_email(email: str | None) -> str:
    """
    Derives a clean, formatted human display name from an email address.
    e.g. 'sanid.sharma@gmail.com' -> 'Sanid Sharma'
         'rahul_verma@company.com'  -> 'Rahul Verma'
         'priya.k@domain.org'       -> 'Priya K'
         'alex@gmail.com'           -> 'Alex'
    """
    if not email or "@" not in email:
        return "User"
    local_part = email.split("@")[0].strip()
    # Replace dots, underscores, hyphens, plus signs with spaces
    cleaned = re.sub(r"[._\-+]+", " ", local_part)
    # Remove trailing digits from each token if letters exist
    tokens = []
    for token in cleaned.split():
        token_cleaned = re.sub(r"\d+$", "", token).strip()
        tokens.append(token_cleaned if token_cleaned else token)
    words = [t for t in tokens if t]
    if not words:
        return local_part.title()
    return " ".join(words).title()



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
    name: str | None = None,
    google_id: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """Create a new user. If name is omitted or Dev User, derive from email."""
    resolved_name = name.strip() if name and name.strip() else extract_name_from_email(email)
    if resolved_name == "Dev User":
        resolved_name = extract_name_from_email(email)

    user = User(
        email=email,
        name=resolved_name,
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
    name: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """
    Find or create a user from Google OAuth profile.
    Updates name/avatar_url on subsequent logins, deriving name from email if needed.
    """
    resolved_name = name.strip() if name and name.strip() else extract_name_from_email(email)
    if resolved_name == "Dev User":
        resolved_name = extract_name_from_email(email)

    # 1. Look up by google_id
    if google_id:
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()
        if user:
            # Upgrade placeholder "Dev User" or empty name to clean derived name
            if not user.name or user.name == "Dev User":
                user.name = resolved_name
            elif resolved_name and resolved_name != "Dev User":
                user.name = resolved_name
            user.avatar_url = avatar_url or user.avatar_url
            await db.flush()
            return user

    # 2. Look up by email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        user.google_id = google_id or user.google_id
        if not user.name or user.name == "Dev User":
            user.name = resolved_name
        elif resolved_name and resolved_name != "Dev User":
            user.name = resolved_name
        user.avatar_url = avatar_url or user.avatar_url
        await db.flush()
        return user

    # 3. Create brand new user
    return await create_user(
        db=db,
        email=email,
        name=resolved_name,
        google_id=google_id,
        avatar_url=avatar_url,
    )
