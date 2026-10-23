"""
Security middleware for DecisionAI.
Includes:
1. SecurityHeadersMiddleware: Sets CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy.
2. CSRFProtectionMiddleware: Validates X-CSRF-Token header against csrf_token cookie for state-changing requests.
Strict constraints: No emojis, no em dashes.
"""

import secrets
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.config import Settings, get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_COOKIE_NAME = "decisionai_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

# Endpoints exempt from CSRF (e.g. initial login initiation or extension auth)
EXEMPT_PATHS = {
    "/auth/google/login",
    "/auth/google/callback",
    "/auth/mock-login",
    "/auth/logout",
    "/auth/extension-token",
    "/api/health",
    "/docs",
    "/openapi.json",
}


def generate_csrf_token() -> str:
    """Generate a cryptographically secure URL-safe random string for CSRF token."""
    return secrets.token_urlsafe(32)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Sets security hardening headers on every HTTP response.
    Protects against clickjacking, MIME confusion, referrer leakage, and XSS.
    """

    def __init__(self, app, settings: Settings | None = None):
        super().__init__(app)
        self.settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        # Content-Security-Policy: restrict source origins for scripts, styles, fonts, frames
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",
            "connect-src 'self' ws://localhost:8000 ws://127.0.0.1:8000 http://localhost:8000 http://127.0.0.1:8000",
            "frame-ancestors 'none'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Prevent clickjacking by forbidding iframe framing
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent browser MIME-sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Limit referrer information leaked on external requests
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Restrict dangerous browser features
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"

        # Ensure CSRF cookie is set on GET requests if not already present
        if request.method in SAFE_METHODS and CSRF_COOKIE_NAME not in request.cookies:
            new_csrf = generate_csrf_token()
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=new_csrf,
                httponly=False,  # JavaScript must be able to read it to submit in header
                samesite="lax",
                secure=self.settings.session_cookie_secure,
                path="/",
            )

        return response


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """
    Double Submit Cookie CSRF protection.
    For state-changing methods (POST, PUT, PATCH, DELETE):
    If the request uses ambient credentials (session cookie), verifies that
    the X-CSRF-Token header matches the decisionai_csrf cookie.
    Bearer token requests (from Chrome extension / API) are exempt as they are not vulnerable.
    """

    def __init__(self, app, settings: Settings | None = None):
        super().__init__(app)
        self.settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in SAFE_METHODS or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Check if request is authenticated via Bearer token in Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            # Bearer token is immune to CSRF because browsers do not attach it automatically
            return await call_next(request)

        # If request has a session cookie, enforce CSRF token check
        session_cookie = request.cookies.get(self.settings.session_cookie_name)
        if session_cookie:
            csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
            csrf_header = request.headers.get(CSRF_HEADER_NAME)

            if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF verification failed: missing or invalid CSRF token."},
                )

        return await call_next(request)
