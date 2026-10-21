"""
Base interface and data models for Speech-to-Text (STT) transcription providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TranscriptionSegment:
    """A single aligned utterance segment returned by an STT provider."""

    speaker: str
    start_ms: int
    end_ms: int
    text: str
    raw_text: str = ""
    language: str | None = None

    def __post_init__(self):
        if not self.raw_text:
            self.raw_text = self.text

    @property
    def start_time(self) -> float:
        """Start time in seconds for backward compatibility."""
        return self.start_ms / 1000.0

    @property
    def end_time(self) -> float:
        """End time in seconds for backward compatibility."""
        return self.end_ms / 1000.0

    def to_dict(self) -> dict:
        return {
            "speaker": self.speaker,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
            "raw_text": self.raw_text,
            "language": self.language,
        }


@dataclass
class TranscriptionResult:
    """Complete transcription output for an audio track or file."""

    segments: list[TranscriptionSegment] = field(default_factory=list)
    duration_seconds: float = 0.0
    language: str | None = None
    provider_name: str = ""

    @property
    def text(self) -> str:
        """Concatenated text of all segments."""
        return " ".join(s.text for s in self.segments if s.text).strip()


class TranscriptionProvider(ABC):
    """
    Abstract interface for speech-to-text providers.
    Implementations must support audio file transcription, speaker labeling,
    language hints, and domain vocabulary / glossary hints.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'faster_whisper', 'gemini', 'sarvam', 'deepgram')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider dependencies and credentials are configured."""
        pass

    @abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        speaker: str = "speaker",
        language: str | None = None,
        glossary: list[str] | None = None,
        chunk_offset_ms: int = 0,
    ) -> TranscriptionResult:
        """
        Transcribe the given audio file.

        Args:
            audio_path: Path to mono 16 kHz WAV audio file.
            speaker: Speaker identifier label (e.g. 'Participants (Tab)' or 'You (Mic)').
            language: Optional language hint (e.g. 'hi', 'en', 'hi-IN').
            glossary: List of custom proper nouns, technical terms, or names to bias recognition.
            chunk_offset_ms: Offset in milliseconds to add to timestamps when chunking.

        Returns:
            TranscriptionResult with list of TranscriptionSegment objects.
        """
        pass
