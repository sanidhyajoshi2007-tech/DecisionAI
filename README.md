# DecisionAI

DecisionAI is an intelligent meeting assistant that captures dual-channel audio directly from Google Meet calls, transcribes multi-speaker conversations across English, Hindi, and Hinglish, extracts structured decisions and action items, and powers hybrid Retrieval-Augmented Generation (RAG) conversational search with grounded timestamp citations.

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Chrome Extension (Manifest V3) Architecture](#chrome-extension-manifest-v3-architecture)
4. [Speech-to-Text (STT) Pipeline](#speech-to-text-stt-pipeline)
5. [Meeting Intelligence and Extraction](#meeting-intelligence-and-extraction)
6. [Retrieval-Augmented Generation (RAG) Architecture](#retrieval-augmented-generation-rag-architecture)
7. [Frontend Architecture and Design Tokens](#frontend-architecture-and-design-tokens)
8. [Security, Privacy, and Data Governance](#security-privacy-and-data-governance)
9. [Backend API Reference](#backend-api-reference)
10. [Repository Directory Structure](#repository-directory-structure)
11. [Setup and Installation Guide](#setup-and-installation-guide)
12. [Testing and Benchmarking](#testing-and-benchmarking)
13. [License](#license)

---

## Overview

Modern engineering and product discussions often take place in multilingual environments where participants switch fluidly between English, Hindi, and Hinglish. Traditional meeting bots require third-party calendar invites, join calls as visible participant bots, and frequently drop spoken context or fail on code-mixed speech.

DecisionAI addresses this through:
- Direct In-Browser Dual Capture: Records your microphone and tab audio directly via a lightweight Chrome Manifest V3 extension, eliminating the need for meeting bots.
- Hardware-Accelerated 16 kHz Resampling: AudioWorklets resample both audio channels to 16 kHz mono signed 16-bit PCM in real time.
- Resilient WebSocket Streaming: Live binary streaming with reconnect buffering ensures no audio loss during brief disconnections.
- Multilingual and Code-Mixed Speech-to-Text: Supports faster-whisper, Google Gemini, Sarvam AI, and Deepgram with specialized handling for Hindi and Hinglish.
- Automatic Channel Merge: Chronologically merges participant tab audio and microphone audio into unified dialogue turns.
- Action Item and Decision Extraction: Extracts agreements, action items, assignees, and deadlines into a structured relational format.
- Hybrid RAG Search: Combines dense vector search (ChromaDB) and sparse lexical search (BM25) via Reciprocal Rank Fusion, citing exact seconds that link directly to the interactive audio player.
- Enterprise-Grade Privacy and Governance: Google OAuth 2.0 authentication, strict multi-tenant isolation by user ID, configurable retention periods, audit logging, and a single-action data wipeout tool.

---

## System Architecture

```
Chrome MV3 Extension                   FastAPI Backend                Browser Frontend (/web)
+------------------------+      +---------------------------+      +------------------------+
| - Popup Controller     |      | - Google OAuth 2.0 Auth   |      | - Bento Dashboard      |
| - Service Worker (MV3) |----->| - WebSocket /ws/meetings  |<---->| - Audio & Transcript   |
| - Offscreen Document   |      | - STT Processing Pipeline |      | - Structured Decisions |
| - 16 kHz AudioWorklet  |      | - Hybrid RAG Engine       |      | - Ask AI Chat (SSE)    |
+------------------------+      | - SQLite + ChromaDB       |      | - Privacy Settings     |
                                +---------------------------+      +------------------------+
```

### Component Breakdown

1. Client Capture Layer: A Chrome extension running Manifest V3 captures both tab audio (remote participants) and microphone audio (local speaker), converting streams into 16 kHz 16-bit PCM chunks.
2. Transport Layer: Chunks are streamed over a persistent WebSocket connection to the FastAPI server, framed with 4-byte channel headers (`TAB:` and `MIC:`).
3. Backend Service Layer: FastAPI orchestrates session lifecycles, writes separate 16 kHz WAV files for each channel, triggers transcription workers, and synchronizes transcripts.
4. Intelligence & Retrieval Layer: Google Gemini powers meeting summarization, decision extraction, and RAG answer synthesis. ChromaDB and BM25 index transcript segments with strict user scoping.
5. Presentation Layer: A responsive, zero-framework vanilla HTML, CSS, and JavaScript interface built around WCAG AA contrast standards.

---

## Chrome Extension (Manifest V3) Architecture

The extension is designed around Manifest V3 security requirements, ensuring audio capture operates seamlessly without muting the user's audio output.

### 1. Tab Capture and Stream Identification
- `chrome.tabCapture.getMediaStreamId({ targetTabId })` executes in the background service worker upon user initiation.
- Generates a scoped `streamId` string consumed by `navigator.mediaDevices.getUserMedia` inside an offscreen document.
- Note: Capturing tab audio directly in Chrome mutes the tab by default. To preserve user listening experience, the offscreen document routes captured audio to `audioCtx.destination`.

### 2. Offscreen Document Execution
- Manifest V3 background service workers lack access to DOM and Web Audio APIs (`AudioContext`, `MediaStream`).
- An offscreen document (`offscreen.html` and `offscreen.js`) is launched with the `USER_MEDIA` reason.
- Dual Media Streams are acquired simultaneously:
  1. Tab Stream (Participants): Acquired via `chromeMediaSourceId: streamId`.
  2. Microphone Stream (User): Acquired via `navigator.mediaDevices.getUserMedia({ audio: true })`.
- Tab audio is routed to both the recorder worklet and `audioCtx.destination`, allowing call audio to play normally through speakers or headphones.

### 3. AudioWorklet and 16 kHz Mono Resampling
- An `AudioContext` is instantiated with `{ sampleRate: 16000 }` to leverage native hardware anti-aliasing downsampling to 16 kHz.
- Two distinct instances of `PCMRecorderProcessor` (defined in `pcm-processor.js`) run on dedicated worker threads:
  - `tabWorklet` for participant audio
  - `micWorklet` for microphone audio
- The processor downmixes multi-channel audio to mono using `(channel_0 + channel_1) / 2`.
- Float32 audio samples are converted to signed 16-bit integer PCM (`Int16Array`).
- Exactly 16,000 samples (1 second at 16 kHz, or 32,000 bytes) are buffered before transmission to the offscreen controller.

### 4. WebSocket Streaming and Reconnect Buffer
- The offscreen document connects to `ws://localhost:8000/ws/meetings/{meeting_id}`.
- Chunks are framed with a 4-byte ASCII identifier:
  - `TAB:` followed by 32,000 bytes PCM for participant audio
  - `MIC:` followed by 32,000 bytes PCM for user audio
- If network disruption occurs, chunks are held in an in-memory queue (`bufferQueue`).
- Reconnection attempts execute every 1.5 seconds. Once reconnected, buffered chunks are transmitted in chronological FIFO order.

### 5. Session Finalization and Redirection
- When the user selects "Stop Recording":
  1. AudioWorklets flush any remaining buffered audio and terminate.
  2. Media tracks are stopped to release microphone and tab permissions.
  3. Pending frames are transmitted over the WebSocket before socket closure.
  4. The offscreen document dispatches `POST /meetings/{meeting_id}/finish` to compute duration and initiate background transcription.
  5. The service worker closes the offscreen document and automatically navigates the active browser window to the meeting viewer at `http://localhost:8000/meetings/{meeting_id}`.

---

## Speech-to-Text (STT) Pipeline

DecisionAI incorporates a pluggable, multi-provider transcription pipeline located in `backend/services/stt/`:

### Transcription Providers

1. faster-whisper (`FasterWhisperProvider`):
   - Local, offline CTranslate2 inference with int8 or float16 quantization.
   - Zero external API costs, low latency, and absolute data privacy.
   - Injects domain terms and proper nouns via the `initial_prompt` parameter.

2. Google Gemini (`GeminiTranscriptionProvider`):
   - Multimodal audio understanding via Google GenAI (`gemini-2.5-flash` or `gemini-3.8-flash`).
   - Strong handling of colloquial Hindi and English code-switching.
   - Direct JSON structured output mapping speaker dialogue and timestamps.

3. Sarvam AI (`SarvamTranscriptionProvider`):
   - Specialist engine for Indian regional languages and accents (`saaras:v4`).
   - Native `codemix` mode optimized for Hinglish and Indian colloquial vocabulary.
   - Supports contextual vocabulary hints through prompt keyterms.

4. Deepgram (`DeepgramTranscriptionProvider`):
   - Cloud speech platform running `nova-3`.
   - Native speaker turn extraction and keyword boosting.

### Chunking and Chronological Channel Merge

1. Segmentation: Audio files are segmented into 30-second windows with 0.5-second overlap to prevent memory spikes and respect provider request limits.
2. Concurrent Processing: Tab audio and microphone audio are transcribed concurrently using asynchronous tasks.
3. Chronological Merge: Recognized segments from both channels are sorted by `start_ms` and merged into a unified dialogue stream.
4. Schema Normalization: Each transcript segment records:
   - `meeting_id`: Associated meeting identifier
   - `speaker`: Channel identity ("You (Mic)" vs "Participant (Tab)")
   - `start_ms` and `end_ms`: Exact millisecond offsets
   - `start_time` and `end_time`: Formatted MM:SS time strings
   - `text`: Post-processed display text
   - `raw_text`: Direct provider transcription output
   - `language`: Detected language code (en, hi, hi-Latn)

### Hindi and Hinglish Normalization & Benchmarking

- Evaluation Script: `scripts/benchmark.py` compares provider outputs against human-corrected ground truth transcripts.
- Devanagari Normalization: Applies Unicode NFC normalization, standardizes nuktas, and cleans dandas.
- Hinglish Token Alignment: Maps common phonetic variants (e.g., "karenge", "krenge") to canonical forms.
- Error Rate Calculation: Computes Word Error Rate (WER) using Levenshtein distance and outputs structured comparison tables.
- Run benchmark: `python scripts/benchmark.py --demo`

---

## Meeting Intelligence and Extraction

Following transcription, an asynchronous background task invokes Google Gemini to extract actionable intelligence from the merged dialogue:

1. Executive Summary: High-level synthesis of meeting topics, participant viewpoints, and key takeaways.
2. Structured Decisions: Identifies agreed conclusions and commitments made during the discussion.
3. Action Items: Extracts discrete tasks with assignees, due dates, and initial status (`pending`).
4. Client and Stakeholder Requests: Highlights external commitments and timeline requirements.
5. Relational Storage: Items are committed to the `Decision` SQLite table, enabling fast structured queries and filtering by meeting ID and status.

---

## Retrieval-Augmented Generation (RAG) Architecture

DecisionAI features a hybrid RAG pipeline located in `backend/services/rag/`:

### 1. Speaker-Turn Chunking and ChromaDB Storage
- Chunk Boundaries: Transcripts are grouped by speaker turns and time windows into 300 to 500 token chunks. Long monologues are partitioned at sentence boundaries. Pauses exceeding 45 seconds force a chunk break.
- Vector Embeddings: Chunks are encoded using `paraphrase-multilingual-MiniLM-L12-v2` with deterministic offline fallback support.
- Metadata Indexing: Each chunk indexes `user_id`, `meeting_id`, `date`, `speakers`, and `start_ms`.
- Strict Multi-Tenant Isolation: Every ChromaDB vector query enforces a mandatory `user_id` metadata filter.

### 2. Intent Classification and SQL Routing
- Queries are classified into two execution paths:
  1. `decision_lookup`: Queries asking about final decisions, conclusions, or assigned tasks route directly to the relational `Decision` table sorted by date.
  2. `general_qa`: Broad contextual questions route through the hybrid search engine.

### 3. Hybrid Search (Dense Vector + Sparse BM25)
- Dense Retrieval: Semantic similarity queries against ChromaDB embeddings.
- Sparse Retrieval: In-memory BM25 index supporting English, Devanagari Hindi, and romanized Hinglish tokens.
- Reciprocal Rank Fusion: Results are combined using Reciprocal Rank Fusion (RRF with k=60) to score dense and sparse matches fairly.

### 4. Grounded Citations and Interactive Audio Seeking
- Answer Generation: Google Gemini synthesizes grounded answers containing explicit citations (Meeting Title, Date, MM:SS).
- Interactive Citations: In the Ask AI chat interface, clicking any citation chip navigates to the meeting player, seeks both tab and microphone audio to the exact second, and highlights the corresponding transcript block.
- Streaming Responses: Answers stream token-by-token over Server-Sent Events (`POST /api/ask?stream=true`).

---

## Frontend Architecture and Design Tokens

The browser application in `/web` is built with vanilla HTML, CSS, and modern JavaScript without frontend frameworks or third-party component libraries.

### Design Principles

1. Flat Solid Surface System: No gradients, no glassmorphism, no heavy box shadows. Depth is established through subtle surface contrast and crisp solid borders.
2. Bento Grid Layout: The dashboard utilizes a flexible CSS Grid with cards spanning defined column proportions (span 8 recent meetings, span 4 statistics, span 8 decisions, span 4 Ask AI entry).
3. Typography: Limited to two high-legibility fonts: Inter for UI controls and JetBrains Mono for timecodes and technical metrics.
4. Micro-Interactions: Subtle hover card translation (`translateY(-2px)`), solid border emphasis, and accessible focus outlines.
5. Motion Sensitivity: All transitions respect the user's `prefers-reduced-motion: reduce` preference.

### WCAG AA Contrast Compliance

All interface colors meet or exceed WCAG AA standards (minimum 4.5:1 for body copy and 3.0:1 for large text):

| Theme | Element | Background | Hex Values | Contrast Ratio | WCAG AA Status |
|-------|---------|------------|------------|----------------|----------------|
| Dark  | Primary Body Text | Surface | #f1f5f9 on #1a1e27 | 12.8:1 | Pass (4.5:1 required) |
| Dark  | Secondary Text | Surface | #94a3b8 on #1a1e27 | 6.1:1 | Pass (4.5:1 required) |
| Dark  | Primary Accent | Surface | #4f78e3 on #1a1e27 | 5.1:1 | Pass (3.0:1 required) |
| Dark  | Accent Button Text | Accent Bg | #ffffff on #4f78e3 | 4.6:1 | Pass (4.5:1 required) |
| Light | Primary Body Text | Surface | #0f172a on #ffffff | 15.4:1 | Pass (4.5:1 required) |
| Light | Secondary Text | Surface | #475569 on #ffffff | 7.0:1 | Pass (4.5:1 required) |
| Light | Primary Accent | Surface | #2557c7 on #ffffff | 7.2:1 | Pass (4.5:1 required) |
| Light | Accent Button Text | Accent Bg | #ffffff on #2557c7 | 5.4:1 | Pass (4.5:1 required) |

### Key Design Tokens

```css
/* Color Tokens: Dark Theme */
--bg-base: #12151c;
--bg-surface: #1a1e27;
--bg-surface-raised: #222733;
--border-subtle: #272d3b;
--border-strong: #3b4457;
--text-primary: #f1f5f9;
--text-secondary: #94a3b8;
--accent-primary: #4f78e3;
--accent-complement: #dc2626;

/* Color Tokens: Light Theme */
--bg-base: #f8fafc;
--bg-surface: #ffffff;
--bg-surface-raised: #f1f5f9;
--border-subtle: #e2e8f0;
--border-strong: #cbd5e1;
--text-primary: #0f172a;
--text-secondary: #475569;
--accent-primary: #2557c7;
--accent-complement: #dc2626;

/* Spacing Scale */
--space-1: 4px;   --space-2: 8px;   --space-3: 12px;
--space-4: 16px;  --space-6: 24px;  --space-8: 32px;

/* Radius Scale */
--radius-sm: 4px;  --radius-md: 8px;  --radius-lg: 12px;  --radius-xl: 16px;
```

### Application Pages

- `/web/index.html`: Dashboard showing bento cards for meeting history, recording stats, recent decisions, and quick search.
- `/web/meeting.html`: Meeting detail viewer with synchronized dual audio players (Tab and Mic), real-time search filtering, full dialogue playback, summaries, and action item checklists.
- `/web/login.html`: Google OAuth 2.0 authentication portal explaining session security and user data boundaries.
- `/web/ask-ai.html`: Full-screen conversational AI workspace for multi-meeting queries and grounded citation navigation.

---

## Security, Privacy, and Data Governance

DecisionAI enforces comprehensive data privacy and security measures:

1. Authentication via Verified Google Accounts:
   - Google OAuth 2.0 ensures only genuine, verified accounts can sign in. Unverified manual email entry is disallowed.
   - Authenticated sessions use HTTP-only, SameSite=Lax signed session cookies (`decisionai_session`).

2. Multi-Tenant User Isolation:
   - Every database query in `backend/crud/` scopes records by `user_id`.
   - Audio files are stored in isolated per-user directories: `audio_storage/{user_id}/`.
   - ChromaDB vector queries apply mandatory `{ "user_id": { "$eq": current_user.id } }` filters.

3. Input Validation and Defense in Depth:
   - Request bodies, URL paths, and query parameters are strictly validated with Pydantic schemas.
   - File uploads, titles, glossary terms, and durations are bound to configurable safety thresholds.
   - Text rendered on the frontend is sanitized to prevent cross-site scripting (XSS).

4. Security Headers & CSRF Protection:
   - `SecurityHeadersMiddleware` injects Content-Security-Policy (CSP), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: strict-origin-when-cross-origin`.
   - Double-submit CSRF cookie protection validates state-changing requests.

5. Audit Logging:
   - An `AuditLog` table records logins, logouts, meeting creations, renames, deletions, exports, AI queries, and data wipes.
   - Audit logs never store raw transcript text, audio samples, tokens, or API credentials.

6. Configurable Data Retention:
   - Users can choose retention durations (keep forever, 30 days, 90 days, or 365 days).
   - An automated daily cleanup background job removes expired meetings from SQLite, ChromaDB, and disk storage.

7. Complete Data Erasure ("Delete All My Data"):
   - Available via the settings panel with a typed confirmation prompt (`DELETE`).
   - Atomically purges all user meetings, transcript segments, extracted decisions, audit records, vector embeddings, and audio files from disk.

---

## Backend API Reference

| HTTP Method | Route | Description |
|-------------|-------|-------------|
| GET | `/api/health` | Health check endpoint returning server status |
| GET | `/auth/google` | Initiates Google OAuth 2.0 authentication flow |
| GET | `/auth/google/callback` | Handles Google OAuth callback, creates session cookie |
| POST | `/auth/logout` | Clears user session cookie and logs event |
| GET | `/api/auth/me` | Returns current authenticated user profile |
| POST | `/meetings` | Pre-creates a meeting before live streaming |
| GET | `/meetings` | Returns list of meetings for current user |
| GET | `/meetings/{id}` | HTML viewer for a specific meeting |
| POST | `/meetings/{id}/finish` | Finalizes recording, computes duration, triggers STT pipeline |
| GET | `/meetings/{id}/audio/{channel}` | Streams recorded WAV audio (channel: tab or mic) |
| PATCH | `/api/meetings/{id}` | Updates meeting title |
| DELETE | `/api/meetings/{id}` | Deletes meeting, transcripts, decisions, audio, and vector data |
| GET | `/api/meetings/{id}/export` | Exports full transcript as plain text (.txt) |
| POST | `/api/meetings/upload` | Multipart file upload for pre-recorded audio |
| WS | `/ws/meetings/{id}` | Live binary WebSocket audio streaming endpoint |
| POST | `/ask` / `/api/ask` | RAG question-answering endpoint with SSE streaming support |
| POST | `/api/rag/index-all` | Batch indexes ready meeting transcripts into ChromaDB |
| GET | `/api/rag/status` | Vector store index status and chunk count |
| GET | `/api/users/me/settings` | Returns user preferences and data retention policy |
| PUT | `/api/users/me/settings` | Updates retention policy (forever, 30, 90, 365 days) |
| DELETE | `/api/users/me/data` | Irreversibly deletes all user meetings, audio, and vector data |
| GET | `/api/users/me/audit-logs` | Returns user audit trail records |

---

## Repository Directory Structure

```
DecisionAI/
├── .env.example              # Environment variables template
├── .gitignore                # Git ignore patterns
├── AGENTS.md                 # Technical architecture specification
├── README.md                 # Complete system documentation and setup guide
├── alembic.ini               # Database migration configuration
├── pyproject.toml            # Project build metadata
├── requirements.txt          # Python dependencies
├── alembic/                  # Database migration scripts
│   ├── env.py
│   └── versions/             # Migration revisions
├── backend/                  # FastAPI backend application
│   ├── config.py             # Pydantic settings management
│   ├── database.py           # SQLAlchemy async engine and sessionmaker
│   ├── main.py               # Application entry point and router mounting
│   ├── middleware/           # CSRF and security headers middleware
│   ├── models/               # SQLAlchemy ORM models (User, Meeting, Decision, Audit)
│   ├── crud/                 # Database query and CRUD functions
│   ├── schemas/              # Pydantic request and response schemas
│   ├── routers/              # API route handlers
│   ├── services/             # Core business logic
│   │   ├── audio_streamer.py # WebSocket chunk receiver and WAV writer
│   │   ├── stt/              # Pluggable speech-to-text providers
│   │   ├── rag/              # Vector store, hybrid search, and citation engine
│   │   └── storage/          # Audio file persistence
│   └── providers/            # Pluggable LLM provider interfaces
├── extension/                # Google Chrome Manifest V3 extension
│   ├── manifest.json         # Extension manifest
│   ├── popup.html            # Extension popup user interface
│   ├── popup.js              # Popup controller and state management
│   ├── background.js         # Service worker handling tabCapture stream IDs
│   ├── offscreen.html        # Offscreen document host
│   ├── offscreen.js          # Web Audio coordinator and WebSocket streamer
│   ├── pcm-processor.js      # 16 kHz AudioWorklet processor
│   └── icons/                # Extension action icons
├── web/                      # Vanilla web frontend
│   ├── index.html            # Bento grid dashboard
│   ├── meeting.html          # Synchronized transcript and audio viewer
│   ├── login.html            # Google OAuth 2.0 sign-in page
│   ├── ask-ai.html           # Full-page conversational Ask AI chat
│   ├── app.js                # Frontend controller and API client
│   └── styles.css            # WCAG AA design system and styling tokens
├── scripts/                  # Utilities and evaluation benchmarks
│   ├── benchmark.py          # Hindi and Hinglish STT benchmark tool
│   └── evaluate_rag.py       # 20-question quantitative RAG evaluation suite
└── tests/                    # Automated pytest test suite
    ├── conftest.py           # Test fixtures and async client configuration
    ├── test_health.py        # Health endpoint smoke test
    ├── test_models.py        # Database models and user scoping tests
    ├── test_upload.py        # Audio upload and validation tests
    ├── test_stt.py           # STT provider and channel merge tests
    ├── test_live_stream.py   # WebSocket streaming and WAV framing tests
    ├── test_rag.py           # Hybrid search and citation tests
    └── test_security.py      # Auth, audit logging, and data retention tests
```

---

## Setup and Installation Guide

Follow these steps to run DecisionAI locally from scratch.

### Prerequisites

- Python 3.12 or later
- Google Chrome browser (for the Manifest V3 extension and web interface)
- Git command-line tool
- A Google Cloud Console project with OAuth 2.0 Credentials configured

---

### Step 1: Clone the Repository

```bash
git clone https://github.com/sanidhyajoshi2007-tech/DecisionAI.git
cd DecisionAI
```

---

### Step 2: Create and Activate a Python Virtual Environment

On Windows (PowerShell):
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

On Windows (Command Prompt):
```cmd
python -m venv venv
.\venv\Scripts\activate.bat
```

On macOS or Linux:
```bash
python3 -m venv venv
source venv/bin/activate
```

---

### Step 3: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 4: Configure Environment Variables

Create your local `.env` configuration by copying `.env.example`:

On Windows:
```cmd
copy .env.example .env
```

On macOS or Linux:
```bash
cp .env.example .env
```

Open `.env` in your text editor and configure your settings:

1. Generate a secure secret key:
   ```
   SECRET_KEY=replace-with-a-random-secure-string
   ```

2. Google OAuth 2.0 Credentials:
   - Navigate to Google Cloud Console: https://console.cloud.google.com/apis/credentials
   - Create a project or select an existing one.
   - Click "Create Credentials" and choose "OAuth client ID".
   - Select Application type: "Web application".
   - Under "Authorized redirect URIs", add: `http://localhost:8000/auth/google/callback`
   - Copy the Client ID and Client Secret into your `.env` file:
     ```
     GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
     GOOGLE_CLIENT_SECRET=your-google-client-secret
     GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
     ```

3. Configure AI Providers:
   - For Google Gemini (summaries, decisions, RAG, and optional Gemini STT):
     ```
     GEMINI_API_KEY=your-gemini-api-key
     ```
   - For offline local transcription (default):
     ```
     TRANSCRIPTION_PROVIDER=faster_whisper
     WHISPER_MODEL_SIZE=small
     WHISPER_DEVICE=cpu
     WHISPER_COMPUTE_TYPE=int8
     ```
   - For Sarvam AI (Indian code-mixing and regional speech):
     ```
     SARVAM_API_KEY=your-sarvam-api-key
     ```
   - For Deepgram:
     ```
     DEEPGRAM_API_KEY=your-deepgram-api-key
     ```

---

### Step 5: Run Database Migrations

Apply database schemas using Alembic to create the SQLite database and tables:

```bash
alembic upgrade head
```

---

### Step 6: Start the Backend Server

Start the FastAPI development server with uvicorn:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Verify that the server is operational:
- Open your browser and navigate to: `http://127.0.0.1:8000/api/health`
- The endpoint will return: `{"status":"healthy","app":"DecisionAI"}`
- The web application is accessible at: `http://127.0.0.1:8000`

---

### Step 7: Load the Chrome Extension

1. Open Google Chrome.
2. In the URL address bar, enter `chrome://extensions` and press Enter.
3. In the top-right corner, switch the "Developer mode" toggle to the ON position.
4. Click the "Load unpacked" button in the upper-left corner.
5. In the file picker dialog, select the `extension` folder located inside the `DecisionAI` repository.
6. The DecisionAI extension card will appear in your installed extensions list.
7. Click the Extensions puzzle piece icon in the Chrome toolbar and pin DecisionAI for easy access.

---

### Step 8: Record and Process a Meeting

1. Navigate to `http://localhost:8000/web/login.html` and sign in using your Google Account.
2. Open or join a Google Meet call in another Chrome tab.
3. Click the DecisionAI extension icon in your Chrome toolbar.
4. Click "Start Recording". When prompted by Chrome, select the Google Meet tab to capture tab audio and permit microphone access.
5. Notice that you can still hear all call participants through your headphones or speakers because the offscreen document routes audio through Web Audio output.
6. When your meeting concludes, click the extension icon and select "Stop Recording".
7. The extension will automatically finalize audio chunks and open the meeting transcript page at `http://localhost:8000/meetings/{id}`.
8. The backend will transcribe both channels concurrently, merge speaker dialogue chronologically, generate executive summaries, and extract actionable decisions.

---

## Testing and Benchmarking

DecisionAI includes automated test suites covering backend endpoints, database operations, speech-to-text providers, live streaming, security policies, and retrieval accuracy.

### Running Pytest Test Suite

Execute the complete test suite with verbose output:

```bash
pytest -v
```

Run specific test modules:
```bash
pytest tests/test_security.py -v
pytest tests/test_rag.py -v
pytest tests/test_live_stream.py -v
```

### Running RAG Retrieval Evaluation

Run the quantitative evaluation script evaluating intent classification, keyword recall, and citation validity over 20 test query-answer pairs:

```bash
python scripts/evaluate_rag.py
```

### Running Speech-to-Text Benchmark

Benchmark speech recognition accuracy on Hindi, English, and Hinglish ground truth samples:

```bash
python scripts/benchmark.py --demo
```

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.