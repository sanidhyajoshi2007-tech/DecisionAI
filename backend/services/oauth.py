"""
Google OAuth 2.0 service.

Handles:
1. Authorization URL generation
2. Token exchange with Google's token endpoint (https://oauth2.googleapis.com/token)
3. Fetching user profile from Google's userinfo endpoint (https://www.googleapis.com/oauth2/v3/userinfo)
4. Dev/test mock fallback when Google OAuth credentials are not yet configured
"""

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from backend.config import Settings

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def get_google_auth_url(settings: Settings, state: str | None = None) -> tuple[str, str]:
    """
    Generate Google OAuth 2.0 authorization URL.
    Returns: (authorization_url, state)
    """
    state_token = state or secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id or "placeholder_client_id",
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state_token


async def exchange_google_code_for_user(
    code: str,
    settings: Settings,
) -> dict[str, str]:
    """
    Exchange authorization code for tokens, then retrieve user profile.
    Returns:
        {
            "google_id": str,
            "email": str,
            "name": str,
            "avatar_url": str,
        }
    """
    # 1. Dev / Mock mode support for testing without real Google credentials
    if code.startswith("mock_code_") or not settings.google_client_id or not settings.google_client_secret:
        mock_user_id = code.replace("mock_code_", "") if code.startswith("mock_code_") else "dev"
        return {
            "google_id": f"google_{mock_user_id}",
            "email": f"user_{mock_user_id}@example.com" if mock_user_id != "dev" else "dev@decisionai.local",
            "name": f"Test User {mock_user_id}" if mock_user_id != "dev" else "Dev User",
            "avatar_url": "https://lh3.googleusercontent.com/a/default-avatar",
        }

    # 2. Exchange authorization code for access token with Google
    token_payload = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.google_redirect_uri,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data=token_payload)
        if token_resp.status_code != 200:
            logger.error(f"Google token exchange failed ({token_resp.status_code}): {token_resp.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to exchange Google authorization code: {token_resp.text}",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google token response did not contain access_token",
            )

        # 3. Retrieve user profile using access token
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            logger.error(f"Google userinfo request failed ({userinfo_resp.status_code}): {userinfo_resp.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to fetch user profile from Google: {userinfo_resp.text}",
            )

        userinfo = userinfo_resp.json()
        return {
            "google_id": userinfo.get("sub", ""),
            "email": userinfo.get("email", ""),
            "name": userinfo.get("name", userinfo.get("email", "User")),
            "avatar_url": userinfo.get("picture", ""),
        }
