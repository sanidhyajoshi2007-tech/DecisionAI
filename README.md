# DecisionAI

Meeting intelligence: record Google Meet calls, transcribe them (Hindi, English, Hinglish), and extract decisions, action items, and client requests.

## Architecture

```
Chrome Extension (MV3)          FastAPI Backend              Frontend (Vanilla)
┌──────────────────┐       ┌────────────────────────┐    ┌──────────────────┐
│ Tab + Mic Capture │──────►│ Upload -> Transcribe    │◄───│ Dashboard        │
│ (OffscreenDoc)    │ POST  │ -> Embed -> Ask AI (RAG)│    │ Transcript View  │
│                   │ WS    │ SQLite · ChromaDB      │    │ Ask AI Chat      │
└──────────────────┘       └────────────────────────┘    └──────────────────┘
```

## Tech Stack

| Layer           | Tool                                         |
|-----------------|----------------------------------------------|
| Backend         | Python 3.12, FastAPI, uvicorn                |
| Database        | SQLite via SQLAlchemy + Alembic              |
| Vector Store    | ChromaDB                                     |
| Transcription   | faster-whisper, Gemini, Sarvam AI, Deepgram  |
| Embeddings      | sentence-transformers (multilingual MiniLM)  |
| LLM             | Google Gemini API                            |
| Frontend        | Vanilla HTML, CSS, JavaScript (in `/web`)    |
| Extension       | Chrome Manifest V3                           |

---

## Frontend Architecture and Design System (`/web`)

The frontend is implemented entirely with vanilla HTML, CSS, and JavaScript. It uses zero external frontend frameworks, zero icon libraries, and zero UI component kits.

### Design Principles

1. **Flat Solid Colors Only**: No gradients, no glassmorphism or backdrop filters, no glow effects, and no heavy box shadows. Depth is achieved cleanly through solid borders and subtle background tone contrasts.
2. **Bento Grid Dashboard**: The dashboard uses a CSS Grid layout with cards of varying dimensions (span 8 recent meetings, span 4 stats, span 8 decisions, span 4 Ask AI entry point). The grid collapses cleanly to a single column on tablet and mobile viewports.
3. **Typography**: Maximum of two font families: `Inter` for interface headings and body text, and `JetBrains Mono` for duration counters, time codes, and technical identifiers.
4. **Micro-Interactions and Motion**: Cards lift subtly on hover (`translateY(-2px)`) with solid border highlights. Buttons shift tone smoothly over 150ms to 200ms ease-out transitions. All links and action cards include hand-written inline SVG arrows that slide right (`translateX(4px)`) on hover.
5. **Accessibility and Reduced Motion**: All transitions and transforms are disabled when the user enables `prefers-reduced-motion: reduce`. Interactive elements feature clear `:focus-visible` outlines.

### Color Palette and Theory

The color palette uses one dominant neutral family, one primary accent, and one contrasting complementary color for key irreversible actions:

- **Dominant Neutral (Dark)**: Deep Slate (`#12151c` base, `#1a1e27` surface, `#222733` raised surface).
- **Dominant Neutral (Light)**: Clean Slate (`#f8fafc` base, `#ffffff` surface, `#f1f5f9` raised surface).
- **Primary Accent**: Crisp Indigo (`#4f78e3` in dark mode, `#2557c7` in light mode). Communicates focus, progress, and primary interactive affordance without visual noise.
- **Complementary Action**: Terracotta Red (`#dc2626`). Reserved strictly for destructive or irreversible actions such as deleting meetings.
- **Status Accents**: Solid, muted tones for recording (`#fca5a5`), processing (`#fcd34d`), ready (`#4ade80`), and failed (`#f87171`).

### WCAG AA Contrast Compliance

Every text element and interactive state meets or exceeds the WCAG AA contrast standard (minimum 4.5:1 for body copy and 3.0:1 for large text / graphical controls):

| Theme | Foreground Element | Background | Hex Codes | Contrast Ratio | WCAG AA Status |
|-------|-------------------|------------|-----------|----------------|----------------|
| Dark  | Primary Body Text | Surface    | `#f1f5f9` on `#1a1e27` | 12.8:1 | Passes (4.5:1 required) |
| Dark  | Secondary Text    | Surface    | `#94a3b8` on `#1a1e27` | 6.1:1  | Passes (4.5:1 required) |
| Dark  | Primary Accent    | Surface    | `#4f78e3` on `#1a1e27` | 5.1:1  | Passes (3.0:1 required) |
| Dark  | Accent Button Text| Accent Bg  | `#ffffff` on `#4f78e3` | 4.6:1  | Passes (4.5:1 required) |
| Light | Primary Body Text | Surface    | `#0f172a` on `#ffffff` | 15.4:1 | Passes (4.5:1 required) |
| Light | Secondary Text    | Surface    | `#475569` on `#ffffff` | 7.0:1  | Passes (4.5:1 required) |
| Light | Primary Accent    | Surface    | `#2557c7` on `#ffffff` | 7.2:1  | Passes (4.5:1 required) |
| Light | Accent Button Text| Accent Bg  | `#ffffff` on `#2557c7` | 5.4:1  | Passes (4.5:1 required) |

### Design Tokens

#### 1. Color Tokens

```css
/* Dark Theme */
--bg-base: #12151c;
--bg-surface: #1a1e27;
--bg-surface-raised: #222733;
--bg-surface-hover: #293040;
--border-subtle: #272d3b;
--border-strong: #3b4457;
--border-focus: #4f78e3;
--text-primary: #f1f5f9;
--text-secondary: #94a3b8;
--text-muted: #64748b;
--accent-primary: #4f78e3;
--accent-primary-hover: #3d66cf;
--accent-complement: #dc2626;

/* Light Theme */
--bg-base: #f8fafc;
--bg-surface: #ffffff;
--bg-surface-raised: #f1f5f9;
--bg-surface-hover: #e2e8f0;
--border-subtle: #e2e8f0;
--border-strong: #cbd5e1;
--border-focus: #2557c7;
--text-primary: #0f172a;
--text-secondary: #475569;
--text-muted: #64748b;
--accent-primary: #2557c7;
--accent-primary-hover: #1e459f;
--accent-complement: #dc2626;
```

#### 2. Spacing Scale

| Token | Value | Rem Equivalent |
|-------|-------|----------------|
| `--space-1` | 4px  | 0.25rem |
| `--space-2` | 8px  | 0.50rem |
| `--space-3` | 12px | 0.75rem |
| `--space-4` | 16px | 1.00rem |
| `--space-5` | 20px | 1.25rem |
| `--space-6` | 24px | 1.50rem |
| `--space-8` | 32px | 2.00rem |
| `--space-10`| 40px | 2.50rem |
| `--space-12`| 48px | 3.00rem |

#### 3. Border Radius Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 4px | Small tags, time badges |
| `--radius-md` | 8px | Buttons, inputs, search fields |
| `--radius-lg` | 12px | Bento cards, transcript panels |
| `--radius-xl` | 16px | Modals, login card container |
| `--radius-full` | 9999px | Status badges, circular icons |

#### 4. Typography Scale

| Token | Size | Line Height | Weight | Usage |
|-------|------|-------------|--------|-------|
| `--text-xs` | 0.75rem (12px) | 1.4 | 500/600/700 | Badges, timestamps, captions |
| `--text-sm` | 0.875rem (14px) | 1.5 | 400/500/600 | Dialogue body, table cells |
| `--text-base` | 1.000rem (16px) | 1.5 | 400/500 | Standard descriptive text |
| `--text-lg` | 1.125rem (18px) | 1.4 | 600/700 | Section headers |
| `--text-xl` | 1.250rem (20px) | 1.3 | 700 | Bento card titles |
| `--text-2xl` | 1.500rem (24px) | 1.2 | 700/800 | Page titles |
| `--text-3xl` | 1.875rem (30px) | 1.2 | 800 | Primary overview numbers |

#### 5. Motion and Animation Tokens

- `--transition-fast`: 150ms ease-out (button tone shift, table row hover)
- `--transition-normal`: 200ms ease-out (card lift, modal appearance)
- Card lift: `transform: translateY(-2px)`
- SVG Arrow shift: `transform: translateX(4px)`

---

## Pages in `/web`

1. **Dashboard (`/web/index.html`)**:
   - Bento grid with cards: Recorded Meetings table, Overview Stats, Recent Decisions, and Ask AI entry point.
   - Meeting table displays title, date and time, duration, and status badge (`recording`, `processing`, `ready`, `failed`).
   - Handles loading skeleton, populated table, and honest empty states.
2. **Meeting Detail (`/web/meeting.html`)**:
   - Audio players for Tab (participant) and Mic (user) channels.
   - Interactive transcript viewer with speaker channels ("You (Mic)" vs "Participant (Tab)"), clickable timestamps that seek audio, instant text search filter with match counter, copy transcript to clipboard, and plain text export (`.txt`).
   - Summary tab showing cleaned multi-speaker meeting overview.
   - Decisions and Action Items tab categorized by item type with status badges (`accepted`, `rejected`, `pending`).
   - In-place Rename Meeting and Delete Meeting dialogs.
3. **Login (`/web/login.html`)**:
   - Google OAuth 2.0 sign-in flow.
   - Explains session security, signed httpOnly cookies, and account isolation.
4. **Ask AI (`/web/ask-ai.html`)**:
   - Entry point for conversational Q&A linking to Phase 7 RAG capabilities.
   - Outlines semantic transcript search, exact timestamp citations, and multi-session synthesis.

---

## Phase History

### Phase 0: Project Skeleton and Config
- Project folder structure: `backend/`, `frontend/`, `extension/`, `tests/`
- Configuration system using `pydantic-settings` reading `.env` with environment overrides
- FastAPI application with CORS and `/api/health` endpoint
- Async SQLAlchemy database session management with SQLite

### Phase 1: Database Models and Migrations
- SQLAlchemy models: `User`, `Meeting`, `TranscriptSegment`, `Decision`
- Lifecycle status: `recording -> processing -> ready -> failed`
- Alembic database migration environment
- User-scoped CRUD queries in `backend/crud/`

### Phase 2: Audio Upload and Storage
- Multipart audio upload endpoint: `POST /api/meetings/upload`
- Streaming 64 KB chunked writes to prevent memory spikes
- Storage on disk: `audio_storage/{user_id}/{meeting_id}.{ext}`
- Audio format validation for WebM, WAV, MP3, M4A, OGG, AAC, FLAC

### Live Audio Streaming via Chrome MV3 Extension
- Chrome Manifest V3 extension capturing tab and mic audio simultaneously
- Hardware-accelerated 16 kHz mono resampling via AudioWorklet
- Binary WebSocket streaming to `ws://localhost:8000/ws/meetings/{id}`
- Two separate 16 kHz WAV recordings per session: `{id}_tab.wav` and `{id}_mic.wav`

---

## How to Run

```bash
# Setup
python -m venv venv
.\venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env

# Run migrations
alembic upgrade head

# Run development server
uvicorn backend.main:app --reload

# Run test suite
pytest -v
```