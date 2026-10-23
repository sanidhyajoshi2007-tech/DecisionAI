"""
Configuration - single source of truth for all settings.

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
        # Extra fields in .env won't crash the app - useful when other
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

    # ── Security & Authentication ──
    secret_key: str = "dev-secret-key-decisionai-super-safe-change-in-production"
    session_cookie_name: str = "decisionai_session"
    session_cookie_max_age_days: int = 14
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    extension_token_expire_minutes: int = 60
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"


    # ── Rate Limiting ──
    rate_limit_per_minute: int = 60

    # ── Database ──
    # SQLite for prototype; swap to postgresql+asyncpg:// later.
    database_url: str = "sqlite+aiosqlite:///./decisionai.db"

    # ── Audio Storage ──
    audio_storage_dir: str = "audio_storage"
    max_upload_size_mb: int = 500

    # ── Transcription (faster_whisper, gemini, sarvam, deepgram) ──
    transcription_provider: str = "faster_whisper"
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    
    # Gemini Audio STT & LLM
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_transcription_model: str = "gemini-3.8-flash"
    
    # Sarvam AI STT (Hindi & Indian languages)
    sarvam_api_key: str = ""
    sarvam_model: str = "saaras:v4"
    sarvam_mode: str = "codemix"  # codemix, transcribe, verbatim, translit
    
    # Deepgram STT
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    
    # Transcription Glossary (comma-separated hints for proper nouns / terms)
    transcription_glossary: str = "DecisionAI, Google Meet, Standup, UPI, Aadhaar"

    # ── Embeddings ──
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ── Vector Store ──
    chroma_persist_dir: str = "chroma_data"

    # ── LLM ──
    llm_provider: str = "gemini"

    # ── CORS ──
    cors_origins: str = "http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def glossary_list(self) -> list[str]:
        """Parse comma-separated glossary hints into a list."""
        return [g.strip() for g in self.transcription_glossary.split(",") if g.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def audio_storage_path(self) -> Path:
        """Ensure audio dir exists and return as Path."""
        p = Path(self.audio_storage_dir)
        if not p.is_absolute():
            base_dir = Path(__file__).resolve().parent.parent
            p = base_dir / p
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings singleton.
    Using lru_cache so we only read .env once per process.
    Call get_settings.cache_clear() in tests to reset.
    """
    return Settings()
