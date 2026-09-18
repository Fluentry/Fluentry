"""Transcription history entry and its audio metadata."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .settings_types import iso, now, parse_iso


@dataclass(frozen=True)
class DictationAudioMetadata:
    file_name: str
    duration_milliseconds: int
    byte_count: int
    sample_rate: int
    channels: int
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "durationMilliseconds": self.duration_milliseconds,
            "byteCount": self.byte_count,
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "model": self.model,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "DictationAudioMetadata":
        return DictationAudioMetadata(
            file_name=str(payload["fileName"]),
            duration_milliseconds=int(payload.get("durationMilliseconds") or 0),
            byte_count=int(payload.get("byteCount") or 0),
            sample_rate=int(payload.get("sampleRate") or 0),
            channels=int(payload.get("channels") or 0),
            model=payload.get("model"),
        )


@dataclass(frozen=True)
class TranscriptionHistoryEntry:
    raw_text: str
    processed_text: str
    app_name: str
    window_title: str
    was_ai_processed: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    timestamp: datetime = field(default_factory=now)
    character_count: int | None = None
    processing_model: str | None = None
    transcription_duration_milliseconds: int | None = None
    ai_processing_duration_milliseconds: int | None = None
    ai_tokens_per_second: float | None = None
    #: Set when AI post-processing was configured but failed and the raw
    #: transcription was typed instead; carries the error for display.
    ai_processing_error: str | None = None
    audio: DictationAudioMetadata | None = None

    def __post_init__(self) -> None:
        if self.character_count is None:
            object.__setattr__(self, "character_count", len(self.processed_text))

    # --- presentation -----------------------------------------------------

    @property
    def preview_text(self) -> str:
        """First 80 characters, for list rows."""
        text = self.processed_text.strip()
        if len(text) > 80:
            return text[:77] + "..."
        return text

    @property
    def clipboard_text(self) -> str | None:
        """Processed text when present, otherwise the raw transcription."""
        processed = self.processed_text.strip()
        raw = self.raw_text.strip()
        text = raw if not processed else processed
        return text or None

    @property
    def has_audio_metadata(self) -> bool:
        return self.audio is not None

    @staticmethod
    def formatted_duration(milliseconds: int) -> str:
        if milliseconds < 1000:
            return f"{milliseconds} ms"
        return f"{milliseconds / 1000:.1f} s"

    @staticmethod
    def formatted_tokens_per_second(tokens_per_second: float, compact: bool = False) -> str:
        if tokens_per_second >= 100:
            rounded = str(int(round(tokens_per_second)))
        else:
            rounded = f"{tokens_per_second:.1f}"
        return f"{rounded} tok/s" if compact else f"{rounded} tokens/sec"

    def replacing_audio(self, audio: DictationAudioMetadata | None) -> "TranscriptionHistoryEntry":
        return TranscriptionHistoryEntry(
            id=self.id,
            timestamp=self.timestamp,
            raw_text=self.raw_text,
            processed_text=self.processed_text,
            app_name=self.app_name,
            window_title=self.window_title,
            character_count=self.character_count,
            was_ai_processed=self.was_ai_processed,
            processing_model=self.processing_model,
            transcription_duration_milliseconds=self.transcription_duration_milliseconds,
            ai_processing_duration_milliseconds=self.ai_processing_duration_milliseconds,
            ai_tokens_per_second=self.ai_tokens_per_second,
            ai_processing_error=self.ai_processing_error,
            audio=audio,
        )

    # --- coding -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": iso(self.timestamp),
            "rawText": self.raw_text,
            "processedText": self.processed_text,
            "appName": self.app_name,
            "windowTitle": self.window_title,
            "characterCount": self.character_count,
            "wasAIProcessed": self.was_ai_processed,
            "processingModel": self.processing_model,
            "transcriptionDurationMilliseconds": self.transcription_duration_milliseconds,
            "aiProcessingDurationMilliseconds": self.ai_processing_duration_milliseconds,
            "aiTokensPerSecond": self.ai_tokens_per_second,
            "aiProcessingError": self.ai_processing_error,
            "audio": self.audio.to_dict() if self.audio else None,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "TranscriptionHistoryEntry":
        audio = payload.get("audio")
        return TranscriptionHistoryEntry(
            id=str(payload["id"]),
            timestamp=parse_iso(payload["timestamp"]),
            raw_text=str(payload["rawText"]),
            processed_text=str(payload["processedText"]),
            app_name=str(payload["appName"]),
            window_title=str(payload["windowTitle"]),
            character_count=int(payload["characterCount"]),
            was_ai_processed=bool(payload["wasAIProcessed"]),
            processing_model=payload.get("processingModel"),
            transcription_duration_milliseconds=payload.get("transcriptionDurationMilliseconds"),
            ai_processing_duration_milliseconds=payload.get("aiProcessingDurationMilliseconds"),
            ai_tokens_per_second=payload.get("aiTokensPerSecond"),
            ai_processing_error=payload.get("aiProcessingError"),
            audio=DictationAudioMetadata.from_dict(audio) if isinstance(audio, dict) else None,
        )


@dataclass(frozen=True)
class TodaySummary:
    words: int
    transcriptions: int
