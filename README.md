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

## Phase 0 — Project Skeleton & Config 

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

---

## Phase 1 — Database Models & Migrations 

**What was built:**
- SQLAlchemy models: `User`, `Meeting`, `TranscriptSegment`
- Meeting lifecycle status enum: `uploading → processing → ready → error`
- Alembic database migration environment and initial schema migration
- CRUD layers: `backend/crud/meetings.py`, `backend/crud/users.py` with strict user-scoping
- Pydantic request/response schemas in `backend/schemas/`
- Dev auth dependency stub in `backend/auth.py` (auto-creates dev user id=1)
- Model & CRUD test suite in `tests/test_models.py`

---

## Phase 2 — Audio Upload & Storage 

**What was built:**
- Audio upload endpoint: `POST /api/meetings/upload` accepting multipart audio (`file` or `audio_file`) and `title`
- Audio storage service in `backend/services/storage.py` with streaming 64 KB chunked writes to prevent memory spikes
- Storage disk structure: `audio_storage/{user_id}/{meeting_id}.{ext}`
- Audio format validation supporting WebM, WAV, MP3, M4A, OGG, AAC, and FLAC
- Max upload size enforcement (`MAX_UPLOAD_SIZE_MB`, default 500 MB) with 413 response and cleanup of partial files
- Empty file rejection (400) and missing file handling (422)
- Database record creation with `status=uploading` returning `MeetingResponse`
- Unit and integration tests in `tests/test_upload.py`

---

## Chrome Extension (MV3) & Live Audio Streaming 

**What was built:**
- **MV3 Extension (`/extension`)**:
  - Popup UI with Google Meet tab detection (`meet.google.com`), visible pulsating recording badge, live duration counter, and Start/Stop buttons.
  - Tab Audio Capture via `chrome.tabCapture.getMediaStreamId` and Offscreen Document (`offscreen.html` + `offscreen.js`).
  - User Microphone Capture via `navigator.mediaDevices.getUserMedia({ audio: true })`.
  - Passthrough of tab audio to `audioCtx.destination` so meeting participants remain audible.
  - Separate AudioWorklet (`pcm-processor.js`) nodes resampling tab and mic to 16 kHz mono 16-bit PCM.
  - WebSocket streaming in 1-second chunks (`TAB:` and `MIC:` binary framing) to `ws://localhost:8000/ws/meetings/{id}`.
  - In-memory FIFO queue buffering on network drops with automatic reconnection and replay.
  - Finalization flow calling `POST /meetings/{id}/finish` and opening `http://localhost:8000/meetings/{id}` in a new tab.
- **Backend Streaming Pipeline**:
  - `POST /meetings`: Create meeting record ahead of live stream (auth skipped for prototype).
  - `WebSocket /ws/meetings/{meeting_id}`: Receives PCM chunks and writes separate mono 16 kHz WAV files (`{id}_tab.wav` and `{id}_mic.wav`).
  - `POST /meetings/{id}/finish`: Closes writers, sets duration, marks meeting `READY`.
  - `GET /meetings/{id}`: Modern HTML viewer page with dark mode and audio playback for both channels.
  - `GET /meetings/{id}/audio/{channel}`: Audio file streaming endpoint.
  - Full integration test suite in `tests/test_live_recording.py`.

---

**How to run:**
```bash
# Setup
python -m venv venv
.\venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env

# Run migrations
alembic upgrade head

# Run the server
uvicorn backend.main:app --reload

# Run tests
pytest -v
```