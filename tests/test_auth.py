"""
Tests for Google OAuth 2.0, session cookies, logout, extension tokens,
rate limiting, decision models, and user_id scoping across API endpoints.
"""

import time
import pytest
from httpx import AsyncClient

from backend.config import Settings
from backend.services.auth import (
    create_extension_token,
    create_session_token,
    decode_token,
)
from backend.services.rate_limiter import InMemoryRateLimiter


# ── Google OAuth Login & Callback Tests ────────────────────

@pytest.mark.asyncio
async def test_google_login_redirect(client: AsyncClient):
    """GET /auth/google/login should issue a 302 redirect to Google's consent screen."""
    resp = await client.get("/auth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "accounts.google.com/o/oauth2" in location
    assert "response_type=code" in location


@pytest.mark.asyncio
async def test_google_login_json_response(client: AsyncClient):
    """GET /auth/google/login?json_response=true returns JSON with auth URL and state."""
    resp = await client.get("/auth/google/login?json_response=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "auth_url" in data
    assert "state" in data
    assert "accounts.google.com" in data["auth_url"]


@pytest.mark.asyncio
async def test_google_callback_sets_cookie_and_creates_user(client: AsyncClient):
    """
    GET /auth/google/callback with authorization code:
    1. Creates/loads user
    2. Issues signed session token
    3. Sets secure httpOnly cookie 'decisionai_session'
    """
    resp = await client.get(
        "/auth/google/callback?code=mock_code_alpha&json_response=true",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "authenticated"
    assert "token" in data
    assert data["user"]["email"] == "user_alpha@example.com"
    assert data["user"]["google_id"] == "google_alpha"

    # Verify httpOnly cookie in response headers
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "decisionai_session=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header or "httponly" in set_cookie_header.lower()


# ── Session Cookie & Profile (/auth/me) Tests ─────────────

@pytest.mark.asyncio
async def test_auth_me_with_cookie_and_logout(client: AsyncClient):
    """
    Verify /auth/me works with session cookie, and /auth/logout clears cookie.
    """
    # 1. Login via callback
    login_resp = await client.get(
        "/auth/google/callback?code=mock_code_user1&json_response=true"
    )
    assert login_resp.status_code == 200

    # 2. Access /auth/me with cookie
    me_resp = await client.get("/auth/me")
    assert me_resp.status_code == 200
    user_data = me_resp.json()
    assert user_data["email"] == "user_user1@example.com"

    # 3. Logout
    logout_resp = await client.post("/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["status"] == "logged_out"

    # 4. Access /auth/me without cookie (in clean client) -> 401
    clean_client = AsyncClient(transport=client._transport, base_url="http://test")
    try:
        unauth_resp = await clean_client.get("/auth/me")
        assert unauth_resp.status_code == 401
    finally:
        await clean_client.aclose()


# ── Extension Token Flow Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_extension_token_issuance_and_usage(client: AsyncClient):
    """
    1. Log in via session cookie
    2. POST /auth/extension-token -> issues short-lived token
    3. Use token as Bearer token on protected endpoint /auth/me
    """
    # Login
    await client.get("/auth/google/callback?code=mock_code_extuser&json_response=true")

    # Issue extension token
    tok_resp = await client.post("/auth/extension-token")
    assert tok_resp.status_code == 200
    token_data = tok_resp.json()
    assert "token" in token_data
    assert token_data["token_type"] == "Bearer"
    assert token_data["expires_in"] == 3600
    ext_token = token_data["token"]

    # Use in a separate client without cookies via Authorization: Bearer <token>
    ext_client = AsyncClient(transport=client._transport, base_url="http://test")
    try:
        headers = {"Authorization": f"Bearer {ext_token}"}
        me_resp = await ext_client.get("/auth/me", headers=headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "user_extuser@example.com"

        # Also supports ?token= query parameter
        me_query_resp = await ext_client.get(f"/auth/me?token={ext_token}")
        assert me_query_resp.status_code == 200
        assert me_query_resp.json()["email"] == "user_extuser@example.com"
    finally:
        await ext_client.aclose()


@pytest.mark.asyncio
async def test_invalid_and_expired_tokens():
    """Verify HMAC token decoding rejects tampering and expiration."""
    secret = "test-secret-key-12345"
    token = create_extension_token(user_id=42, email="test@example.com", secret_key=secret, expires_in_minutes=60)
    payload = decode_token(token, secret_key=secret)
    assert payload["sub"] == "42"
    assert payload["email"] == "test@example.com"
    assert payload["type"] == "extension"

    # Tampered signature
    tampered = token[:-4] + "xxxx"
    with pytest.raises(Exception):
        decode_token(tampered, secret_key=secret)

    # Expired token
    expired = create_extension_token(user_id=42, email="test@example.com", secret_key=secret, expires_in_minutes=-1)
    with pytest.raises(Exception):
        decode_token(expired, secret_key=secret)


# ── User Scoping Tests (Data Isolation) ───────────────────

@pytest.mark.asyncio
async def test_user_scoping_meeting_and_decisions(client: AsyncClient):
    """
    Strict isolation test:
    - User A creates a meeting and a decision.
    - User B cannot access, view, finish, or delete User A's meeting or decisions.
    """
    # 1. Login User A
    resp_a = await client.get("/auth/google/callback?code=mock_code_user_a&json_response=true")
    token_a = resp_a.json()["token"]

    # 2. Login User B
    resp_b = await client.get("/auth/google/callback?code=mock_code_user_b&json_response=true")
    token_b = resp_b.json()["token"]

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates a meeting
    create_resp = await client.post(
        "/meetings",
        json={"title": "Confidential Board Meeting A"},
        headers=headers_a,
    )
    assert create_resp.status_code == 200
    meeting_a = create_resp.json()
    meeting_a_id = meeting_a["id"]

    # User A adds a decision
    dec_resp = await client.post(
        f"/api/meetings/{meeting_a_id}/decisions",
        json={"text": "Approve Q3 Budget Allocation", "type": "decision", "timestamp_ms": 12500},
        headers=headers_a,
    )
    assert dec_resp.status_code == 200
    decision_id = dec_resp.json()["id"]

    # ── User B Isolation Checks ──

    # User B lists meetings: should NOT see User A's meeting
    list_b_resp = await client.get("/api/meetings", headers=headers_b)
    assert list_b_resp.status_code == 200
    meetings_b = list_b_resp.json()["meetings"]
    assert all(m["id"] != meeting_a_id for m in meetings_b)

    # User B tries to get User A's meeting: 404 Not Found
    get_b_resp = await client.get(f"/api/meetings/{meeting_a_id}", headers=headers_b)
    assert get_b_resp.status_code == 404

    # User B tries to view User A's meeting page: 404 Not Found
    view_b_resp = await client.get(f"/meetings/{meeting_a_id}", headers=headers_b)
    assert view_b_resp.status_code == 404

    # User B tries to finish User A's meeting: 403 Forbidden
    finish_b_resp = await client.post(f"/meetings/{meeting_a_id}/finish", headers=headers_b)
    assert finish_b_resp.status_code == 403

    # User B tries to list User A's decisions: 404 Not Found
    dec_list_b_resp = await client.get(f"/api/meetings/{meeting_a_id}/decisions", headers=headers_b)
    assert dec_list_b_resp.status_code == 404

    # User B tries to create a decision on User A's meeting: 404 Not Found
    dec_create_b_resp = await client.post(
        f"/api/meetings/{meeting_a_id}/decisions",
        json={"text": "Malicious inject", "type": "decision"},
        headers=headers_b,
    )
    assert dec_create_b_resp.status_code == 404

    # User B tries to delete User A's decision: 404 Not Found
    dec_del_b_resp = await client.delete(
        f"/api/meetings/{meeting_a_id}/decisions/{decision_id}",
        headers=headers_b,
    )
    assert dec_del_b_resp.status_code == 404

    # User B tries to delete User A's meeting: 404 Not Found
    del_b_resp = await client.delete(f"/api/meetings/{meeting_a_id}", headers=headers_b)
    assert del_b_resp.status_code == 404

    # ── User A Ownership Checks ──

    # User A gets own meeting: 200 OK
    get_a_resp = await client.get(f"/api/meetings/{meeting_a_id}", headers=headers_a)
    assert get_a_resp.status_code == 200
    assert get_a_resp.json()["title"] == "Confidential Board Meeting A"

    # User A lists decisions: contains decision
    dec_list_a = await client.get(f"/api/meetings/{meeting_a_id}/decisions", headers=headers_a)
    assert dec_list_a.status_code == 200
    assert dec_list_a.json()["total"] == 1
    assert dec_list_a.json()["decisions"][0]["text"] == "Approve Q3 Budget Allocation"

    # User A deletes decision: 200 OK
    dec_del_a = await client.delete(f"/api/meetings/{meeting_a_id}/decisions/{decision_id}", headers=headers_a)
    assert dec_del_a.status_code == 200

    # User A deletes meeting: 200 OK
    del_a_resp = await client.delete(f"/api/meetings/{meeting_a_id}", headers=headers_a)
    assert del_a_resp.status_code == 200


# ── In-Memory Rate Limiter Tests ───────────────────────────

@pytest.mark.asyncio
async def test_in_memory_rate_limiter_unit():
    """Verify in-memory sliding window rate limiter blocks requests exceeding limit."""
    limiter = InMemoryRateLimiter(requests_per_minute=3, window_seconds=60)
    key = "test_client_1"

    # First 3 requests allowed
    for _ in range(3):
        allowed, retry_after, remaining = await limiter.check(key)
        assert allowed is True

    # 4th request blocked with HTTP 429 semantics
    allowed, retry_after, remaining = await limiter.check(key)
    assert allowed is False
    assert retry_after > 0
    assert remaining == 0


@pytest.mark.asyncio
async def test_endpoint_rate_limiting_429():
    """Verify FastAPI rate limit dependency raises HTTP 429 with Retry-After header."""
    from fastapi import HTTPException
    from backend.services.rate_limiter import rate_limit

    dep = rate_limit(requests_per_minute=2, window_seconds=60)

    class DummyClient:
        host = "192.168.1.100"

    class DummyRequest:
        headers = {}
        client = DummyClient()
        url = type("URL", (), {"path": "/api/test-limit"})()

    req = DummyRequest()
    await dep(req)  # 1st request -> ok
    await dep(req)  # 2nd request -> ok

    with pytest.raises(HTTPException) as exc_info:
        await dep(req)  # 3rd request -> 429 Too Many Requests

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert exc_info.value.headers["X-RateLimit-Limit"] == "2"
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"

