"""Speaker-labelled transcription of recorded files.

Ports `SpeakerDiarizationService.mergeAdjacentTurns` and
`SpeakerLabeledTranscriptionPolicy`. The diarizer itself was CoreML on macOS;
on Linux it is an ONNX segmentation + embedding pipeline, but every decision
*about* its output is the portable part and lives here:

* adjacent turns from one speaker merge across short pauses, without ever
  shrinking the covered time range or exceeding the chunk ceiling,
* a speaker-labelled transcript is only kept when it did not silently drop
  meaningful audio — otherwise the plain full-file transcript wins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

DEFAULT_MAX_GAP_SECONDS = 1.0
#: Mirrors the 20-minute chunk ceiling used by file transcription.
DEFAULT_MAX_TURN_SECONDS = 20 * 60

#: A single unlabelled stretch longer than this rejects the labelled transcript.
MAXIMUM_SINGLE_GAP_SECONDS = 5.0
#: At most 1% of the audio may be missing, capped at 30 seconds overall.
MAXIMUM_SKIPPED_RATIO = 0.01
MAXIMUM_SKIPPED_SECONDS = 30.0


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_label: str
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def merge_adjacent_turns(
    turns: Sequence[SpeakerTurn],
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    max_turn_seconds: float = DEFAULT_MAX_TURN_SECONDS,
) -> list[SpeakerTurn]:
    """Join consecutive same-speaker turns across short pauses."""
    if not turns:
        return []

    current = turns[0]
    merged: list[SpeakerTurn] = []
    for turn in turns[1:]:
        gap = turn.start_seconds - current.end_seconds
        combined_duration = turn.end_seconds - current.start_seconds
        if (
            turn.speaker_label == current.speaker_label
            and gap <= max_gap_seconds
            and combined_duration <= max_turn_seconds
        ):
            # Diarization segments can overlap at boundaries, so a later
            # same-speaker turn may end *before* the accumulated one. Taking
            # its end directly would drop the trailing audio.
            current = SpeakerTurn(
                speaker_label=current.speaker_label,
                start_seconds=current.start_seconds,
                end_seconds=max(current.end_seconds, turn.end_seconds),
            )
        else:
            merged.append(current)
            current = turn
    merged.append(current)
    return merged


def assign_chronological_labels(segments: Sequence[tuple[str, float, float]]) -> list[SpeakerTurn]:
    """Label clusters in order of first appearance, so "Speaker 1" spoke first."""
    chronological = sorted(segments, key=lambda segment: segment[1])
    labels: dict[str, str] = {}
    turns: list[SpeakerTurn] = []
    for cluster_id, start, end in chronological:
        label = labels.get(cluster_id)
        if label is None:
            label = f"Speaker {len(labels) + 1}"
            labels[cluster_id] = label
        turns.append(SpeakerTurn(speaker_label=label, start_seconds=start, end_seconds=end))
    return merge_adjacent_turns(turns)


# --- transcript assembly ----------------------------------------------------


def _timestamp(seconds: float) -> str:
    total = int(math.floor(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remaining = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{remaining:02d}"
    return f"{minutes}:{remaining:02d}"


@dataclass(frozen=True)
class SpeakerTranscriptSegment:
    speaker: str
    start_seconds: float
    end_seconds: float
    text: str

    @property
    def id(self) -> str:
        return f"{self.speaker}-{self.start_seconds}"

    @property
    def timestamp_text(self) -> str:
        return _timestamp(self.start_seconds)

    @property
    def plain_text(self) -> str:
        return f"[{self.timestamp_text}] {self.speaker}: {self.text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "startSeconds": self.start_seconds,
            "endSeconds": self.end_seconds,
            "text": self.text,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SpeakerTranscriptSegment":
        return SpeakerTranscriptSegment(
            speaker=str(payload["speaker"]),
            start_seconds=float(payload["startSeconds"]),
            end_seconds=float(payload["endSeconds"]),
            text=str(payload["text"]),
        )


@dataclass(frozen=True)
class SpeakerTranscriptGap:
    """An interval for which speaker-attributed ASR produced no usable text."""

    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def timestamp_range_text(self) -> str:
        return f"{self._stamp(self.start_seconds)}-{self._stamp(self.end_seconds)}"

    @staticmethod
    def _stamp(seconds: float) -> str:
        safe = max(0.0, seconds)
        whole_minutes = int(safe) // 60
        remaining = safe - whole_minutes * 60
        return f"{whole_minutes}:{remaining:04.1f}"

    def to_dict(self) -> dict[str, Any]:
        return {"startSeconds": self.start_seconds, "endSeconds": self.end_seconds}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SpeakerTranscriptGap":
        return SpeakerTranscriptGap(
            start_seconds=float(payload["startSeconds"]), end_seconds=float(payload["endSeconds"])
        )


@dataclass(frozen=True)
class SpeakerChunkTranscription:
    text: str
    confidence: float


@dataclass(frozen=True)
class SpeakerTurnTranscription:
    text: str
    confidence: float
    gaps: tuple[SpeakerTranscriptGap, ...]


@dataclass(frozen=True)
class SpeakerRecognizedTurn:
    speaker: str
    start_seconds: float
    end_seconds: float
    transcription: SpeakerTurnTranscription


@dataclass(frozen=True)
class SpeakerLabeledTranscript:
    segments: tuple[SpeakerTranscriptSegment, ...]
    confidence: float
    gaps: tuple[SpeakerTranscriptGap, ...]
    notice: str | None


@dataclass(frozen=True)
class SpeakerLabelingCoverage:
    gap_count: int
    skipped_duration_seconds: float
    max_gap_duration_seconds: float
    diarized_duration_seconds: float

    @property
    def skipped_ratio(self) -> float:
        if not math.isfinite(self.diarized_duration_seconds) or self.diarized_duration_seconds <= 0:
            return math.inf if self.skipped_duration_seconds > 0 else 0.0
        return self.skipped_duration_seconds / self.diarized_duration_seconds


def transcribe_chunks(
    ranges: Sequence[SpeakerTranscriptGap],
    operation: Callable[[SpeakerTranscriptGap], SpeakerChunkTranscription | None],
) -> SpeakerTurnTranscription:
    """Run one chunk at a time, recording which produced nothing.

    A chunk that yields only whitespace is a gap, not text, and does not drag
    the average confidence down. A provider *error* propagates, so the caller
    can fall back to the plain full-file transcript.
    """
    pieces: list[str] = []
    confidence_sum = 0.0
    gaps: list[SpeakerTranscriptGap] = []

    for chunk_range in ranges:
        result = operation(chunk_range)
        text = (result.text if result else "").strip()
        if not text or result is None:
            gaps.append(chunk_range)
            continue
        pieces.append(text)
        confidence_sum += result.confidence

    confidence = 0.0 if not pieces else confidence_sum / len(pieces)
    return SpeakerTurnTranscription(
        text=" ".join(pieces), confidence=confidence, gaps=tuple(gaps)
    )


def coverage(
    gaps: Sequence[SpeakerTranscriptGap], diarized_duration_seconds: float
) -> SpeakerLabelingCoverage:
    durations = [gap.duration_seconds for gap in gaps]
    return SpeakerLabelingCoverage(
        gap_count=len(gaps),
        skipped_duration_seconds=sum(durations),
        max_gap_duration_seconds=max(durations) if durations else 0.0,
        diarized_duration_seconds=diarized_duration_seconds,
    )


def should_keep_speaker_labels(
    has_recognized_text: bool,
    gaps: Sequence[SpeakerTranscriptGap],
    diarized_duration_seconds: float,
) -> bool:
    """Keep labels only when nothing meaningful was dropped."""
    if not has_recognized_text:
        return False
    if not gaps:
        return True
    if not math.isfinite(diarized_duration_seconds) or diarized_duration_seconds <= 0:
        return False

    measured = coverage(gaps, diarized_duration_seconds)
    if measured.max_gap_duration_seconds > MAXIMUM_SINGLE_GAP_SECONDS:
        return False
    allowed = min(MAXIMUM_SKIPPED_SECONDS, diarized_duration_seconds * MAXIMUM_SKIPPED_RATIO)
    return measured.skipped_duration_seconds <= allowed


def fallback_diagnostic(
    has_recognized_text: bool,
    gaps: Sequence[SpeakerTranscriptGap],
    diarized_duration_seconds: float,
) -> str:
    """Says why the labelled transcript was rejected, in reviewable numbers."""
    if not has_recognized_text:
        return "Speaker labeling produced no recognized text"
    if not math.isfinite(diarized_duration_seconds) or diarized_duration_seconds <= 0:
        return "Speaker labeling produced an invalid diarized duration"
    measured = coverage(gaps, diarized_duration_seconds)
    return (
        "Speaker labeling omitted too much audio "
        f"(gaps={measured.gap_count}, skipped={measured.skipped_duration_seconds:.3f}s, "
        f"maxGap={measured.max_gap_duration_seconds:.3f}s, "
        f"diarized={measured.diarized_duration_seconds:.3f}s, "
        f"ratio={measured.skipped_ratio:.4f})"
    )


def limitation_notice(gaps: Sequence[SpeakerTranscriptGap]) -> str | None:
    if not gaps:
        return None
    noun = "section" if len(gaps) == 1 else "sections"
    duration = sum(gap.duration_seconds for gap in gaps)
    return (
        f"Speaker labels were kept, but {len(gaps)} short audio {noun} "
        f"totaling {duration:.1f} seconds produced no text."
    )


def assemble_turns(turns: Sequence[SpeakerRecognizedTurn]) -> SpeakerLabeledTranscript | None:
    gaps = [gap for turn in turns for gap in turn.transcription.gaps]
    recognized = [turn for turn in turns if turn.transcription.text.strip()]
    diarized_duration = sum(max(0.0, turn.end_seconds - turn.start_seconds) for turn in turns)

    if not should_keep_speaker_labels(bool(recognized), gaps, diarized_duration):
        return None

    segments = tuple(
        SpeakerTranscriptSegment(
            speaker=turn.speaker,
            start_seconds=turn.start_seconds,
            end_seconds=turn.end_seconds,
            text=turn.transcription.text,
        )
        for turn in recognized
    )
    confidence = sum(turn.transcription.confidence for turn in recognized) / len(recognized)
    return SpeakerLabeledTranscript(
        segments=segments,
        confidence=confidence,
        gaps=tuple(gaps),
        notice=limitation_notice(gaps),
    )
