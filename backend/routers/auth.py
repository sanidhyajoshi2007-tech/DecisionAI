"""Authentication API routes - Google OAuth, session cookies, logout, and extension tokens."""

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field
from backend.auth import get_current_user, get_required_user
from backend.config import Settings, get_settings
from backend.crud.users import create_user, get_or_create_google_user, get_user_by_email
from backend.database import get_db
from backend.middleware.security import CSRF_COOKIE_NAME, generate_csrf_token
from backend.models.user import User
from backend.schemas.user import ExtensionTokenResponse, UserResponse
from backend.services.audit import log_audit_event
from backend.services.auth import create_extension_token, create_session_token
from backend.services.oauth import exchange_google_code_for_user, get_google_auth_url
from backend.services.rate_limiter import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


# ── CSRF Token Endpoint ───────────────────────────────────

@router.get(
    "/csrf",
    summary="Get CSRF token for the active session",
)
async def get_csrf_token(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Issues or refreshes the CSRF double-submit cookie and returns token."""
    token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return {"csrf_token": token}


# ── Local / Personal Login ────────────────────────────────

class LocalLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    name: str | None = Field(default=None, max_length=100)


@router.post(
    "/login",
    summary="Sign in or register with email for personal profile",
    dependencies=[Depends(rate_limit(requests_per_minute=30))],
)
async def local_login(
    payload: LocalLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Creates or logs in as a personal user with email.
    Sets secure session and CSRF cookies, and returns the profile.
    """
    email_val = str(payload.email).lower().strip()
    if "@" not in email_val or "." not in email_val.split("@")[-1]:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid email address format.")
    user = await get_user_by_email(db, email_val)
    if not user:
        name_val = payload.name.strip() if payload.name and payload.name.strip() else email_val.split("@")[0].title()
        user = await create_user(
            db=db,
            email=email_val,
            name=name_val,
        )
        await db.commit()
        await db.refresh(user)

    await log_audit_event(
        db=db,
        action="login",
        user_id=user.id,
        target=f"user:{user.id}",
        request=request,
    )

    session_token = create_session_token(
        user_id=user.id,
        email=user.email,
        secret_key=settings.secret_key,
        expires_in_days=settings.session_cookie_max_age_days,
    )
    max_age = settings.session_cookie_max_age_days * 86400

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age,
        httponly=True,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
        path="/",
    )
    csrf_token = generate_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )

    return {
        "status": "authenticated",
        "token": session_token,
        "csrf_token": csrf_token,
        "user": UserResponse.model_validate(user).model_dump(mode="json"),
    }


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
    request: Request,
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

    # Log audit event for login
    await log_audit_event(
        db=db,
        action="login",
        user_id=user.id,
        target=f"user:{user.id}",
        request=request,
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

    # Set CSRF double-submit cookie
    csrf_token = generate_csrf_token()
    resp.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        samesite="lax",
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
    request: Request,
    response: Response,
    current_user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Clears session and CSRF cookies from the browser client and logs audit event."""
    if current_user:
        await log_audit_event(
            db=db,
            action="logout",
            user_id=current_user.id,
            target="session",
            request=request,
        )

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
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
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Returns profile for currently logged in user, deriving display name from email if needed."""
    from backend.crud.users import extract_name_from_email
    if not current_user.name or current_user.name == "Dev User":
        current_user.name = extract_name_from_email(current_user.email)
        await db.flush()
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
