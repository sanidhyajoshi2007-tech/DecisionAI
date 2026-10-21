# DecisionAI — Agent & Architecture Guide

## Overview

DecisionAI is an intelligent meeting assistant that captures audio directly from Google Meet calls, transcribes multi-speaker conversations (Hindi, English, Hinglish), extracts action items and decisions, and powers RAG-based AI Q&A over meeting transcripts.

---

## Architecture

```
Chrome MV3 Extension                  FastAPI Backend                Browser Frontend
┌────────────────────────┐      ┌───────────────────────────┐      ┌────────────────────┐
│ • Popup UI             │      │ • POST /meetings (create) │      │ • Dashboard        │
│ • Service Worker (MV3) │──────┼─► WebSocket /ws/meetings  │◄─────┼─► Meeting Viewer   │
│ • Offscreen Document   │      │ • POST /meetings/finish   │      │ • Ask AI Chat (RAG)│
│ • 16 kHz AudioWorklet  │      │ • SQLite + WAV Storage    │      └────────────────────┘
└────────────────────────┘      └───────────────────────────┘
```

---

## Chrome Extension (MV3) Architecture

### 1. `chrome.tabCapture` & Stream ID
- `chrome.tabCapture.getMediaStreamId({ targetTabId })` runs in the background service worker upon user action.
- Returns a restricted `streamId` string that can be used by `getUserMedia` to obtain the tab's audio stream.
- **Important**: Tab audio capture automatically mutes tab audio in the browser unless routed through `AudioContext.destination` in the capturing document!

### 2. Offscreen Document
- Background service workers in Manifest V3 do not have access to DOM or Web Audio APIs (`AudioContext`, `navigator.mediaDevices.getUserMedia`).
- An offscreen document (`offscreen.html` + `offscreen.js`) is created with the reason `USER_MEDIA`.
- It executes `navigator.mediaDevices.getUserMedia` for:
  1. **Tab Audio** (Other participants): `mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId }`
  2. **Microphone Audio** (Your voice): `navigator.mediaDevices.getUserMedia({ audio: true })`
- Connects tab audio to `audioCtx.destination` so you can continue hearing call participants through your headphones/speakers during the recording!

### 3. AudioWorklet (`pcm-processor.js`) & 16 kHz Mono Resampling
- An `AudioContext` is instantiated with `{ sampleRate: 16000 }` to perform hardware-accelerated anti-aliased resampling down to 16 kHz.
- Two instances of `PCMRecorderProcessor` (AudioWorklet) are instantiated:
  - `tabWorklet` (`channel: 'tab'`)
  - `micWorklet` (`channel: 'mic'`)
- The processor downmixes multi-channel audio to mono: `(ch0 + ch1) / 2`.
- Float32 audio samples are converted to signed 16-bit integer PCM (`Int16Array`).
- Exactly 16,000 samples (1 second at 16 kHz = 32,000 bytes) are buffered before being transferred to the offscreen thread via `postMessage`.

### 4. WebSocket Streaming & Reconnect Buffering
- Offscreen connects to `ws://localhost:8000/ws/meetings/{meeting_id}`.
- Chunks are framed with a 4-byte ASCII identifier:
  - `TAB:` + 32,000 bytes PCM (Tab audio)
  - `MIC:` + 32,000 bytes PCM (Microphone audio)
- If the WebSocket disconnects, chunks are pushed into an in-memory queue (`bufferQueue`) and connection retries occur every 1.5 seconds.
- Upon reconnection, the buffer is drained and flushed in FIFO order.

### 5. Finalization
- When the user clicks **Stop Recording**:
  1. The worklets are flushed and stopped.
  2. The media tracks are closed.
  3. Pending chunks are sent over the WebSocket before closing.
  4. Offscreen calls `POST http://localhost:8000/meetings/{meeting_id}/finish`.
  5. The service worker closes the offscreen document and opens `http://localhost:8000/meetings/{meeting_id}` in a new tab.

---

## Backend Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/meetings` | Create meeting ahead of live streaming (returns `id`, `title`, `status`) |
| `POST` | `/api/meetings/upload` | Multipart file upload (Phase 2 legacy) |
| `WS` | `/ws/meetings/{id}` | Live WebSocket audio stream; saves `{id}_tab.wav` and `{id}_mic.wav` |
| `POST` | `/meetings/{id}/finish` | Finalizes recording, computes duration, marks status as `ready` |
| `GET` | `/meetings/{id}` | HTML viewer with audio players for both tab and microphone recordings |
| `GET` | `/meetings/{id}/audio/{channel}` | Streams recorded WAV audio (`tab` or `mic`) |
| `GET` | `/api/health` | Health check endpoint |

---

## Speech-to-Text (STT) Architecture

DecisionAI features a pluggable, multi-provider transcription pipeline in `backend/services/stt/`:

### Providers
1. **faster-whisper** (`FasterWhisperProvider`): Local offline inference using CTranslate2 (int8/float16). Fast, private, and zero API cost. Supports domain glossaries via `initial_prompt`.
2. **Google Gemini** (`GeminiTranscriptionProvider`): Multimodal audio understanding via Google GenAI REST API (`gemini-2.5-flash`). Handles conversational Hindi/English code-switching and structured JSON timestamps.
3. **Sarvam AI** (`SarvamTranscriptionProvider`): Indian language specialist (`saaras:v4`). Built for Indian accents and regional dialects with native `codemix` mode and `prompt` keyterms.
4. **Deepgram** (`DeepgramTranscriptionProvider`): Cloud speech platform with `nova-3`, `utterances=true`, and keyword boosting (`Term:2`).

### Chunking & Chronological Channel Merge
- When `/meetings/{id}/finish` is called, a background task (`process_meeting_transcription`) triggers.
- Long audio files are chunked into 30-second segments with 0.5-second overlap to respect API limits and prevent memory exhaustion.
- Tab audio (participants) and Mic audio (user) are transcribed concurrently.
- Segments from both channels are chronologically sorted and merged by `start_ms`.
- Each segment stores: `meeting_id`, `speaker` ("You (Mic)" vs "Participant (Tab)"), `start_ms`, `end_ms`, `start_time`, `end_time`, `text`, `raw_text`, and `language`.

### Hindi & Hinglish Benchmarking
- `scripts/benchmark.py`: Evaluates providers against human-corrected ground truth transcripts.
- Performs Unicode NFC normalization, nukta/danda cleanup for Devanagari Hindi, and phonetic equivalence token mappings for Hinglish.
- Calculates Word Error Rate (WER) via Levenshtein distance and renders a side-by-side comparison table.
- Run demo benchmark: `python scripts/benchmark.py --demo`

---

## Testing Guidelines

```bash
# Run pytest test suite
.\venv\Scripts\pytest -v

# Run backend development server
.\venv\Scripts\uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```
