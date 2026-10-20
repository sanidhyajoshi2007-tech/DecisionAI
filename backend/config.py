"""
Configuration — single source of truth for all settings.

Uses pydantic-settings to load from .env file and environment variables.
Environment variables always override .env file values, which makes
deployment easy: same code reads .env locally and real env vars in prod.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Extra fields in .env won't crash the app — useful when other
        # tools (Alembic, etc.) add their own env vars.
        extra="ignore",
    )

    # ── App ──
    app_name: str = "DecisionAI"
    app_env: str = "development"
    debug: bool = True

    # ── Server ──
    host: str = "127.0.0.1"
    port: int = 8000

    # ── Database ──
    # SQLite for prototype; swap to postgresql+asyncpg:// later.
    database_url: str = "sqlite+aiosqlite:///./decisionai.db"

    # ── Audio Storage ──
    audio_storage_dir: str = "audio_storage"
    max_upload_size_mb: int = 500

    # ── Transcription ──
    transcription_provider: str = "faster_whisper"
    whisper_model_size: str = "small"

    # ── Embeddings ──
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ── Vector Store ──
    chroma_persist_dir: str = "chroma_data"

    # ── LLM ──
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    # ── CORS ──
    cors_origins: str = "http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def audio_storage_path(self) -> Path:
        """Ensure audio dir exists and return as Path."""
        path = Path(self.audio_storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings singleton.
    Using lru_cache so we only read .env once per process.
    Call get_settings.cache_clear() in tests to reset.
    """
    return Settings()
