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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import get_settings
from backend.routers import auth_router, meetings_router

settings = get_settings()


# ── Lifespan ─────────────────────────────────────────────
# Replaces the deprecated @app.on_event("startup") pattern.
# Anything before `yield` runs on startup; after `yield` runs on shutdown.

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle."""
    # Ensure audio storage directory exists
    settings.audio_storage_path  # property call creates the dir
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


# ── CORS ─────────────────────────────────────────────────
# Needed for:
#   1. Chrome extension making fetch() calls to our API
#   2. Frontend served from a different origin during dev

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health Check ─────────────────────────────────────────

@app.get("/api/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """
    Smoke test endpoint.
    Returns 200 if the server is alive. No auth required.
    """
    return {"status": "healthy", "app": settings.app_name}


# ── Routers ──────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(meetings_router)


# ── Static Files (Frontend) ─────────────────────────────
# Mounted LAST so /api/* routes are matched before falling through
# to the static file handler. html=True serves index.html for "/".

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
