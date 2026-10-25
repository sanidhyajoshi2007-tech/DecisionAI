"""
Generate an executive-grade PDF document of the DecisionAI Project Journey.
Uses ReportLab Platypus framework with custom styles, callout blocks, and summary tables.
Strict constraints: Zero emojis, zero em dashes.
"""

import sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print total page numbers."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages):
        self.saveState()
        self.setFont("Helvetica", 8.5)
        self.setFillColor(colors.HexColor("#718096"))
        
        # Header (Pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 11 * inch - 36, "DecisionAI: Engineering Journey (Phase 0 to Phase 8)")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 11 * inch - 42, 8.5 * inch - 54, 11 * inch - 42)

        # Footer (All pages)
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(8.5 * inch - 54, 32, page_str)
        self.drawString(54, 32, "Confidential: DecisionAI Architecture Documentation")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 44, 8.5 * inch - 54, 44)
        
        self.restoreState()


def build_pdf(filename="DecisionAI_Project_Journey.pdf"):
    pdf_path = Path(filename).resolve()
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    NAVY = colors.HexColor("#1A365D")
    BLUE = colors.HexColor("#2B6CB0")
    DARK = colors.HexColor("#2D3748")
    MUTED = colors.HexColor("#4A5568")

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=NAVY,
        alignment=1,  # Center
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=BLUE,
        alignment=1,  # Center
        spaceAfter=14,
    )

    h1_style = ParagraphStyle(
        "H1Style",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=NAVY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "H2Style",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=BLUE,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=DARK,
        spaceAfter=6,
    )

    bullet_style = ParagraphStyle(
        "BulletStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=DARK,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=3,
    )

    callout_title_style = ParagraphStyle(
        "CalloutTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=13,
        textColor=BLUE,
        spaceAfter=2,
    )

    callout_body_style = ParagraphStyle(
        "CalloutBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=DARK,
    )

    story = []

    # Title Block
    story.append(Paragraph("DecisionAI: Complete Engineering Journey", title_style))
    story.append(Paragraph("From Inception (Phase 0) to Thought-Partner Meeting Intelligence", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=BLUE, spaceBefore=0, spaceAfter=10))

    # Meta banner table
    meta_data = [
        [
            Paragraph("<b>Stack:</b> FastAPI + MV3 + ChromaDB", body_style),
            Paragraph("<b>Timeline:</b> Phase 0 through Phase 8", body_style),
            Paragraph("<b>Status:</b> 61/61 Tests Passing (Production)", body_style),
        ]
    ]
    t_meta = Table(meta_data, colWidths=[2.3 * inch, 2.3 * inch, 2.4 * inch])
    t_meta.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF2F7")),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E0")),
            ]
        )
    )
    story.append(t_meta)
    story.append(Spacer(1, 10))

    def make_callout(title, text):
        content = [
            Paragraph(f"<b>{title}:</b>", callout_title_style),
            Paragraph(text, callout_body_style),
        ]
        t = Table([[content]], colWidths=[7.0 * inch])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                    ("LINEBEFORE", (0, 0), (0, 0), 3.5, BLUE),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        return t

    # 1. Executive Summary
    story.append(Paragraph("1. Executive Summary & Vision", h1_style))
    story.append(
        Paragraph(
            "DecisionAI was built to replace clumsy, invasive meeting bots with native browser capture "
            "and active thought-partner intelligence. Traditional bots require calendar invitations, appear as "
            "unwanted accounts in call recordings, and drop speech context during bilingual English, Hindi, and "
            "Hinglish discussions. DecisionAI solves these limitations with direct dual-channel audio capture, "
            "sub-second 16 kHz AudioWorklet processing, pluggable multilingual speech recognition, and hybrid RAG search.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Bot-Free Capture:</b> Records tab and mic audio directly via a Manifest V3 extension.<br/>"
            "- <b>Signal Precision:</b> Real-time 16 kHz mono Int16 PCM conversion via dedicated AudioWorklets.<br/>"
            "- <b>Linguistic Inclusivity:</b> Accurate transcription across English, Hindi, and Hinglish code-switching.<br/>"
            "- <b>Thought-Partner AI:</b> Synthesizes decisions and provides constructive technical recommendations rather than serving as a passive archive.",
            bullet_style,
        )
    )

    # 2. Phase 0
    story.append(Paragraph("2. Phase 0: Project Skeleton & Configuration Management", h1_style))
    story.append(
        Paragraph(
            "Phase 0 established the project scaffolding, dependency management, and centralized settings. "
            "The backend utilizes FastAPI for asynchronous concurrency, SQLite with aiosqlite for zero-config persistence, "
            "and Pydantic Settings as the single source of truth.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Centralized Config:</b> Implemented in backend/config.py via pydantic-settings loading .env parameters.<br/>"
            "- <b>FastAPI Application:</b> Root routing, CORS middleware for extension communication, and async startup lifecycles.<br/>"
            "- <b>Design Tokens:</b> Established a zero-framework vanilla frontend standard with strict WCAG AA contrast rules.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 0 CHALLENGE & SOLUTION",
            "Inconsistent environment variable overrides caused test runs to point to unintended database files. "
            "Solution: Configured pydantic-settings with explicit .env precedence and standardized test-isolated SQLite in-memory fixtures.",
        )
    )

    # 3. Phase 1
    story.append(Paragraph("3. Phase 1: Relational Schema & Persistence Models", h1_style))
    story.append(
        Paragraph(
            "Phase 1 delivered SQLAlchemy 2.0 declarative async data models representing the meeting lifecycle, "
            "speaker dialogue, extracted decisions, and user auditing.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Meeting Model:</b> Tracks lifecycle transitions (CREATED to RECORDING to PROCESSING to READY to FAILED) with duration metrics.<br/>"
            "- <b>TranscriptSegment Model:</b> Stores speaker turns (You vs Participant), start/end millisecond offsets, raw text, and language codes.<br/>"
            "- <b>Decision Model:</b> Stores extracted decisions, action items, assignees, deadlines, and status (accepted, rejected, pending).<br/>"
            "- <b>AuditLog Model:</b> Records user actions, IP addresses, and timestamps for governance.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 1 CHALLENGE & SOLUTION",
            "SQLAlchemy 2.0 async sessions threw greenlet errors when serializing lazy-loaded relationship attributes. "
            "Solution: Enforced selectinload on all relationship queries and decoupled API endpoints from database models using strict Pydantic response schemas.",
        )
    )

    # 4. Phase 2
    story.append(Paragraph("4. Phase 2: Audio File Upload & Storage Management", h1_style))
    story.append(
        Paragraph(
            "Phase 2 introduced manual file ingestion via POST /api/meetings/upload to support legacy meeting audio, "
            "file validation, and offline testing workflows.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Payload Verification:</b> Magic-byte binary verification for WAV, MP3, M4A, and WebM audio formats.<br/>"
            "- <b>Storage Safeguards:</b> Strict 500 MB upload ceiling and duration extraction via soundfile and wave.<br/>"
            "- <b>Deterministic Organization:</b> Organized audio artifacts under user-isolated directories.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 2 CHALLENGE & SOLUTION",
            "Operating systems transmitted non-standard MIME headers (e.g. audio/x-wav vs audio/wav). "
            "Solution: Paired MIME header parsing with binary magic-byte inspection, guaranteeing robust upload validation across platforms.",
        )
    )

    # 5. Phase 3
    story.append(Paragraph("5. Phase 3: Manifest V3 Extension & Dual-Channel Live Capture", h1_style))
    story.append(
        Paragraph(
            "Phase 3 accomplished live audio capture directly from Google Meet without meeting bots, "
            "overcoming stringent Manifest V3 sandbox restrictions.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>chrome.tabCapture:</b> Service worker obtains restricted streamId without elevated intrusive permissions.<br/>"
            "- <b>Offscreen Document:</b> Hosts AudioContext and navigator.mediaDevices.getUserMedia for participant tab and user mic.<br/>"
            "- <b>Dual AudioWorklets:</b> PCMRecorderProcessor resamples audio streams to 16 kHz mono signed 16-bit PCM.<br/>"
            "- <b>Live Binary Streaming:</b> Streams 1-second chunks over WebSocket framed with ASCII headers (TAB: and MIC:).",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 3 MAJOR PROBLEM & SOLUTION: TAB MUTING",
            "Capturing tab audio via chrome.tabCapture automatically muted the call in Chrome! The user could record participants but could not hear them. "
            "Solution: In offscreen.js, the participant stream was split and piped into audioCtx.destination. This maintained crystal-clear audio to the user's headphones while concurrently streaming samples to the recording worklet.",
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 3 MAJOR PROBLEM & SOLUTION: WEBSOCKET AUTH & 0.0s DURATION",
            "Unauthenticated WebSocket handshakes failed with HTTP 403, causing the recording to disconnect and persist 0.0 seconds of audio. "
            "Solution: Built the /auth/extension-token endpoint. The popup authenticates the user, obtains a secure stream token, and passes it to the offscreen worker to inject into the WebSocket handshake.",
        )
    )

    # 6. Phase 4
    story.append(Paragraph("6. Phase 4: Pluggable Multi-Provider STT & Normalization", h1_style))
    story.append(
        Paragraph(
            "Phase 4 delivered a multi-provider transcription pipeline capable of handling regional Indian dialects, "
            "Devanagari Hindi, English, and romanized Hinglish code-switching.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Provider Architecture:</b> Pluggable interfaces for faster-whisper (local int8), Google Gemini, Sarvam AI (saaras:v4), and Deepgram.<br/>"
            "- <b>Window Chunking & Channel Merge:</b> 30-second windows with 0.5s overlap; concurrent Tab and Mic processing chronologically merged by start_ms.<br/>"
            "- <b>Hindi Normalization:</b> Unicode NFC normalization, nukta/danda cleanup, and phonetic Hinglish canonical mapping.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 4 MAJOR PROBLEM & SOLUTION: SILENCE HALLUCINATIONS",
            "When users were alone on calls, the participant Tab channel recorded pure digital silence (RMS = 0.00). Gemini STT previously hallucinated 1878 historical text (Vernacular Press Act) on silence! "
            "Solution: Implemented is_silent_wav pre-flight energy gating. The pipeline computes Root-Mean-Square (RMS) amplitude on raw audio buffers; if pure digital silence is detected, transcription is immediately bypassed with zero hallucination.",
        )
    )

    # 7. Phase 5
    story.append(Paragraph("7. Phase 5: Authentication, User Scoping & Enterprise Governance", h1_style))
    story.append(
        Paragraph(
            "Phase 5 established security and privacy compliance across all application layers.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Google OAuth 2.0:</b> Real OAuth authorization with httpOnly=True, samesite='lax' session cookies.<br/>"
            "- <b>Tenant Isolation:</b> Strict user_id enforcement on all SQL queries, meeting viewers, and ChromaDB vector filters.<br/>"
            "- <b>In-Memory Rate Limiting:</b> Sliding-window limiter enforcing a 60 request/min cap without external Redis dependencies.<br/>"
            "- <b>Privacy Wipeout:</b> Single-action endpoint (/api/user/delete-all) that permanently purges all audio, transcripts, and embeddings.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 5 CHALLENGE & SOLUTION",
            "CSRF protection middleware blocked legitimate POST requests from the Chrome extension. "
            "Solution: Configured selective CSRF exemptions for Bearer extension token authenticated endpoints while maintaining cookie protection for browser sessions.",
        )
    )

    # 8. Phase 6
    story.append(Paragraph("8. Phase 6: Post-Processing Intelligence & Structured Decisions", h1_style))
    story.append(
        Paragraph(
            "Phase 6 transformed unstructured speech transcripts into executive summaries and structured decision records.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Glossary Cleaning:</b> Technical domain terms (DecisionAI, ChromaDB, AudioWorklet) corrected in transcripts while preserving raw text.<br/>"
            "- <b>Executive Summary:</b> Concise overview of meeting topics, participant positions, and conclusions.<br/>"
            "- <b>Decision Extraction:</b> Structured classification of commitments into accepted, rejected, and pending states with assignees and timestamps.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 6 CHALLENGE & SOLUTION",
            "The extension popup previously froze at 86% processing and never redirected to the meeting viewer. "
            "Solution: Identified that popup.js was polling the generic meetings list instead of the specific meeting ID. Updated polling to query /api/meetings/{id} directly and automatically redirect when status reached 'ready'.",
        )
    )

    # 9. Phase 7
    story.append(Paragraph("9. Phase 7: Responsive Vanilla Frontend & Bento Grid Dashboard", h1_style))
    story.append(
        Paragraph(
            "Phase 7 constructed a zero-framework vanilla web frontend (/web) designed for high performance and accessibility.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Bento Grid Dashboard:</b> Modular CSS Grid layout organizing recent meetings, quick metrics, decisions, and chat entry.<br/>"
            "- <b>WCAG AA Compliance:</b> Contrast ratios strictly exceed 5.0:1 across dark and light palettes.<br/>"
            "- <b>Dual Audio Player:</b> Interactive waveform seeking with automatic transcript dialogue highlighting.<br/>"
            "- <b>Copy Refactor:</b> Replaced internal engineering jargon with intuitive executive copy across web and extension.",
            bullet_style,
        )
    )

    # 10. Phase 8
    story.append(Paragraph("10. Phase 8: Hybrid RAG, Thought-Partner Advisory AI & Resilience", h1_style))
    story.append(
        Paragraph(
            "Phase 8 delivered the conversational intelligence engine, transforming DecisionAI from a storage box "
            "into an active, intelligent thought partner.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "- <b>Speaker-Turn Chunking:</b> Chunks of 300 to 500 tokens respecting conversational boundaries.<br/>"
            "- <b>ChromaDB & BM25 Hybrid Search:</b> Fused using Reciprocal Rank Fusion (RRF with k=60) for balanced dense and lexical retrieval.<br/>"
            "- <b>Grounded Citations:</b> Cites exact meeting titles, dates, and MM:SS timestamps with interactive audio seeking.",
            bullet_style,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 8 MAJOR PROBLEM & SOLUTION: THOUGHT-PARTNER ADVISORY MODE",
            "When users asked consultative or value-add questions (e.g. 'do u think we can add anything else valuable to it'), the assistant refused with: 'No relevant meeting discussions found...'. The AI was acting like a rigid keyword search tool rather than an intelligent partner. "
            "Solution: Introduced is_advisory_query classification, separating factual recall queries (grounded strictly in meeting records) from advisory queries (which synthesize meeting context and apply AI intellect and reasoning to deliver concrete architectural recommendations). Updated offline fallbacks to synthesize domain-grounded suggestions rather than quitting.",
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        make_callout(
            "PHASE 8 MAJOR PROBLEM & SOLUTION: MULTI-MODEL QUOTA RESILIENCE",
            "Gemini free-tier experienced intermittent HTTP 429 quota limits and HTTP 503 high-demand errors, causing RAG queries to fail unexpectedly. "
            "Solution: Implemented tiered candidate model cascading across gemini-3.5-flash-lite, gemini-flash-lite-latest, gemini-3.1-flash-lite, and gemini-3.5-flash with exponential backoff retries and dynamic temperature adjustment (0.4 for advisory, 0.2 for factual recall).",
        )
    )

    # 11. Milestone Summary Table
    story.append(Paragraph("11. Complete Journey Milestone Matrix", h1_style))

    table_data = [
        [
            Paragraph("<b>Phase</b>", body_style),
            Paragraph("<b>Milestone / Scope</b>", body_style),
            Paragraph("<b>Key Problem Encountered</b>", body_style),
            Paragraph("<b>Technical Solution</b>", body_style),
        ]
    ]

    milestones = [
        ("Phase 0", "Project Skeleton & Config", "Inconsistent config paths across OS environments", "Pydantic Settings with single source of truth"),
        ("Phase 1", "Database Schema & CRUD", "SQLAlchemy async greenlet spawning on lazy relations", "Explicit selectinload and dedicated Pydantic DTOs"),
        ("Phase 2", "Audio Upload & Storage", "MIME type variations across operating systems", "Magic-byte binary inspection and size validation"),
        ("Phase 3", "Chrome MV3 Live Capture", "Tab capture automatically muted browser call audio", "Split stream to audioCtx.destination in offscreen"),
        ("Phase 4", "Pluggable STT Pipeline", "Gemini STT hallucinated history text on silent calls", "Root-Mean-Square (RMS) pre-flight silence gating"),
        ("Phase 5", "Auth & Data Governance", "CSRF middleware blocked Chrome extension requests", "Exempted Bearer token authenticated extension routes"),
        ("Phase 6", "Intelligence Extraction", "Popup UI froze at 86% during meeting processing", "Targeted meeting ID polling and automated browser redirect"),
        ("Phase 7", "Vanilla Web Dashboard", "Technical jargon confused executive end users", "Comprehensive copy refactor across UI and extension"),
        ("Phase 8", "Thought-Partner RAG", "Assistant refused consultative queries as 'not found'", "Added is_advisory_query, thought-partner prompt, 429 backoff"),
    ]

    for p, s, pr, sol in milestones:
        table_data.append(
            [
                Paragraph(f"<b>{p}</b>", bullet_style),
                Paragraph(s, bullet_style),
                Paragraph(pr, bullet_style),
                Paragraph(sol, bullet_style),
            ]
        )

    t_summary = Table(table_data, colWidths=[0.8 * inch, 1.8 * inch, 2.1 * inch, 2.3 * inch])
    t_summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F7FAFC"), colors.white]),
            ]
        )
    )
    story.append(t_summary)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully built at: {pdf_path}")


if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "DecisionAI_Project_Journey.pdf"
    build_pdf(out_file)
