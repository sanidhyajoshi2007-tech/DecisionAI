# DecisionAI

Meeting intelligence — record Google Meet calls, transcribe them (Hindi, English, Hinglish), and ask an AI about decisions, action items, and client requests.

## Architecture

```
Chrome Extension (MV3)          FastAPI Backend              Frontend (Vanilla)
┌──────────────────┐       ┌────────────────────────┐    ┌──────────────────┐
│ Tab + Mic Capture │──────►│ Upload → Transcribe    │◄───│ Dashboard        │
│ (OffscreenDoc)    │ POST  │ → Embed → Ask AI (RAG) │    │ Transcript View  │
└──────────────────┘       │ SQLite · ChromaDB      │    │ Ask AI Chat      │
                           └────────────────────────┘    └──────────────────┘
```

## Tech Stack

| Layer           | Tool                                         |
|-----------------|----------------------------------------------|
| Backend         | Python 3.12, FastAPI, uvicorn                |
| Database        | SQLite via SQLAlchemy + Alembic              |
| Vector Store    | ChromaDB (pip-installed)                     |
| Transcription   | faster-whisper (batch, multilingual)         |
| Embeddings      | sentence-transformers (multilingual MiniLM)  |
| LLM             | Google Gemini API (free tier)                |
| Frontend        | Vanilla HTML / CSS / JS                      |
| Extension       | Chrome Manifest V3                           |

---

## Phase 0 — Project Skeleton & Config ✅

**What was built:**
- Project folder structure: `backend/`, `frontend/`, `extension/`, `tests/`
- Configuration system using `pydantic-settings` — reads `.env` with env-var overrides
- FastAPI app with CORS, lifespan events, and a `/api/health` endpoint
- Async SQLAlchemy database session management (SQLite + aiosqlite)
- Declarative ORM base class for future models
- Pytest fixtures with in-memory SQLite for test isolation
- `.gitignore`, `.env.example`, `requirements.txt`

**How it works:**
1. `backend/config.py` loads settings from `.env` using `pydantic-settings`. Every setting has a default, so the app runs with zero config for local dev.
2. `backend/database.py` creates an async SQLAlchemy engine and a `get_db` dependency that auto-commits/rollbacks.
3. `backend/main.py` wires up CORS, mounts the frontend as static files, and exposes `/api/health`.
4. Tests use dependency overrides to swap in an in-memory database per test.

**How to run:**
```bash
# Setup
python -m venv venv
.\venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env

# Run the server
uvicorn backend.main:app --reload

# Run tests
pytest -v
```