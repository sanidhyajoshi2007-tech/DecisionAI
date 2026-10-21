"""
Authentication, token signing, and session verification service.

Uses HMAC-SHA256 signed URL-safe tokens for both browser sessions and
short-lived extension tokens.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, status


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to URL-safe base64 string without trailing padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data_str: str) -> bytes:
    """Decode URL-safe base64 string with optional padding correction."""
    padding = "=" * (4 - (len(data_str) % 4)) if len(data_str) % 4 != 0 else ""
    return base64.urlsafe_b64decode((data_str + padding).encode("ascii"))


def create_token(
    payload: dict[str, Any],
    secret_key: str,
    expires_in_seconds: int,
) -> str:
    """
    Create a signed token: base64url(header) . base64url(payload) . base64url(signature).
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    full_payload = {
        **payload,
        "iat": now,
        "exp": now + expires_in_seconds,
    }

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload_bytes = json.dumps(full_payload, separators=(",", ":")).encode("utf-8")

    header_b64 = _base64url_encode(header_bytes)
    payload_b64 = _base64url_encode(payload_bytes)

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _base64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_token(
    token: str,
    secret_key: str,
    expected_type: str | None = None,
) -> dict[str, Any]:
    """
    Verify HMAC signature, check expiry, and return decoded payload.
    Raises HTTPException(401) on invalid signature or expired token.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    header_b64, payload_b64, sig_b64 = parts

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()

    try:
        actual_sig = _base64url_decode(sig_b64)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload_bytes = _base64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = int(time.time())
    if "exp" in payload and payload["exp"] < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token type mismatch (expected {expected_type}, got {payload.get('type')})",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def create_session_token(
    user_id: int,
    email: str,
    secret_key: str,
    expires_in_days: int = 14,
) -> str:
    """Create a long-lived browser session cookie token."""
    return create_token(
        payload={"sub": str(user_id), "email": email, "type": "session"},
        secret_key=secret_key,
        expires_in_seconds=expires_in_days * 86400,
    )


def create_extension_token(
    user_id: int,
    email: str,
    secret_key: str,
    expires_in_minutes: int = 60,
) -> str:
    """Create a short-lived token for the Chrome extension to make API & WebSocket calls."""
    return create_token(
        payload={"sub": str(user_id), "email": email, "type": "extension"},
        secret_key=secret_key,
        expires_in_seconds=expires_in_minutes * 60,
    )
