"""
Transcript chunking module for RAG retrieval.
Chunks transcripts by speaker turns and time windows (300-500 tokens).
Strict constraints: No emojis, no em dashes.
"""

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass
class TranscriptChunk:
    chunk_id: str
    meeting_id: int
    user_id: int
    meeting_title: str
    date: str
    speakers: str
    start_ms: int
    end_ms: int
    chunk_index: int
    text: str
    token_count: int
    speaker_turns: list[dict[str, Any]] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for multilingual English, Hindi, and Hinglish text.
    Standard heuristic: ~1.3 tokens per word or ~4 characters per token.
    """
    if not text:
        return 0
    words = text.split()
    word_tokens = int(len(words) * 1.33)
    char_tokens = int(len(text) / 3.8)
    return max(1, max(word_tokens, char_tokens))


def format_timestamp_ms(ms: int) -> str:
    """Format milliseconds into MM:SS format."""
    total_seconds = max(0, int(ms / 1000))
    mins = total_seconds // 60
    secs = total_seconds % 60
    return f"{mins:02d}:{secs:02d}"


def chunk_transcript_segments(
    segments: Sequence,
    meeting_id: int,
    user_id: int,
    meeting_title: str,
    meeting_date: str,
    min_tokens: int = 300,
    max_tokens: int = 500,
) -> list[TranscriptChunk]:
    """
    Chunk transcript segments by speaker turns and time windows into 300-500 token chunks.

    Each segment is expected to have:
      - speaker: str
      - text: str (or raw_text)
      - start_ms: int
      - end_ms: int
    """
    if not segments:
        return []

    chunks: list[TranscriptChunk] = []
    current_lines: list[str] = []
    current_speakers: set[str] = set()
    current_turns: list[dict[str, Any]] = []
    current_start_ms: int = getattr(segments[0], "start_ms", 0)
    current_end_ms: int = getattr(segments[0], "end_ms", 0)
    current_tokens: int = 0
    chunk_index: int = 0

    def finalize_chunk():
        nonlocal current_lines, current_speakers, current_turns, current_start_ms, current_end_ms
        nonlocal current_tokens, chunk_index
        if not current_lines:
            return

        combined_text = "\n".join(current_lines).strip()
        speakers_str = ", ".join(sorted(current_speakers)) or "Unknown"

        chunk = TranscriptChunk(
            chunk_id=f"{meeting_id}_{chunk_index}",
            meeting_id=meeting_id,
            user_id=user_id,
            meeting_title=meeting_title or f"Meeting #{meeting_id}",
            date=meeting_date or "",
            speakers=speakers_str,
            start_ms=current_start_ms,
            end_ms=current_end_ms,
            chunk_index=chunk_index,
            text=combined_text,
            token_count=current_tokens,
            speaker_turns=list(current_turns),
        )
        chunks.append(chunk)
        chunk_index += 1

        # Reset accumulator
        current_lines = []
        current_speakers = set()
        current_turns = []
        current_tokens = 0

    for seg in segments:
        speaker = getattr(seg, "speaker", "Speaker")
        text = getattr(seg, "text", "") or getattr(seg, "raw_text", "")
        text = text.strip()
        if not text:
            continue

        start_ms = getattr(seg, "start_ms", 0)
        end_ms = getattr(seg, "end_ms", start_ms + 1000)
        time_tag = format_timestamp_ms(start_ms)
        line = f"[{time_tag}] {speaker}: {text}"
        line_tokens = estimate_tokens(line)

        # Check if single line is excessively long (> max_tokens)
        if line_tokens > max_tokens:
            # Finalize whatever was accumulated first
            if current_lines:
                finalize_chunk()

            # Split large segment into smaller pieces by sentences
            words = text.split(". ")
            sub_lines: list[str] = []
            sub_turns: list[dict[str, Any]] = []
            sub_tokens: int = 0
            sub_start = start_ms

            for word_part in words:
                part_line = f"[{time_tag}] {speaker}: {word_part}"
                p_tokens = estimate_tokens(part_line)
                if sub_tokens + p_tokens > max_tokens and sub_lines:
                    # Emit sub chunk
                    current_lines = sub_lines
                    current_speakers = {speaker}
                    current_turns = sub_turns
                    current_start_ms = sub_start
                    current_end_ms = end_ms
                    current_tokens = sub_tokens
                    finalize_chunk()
                    sub_lines = []
                    sub_turns = []
                    sub_tokens = 0
                    sub_start = start_ms
                sub_lines.append(part_line)
                sub_turns.append({"speaker": speaker, "time": time_tag, "text": word_part, "start_ms": start_ms, "end_ms": end_ms})
                sub_tokens += p_tokens

            if sub_lines:
                current_lines = sub_lines
                current_speakers = {speaker}
                current_turns = sub_turns
                current_start_ms = sub_start
                current_end_ms = end_ms
                current_tokens = sub_tokens
                finalize_chunk()

            continue

        # Check time window gap (e.g. > 45 seconds gap is a natural topic transition)
        time_gap_large = (
            current_lines and (start_ms - current_end_ms > 45000) and current_tokens >= min_tokens
        )

        # If adding this line would exceed max_tokens, or we have a large gap with enough tokens
        if (current_tokens + line_tokens > max_tokens) or time_gap_large:
            finalize_chunk()
            current_start_ms = start_ms

        if not current_lines:
            current_start_ms = start_ms

        current_lines.append(line)
        current_speakers.add(speaker)
        current_turns.append({"speaker": speaker, "time": time_tag, "text": text, "start_ms": start_ms, "end_ms": end_ms})
        current_end_ms = end_ms
        current_tokens += line_tokens

    # Finalize any remainder
    if current_lines:
        finalize_chunk()

    return chunks
