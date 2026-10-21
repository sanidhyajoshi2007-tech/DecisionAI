"""
Authentication API routes — Google OAuth, session cookies, logout, and extension tokens.
"""

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_required_user
from backend.config import Settings, get_settings
from backend.crud.users import get_or_create_google_user
from backend.database import get_db
from backend.models.user import User
from backend.schemas.user import ExtensionTokenResponse, UserResponse
from backend.services.auth import create_extension_token, create_session_token
from backend.services.oauth import exchange_google_code_for_user, get_google_auth_url
from backend.services.rate_limiter import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Google OAuth Login ────────────────────────────────────

@router.get(
    "/google/login",
    summary="Initiate Google OAuth 2.0 login",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def google_login(
    json_response: bool = Query(False, description="Return JSON authorization URL instead of 302 redirect"),
    settings: Settings = Depends(get_settings),
):
    """
    Redirects the user to Google's consent screen.
    If json_response is True, returns the authorization URL in JSON.
    """
    auth_url, state = get_google_auth_url(settings)
    if json_response:
        return {"auth_url": auth_url, "state": state}
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


# ── Google OAuth Callback ─────────────────────────────────

@router.get(
    "/google/callback",
    summary="Google OAuth 2.0 redirect callback",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def google_callback(
    code: str = Query(..., description="Google authorization code"),
    state: str | None = Query(None, description="OAuth state parameter"),
    json_response: bool = Query(False, description="Return JSON instead of redirecting to dashboard"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Exchanges the authorization code with Google for user identity, creates or
    updates the user in the database, and issues a secure httpOnly session cookie.
    """
    google_user = await exchange_google_code_for_user(code, settings)

    user = await get_or_create_google_user(
        db=db,
        google_id=google_user["google_id"],
        email=google_user["email"],
        name=google_user.get("name"),
        avatar_url=google_user.get("avatar_url"),
    )

    # Generate 14-day signed session token
    session_token = create_session_token(
        user_id=user.id,
        email=user.email,
        secret_key=settings.secret_key,
        expires_in_days=settings.session_cookie_max_age_days,
    )

    max_age = settings.session_cookie_max_age_days * 86400

    if json_response:
        resp = JSONResponse({
            "status": "authenticated",
            "token": session_token,
            "user": UserResponse.model_validate(user).model_dump(mode="json"),
        })
    else:
        resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Set secure httpOnly session cookie
    resp.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age,
        httponly=True,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
        path="/",
    )
    return resp


# ── Logout ────────────────────────────────────────────────

@router.post(
    "/logout",
    summary="Log out and invalidate httpOnly session cookie",
)
@router.get(
    "/logout",
    include_in_schema=False,
)
async def logout(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Clears the session cookie from the browser client."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
    return {"status": "logged_out", "message": "Successfully logged out"}


# ── Current User Profile ──────────────────────────────────

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user profile",
    dependencies=[Depends(rate_limit(requests_per_minute=60))],
)
async def get_my_profile(
    current_user: User = Depends(get_required_user),
) -> UserResponse:
    """Returns profile for currently logged in user."""
    return UserResponse.model_validate(current_user)


# ── Extension Token Issuance ──────────────────────────────

@router.post(
    "/extension-token",
    response_model=ExtensionTokenResponse,
    summary="Issue a short-lived token for Chrome Extension API & WebSocket access",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def issue_extension_token(
    current_user: User = Depends(get_required_user),
    settings: Settings = Depends(get_settings),
) -> ExtensionTokenResponse:
    """
    Called by the logged-in web app. Generates a signed, short-lived (60 min) token
    that the Chrome extension uses for API requests and live WebSocket streaming.
    """
    token = create_extension_token(
        user_id=current_user.id,
        email=current_user.email,
        secret_key=settings.secret_key,
        expires_in_minutes=settings.extension_token_expire_minutes,
    )
    return ExtensionTokenResponse(
        token=token,
        token_type="Bearer",
        expires_in=settings.extension_token_expire_minutes * 60,
        user=UserResponse.model_validate(current_user),
    )
