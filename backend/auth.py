"""
Authentication dependency and token validation.

Supports:
1. HTTP Authorization Bearer token (session or extension token)
2. HTTP Query parameter `?token=<token>` (useful for WebSockets & extensions)
3. HTTP-only session cookie (`decisionai_session`)
4. Development fallback to dev user (id=1) only when no credentials are provided
   and `app_env == "development"`.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.crud.users import create_user, get_user_by_id
from backend.database import get_db
from backend.models.user import User
from backend.services.auth import decode_token

# Dev user fallback constants
DEV_USER_ID = 1
DEV_USER_EMAIL = "dev@decisionai.local"
DEV_USER_NAME = "Dev User"


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Helper to retrieve or initialize the local development user."""
    user = await get_user_by_id(db, DEV_USER_ID)
    if user is None:
        user = await create_user(db, email=DEV_USER_EMAIL, name=DEV_USER_NAME)
    return user


async def get_user_from_token(
    token: str,
    db: AsyncSession,
    settings: Settings,
    expected_type: str | None = None,
) -> User:
    """
    Decodes an HMAC signed token and loads the associated User from the database.
    Raises HTTPException(401) on invalid signature, expiration, or missing user.
    """
    payload = decode_token(token, secret_key=settings.secret_key, expected_type=expected_type)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid subject format in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with token does not exist",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def _extract_token_from_request(request: Request, session_cookie_name: str) -> str | None:
    """
    Extract token candidate from Authorization header, query param, or session cookie.
    """
    # 1. Bearer token in Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    # 2. Query param ?token=...
    query_token = request.query_params.get("token")
    if query_token and query_token.strip():
        return query_token.strip()

    # 3. HTTP-only session cookie
    cookie_token = request.cookies.get(session_cookie_name)
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()

    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """
    Returns the currently authenticated user.

    If credentials (Bearer token, query token, or session cookie) are provided,
    they are strictly verified.
    If no credentials are provided and the app is running in development mode,
    it falls back to the development user to support local prototyping and testing.
    """
    token = _extract_token_from_request(request, settings.session_cookie_name)

    if token:
        return await get_user_from_token(token, db, settings)

    # In development mode, fall back to dev user if no token is provided
    if settings.app_env == "development":
        return await _get_or_create_dev_user(db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication credentials were not provided",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_required_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """
    Strict authentication dependency: requires a valid session or token.
    Does NOT fall back to the development user under any circumstances.
    """
    token = _extract_token_from_request(request, settings.session_cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await get_user_from_token(token, db, settings)
