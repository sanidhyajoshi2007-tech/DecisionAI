"""
FastAPI application entry point.

Wires up:
- CORS (so the Chrome extension and frontend can talk to the API)
- Health-check endpoint (for quick smoke tests)
- Lifespan events (database init on startup)
- Static file serving for the frontend (mounted last so API routes take priority)
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from backend.config import get_settings
from backend.database import get_db
from backend.middleware import CSRFProtectionMiddleware, SecurityHeadersMiddleware
from backend.routers import auth_router, meetings_router, rag_router, users_router

settings = get_settings()


# ── Lifespan ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle."""
    # Ensure audio storage directory exists
    settings.audio_storage_path  # property call creates the dir

    # Auto-initialize database tables if they do not exist
    try:
        from backend.database import engine
        from backend.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print(f"[+] Database tables verified/created at: {settings.resolved_database_url}")
    except Exception as db_err:
        print(f"[!] Database auto-init error: {db_err}")

    print(f"[+] {settings.app_name} starting ({settings.app_env})")
    yield
    print(f"[-] {settings.app_name} shutting down")


# ── App ──────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    description="Meeting transcription & AI-powered Q&A",
    version="0.1.0",
    lifespan=lifespan,
    # Disable docs in production
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)


# ── Global Error Logging Middleware / Handler ────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    tb = traceback.format_exc()
    print(f"[!] ERROR on {request.method} {request.url.path}: {exc}\n{tb}")
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {str(exc)}",
            "path": request.url.path,
        },
    )


# ── Security & CORS Middlewares ──────────────────────────

# 1. CORS for extension and web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. Security Hardening Headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)

# 3. Double-Submit CSRF Protection for state-changing browser requests
app.add_middleware(CSRFProtectionMiddleware, settings=settings)


# ── Health Check ─────────────────────────────────────────

@app.get("/api/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """
    Smoke test endpoint.
    Returns 200 if the server is alive. No auth required.
    """
    return {"status": "healthy", "app": settings.app_name}


@app.get("/api/diag", tags=["system"])
async def diagnostic_check(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Database diagnostic endpoint to inspect table health.
    """
    try:
        res = await db.execute(text("SELECT id, email, name FROM users"))
        users = [{"id": row[0], "email": row[1], "name": row[2]} for row in res.fetchall()]
        return {
            "status": "healthy",
            "database": "connected",
            "user_count": len(users),
            "users": users,
            "has_google_client_id": bool(settings.google_client_id),
            "has_google_client_secret": bool(settings.google_client_secret),
            "redirect_uri": settings.google_redirect_uri,
            "resolved_db_url": settings.resolved_database_url.split("@")[-1],
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {str(exc)}",
            "resolved_db_url": settings.resolved_database_url.split("@")[-1],
        }


# ── Routers ──────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(meetings_router)
app.include_router(rag_router)
app.include_router(users_router)


@app.get("/api/extension/download", tags=["system"])
async def download_extension_zip():
    """
    Direct download endpoint for the packaged Chrome extension ZIP.
    Allows users and collaborators to download the extension directly.
    """
    dist_zip = Path(__file__).parent.parent / "dist" / "decisionai-chrome-extension.zip"
    if not dist_zip.exists():
        from scripts.package_extension import package_extension
        package_extension()
    return FileResponse(
        path=str(dist_zip),
        filename="decisionai-chrome-extension.zip",
        media_type="application/zip",
    )


# ── Static Files (Frontend) ─────────────────────────────
# Mounted LAST so /api/* routes are matched before falling through
# to the static file handler. html=True serves index.html for "/".

web_dir = Path(__file__).parent.parent / "web"
frontend_dir = Path(__file__).parent.parent / "frontend"

if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="web")
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="frontend")
elif frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
