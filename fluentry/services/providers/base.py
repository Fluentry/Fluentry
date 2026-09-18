"""Speech-recognition provider interface.

`TranscriptionProvider`, unchanged in shape. Providers receive 16 kHz mono
float samples and return text; streaming providers additionally emit partial
results while the user is still speaking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    #: Wall-clock time the provider spent, for the history performance row.
    duration_milliseconds: int = 0
    language: str | None = None
    is_partial: bool = False


class TranscriptionProviderError(Exception):
    pass


class ModelNotReadyError(TranscriptionProviderError):
    def __init__(self, model_id: str) -> None:
        super().__init__(f"The speech model '{model_id}' is not downloaded yet.")
        self.model_id = model_id


class TranscriptionProvider(Protocol):
    """Every backend implements this; the ASR service knows nothing else."""

    model_id: str

    @property
    def is_ready(self) -> bool: ...

    def prepare(self) -> None: ...

    def transcribe(self, samples: Sequence[float], language: str | None = None) -> TranscriptionResult: ...

    def release(self) -> None: ...


class StreamingTranscriptionProvider(TranscriptionProvider, Protocol):
    """A provider that can produce a preview before the recording ends."""

    def begin_stream(self, on_partial: Callable[[str], None]) -> None: ...

    def feed(self, samples: Sequence[float]) -> None: ...

    def finish_stream(self, language: str | None = None) -> TranscriptionResult: ...


@dataclass
class ScriptedTranscriptionProvider:
    """Deterministic provider used by tests and the onboarding tryout.

    Returns queued responses in order, so a whole dictation round-trip can be
    exercised without a model or a microphone.
    """

    model_id: str = "scripted"
    responses: list[str] = field(default_factory=list)
    partials: list[str] = field(default_factory=list)
    prepared: bool = False
    released: bool = False
    received_samples: list[float] = field(default_factory=list)
    requested_languages: list[str | None] = field(default_factory=list)
    failure: Exception | None = None

    @property
    def is_ready(self) -> bool:
        return self.prepared

    def prepare(self) -> None:
        self.prepared = True

    def transcribe(self, samples: Sequence[float], language: str | None = None) -> TranscriptionResult:
        if self.failure is not None:
            raise self.failure
        self.received_samples.extend(samples)
        self.requested_languages.append(language)
        text = self.responses.pop(0) if self.responses else ""
        return TranscriptionResult(text=text, duration_milliseconds=1, language=language)

    def release(self) -> None:
        self.released = True

    # --- streaming --------------------------------------------------------

    def begin_stream(self, on_partial: Callable[[str], None]) -> None:
        self._on_partial = on_partial

    def feed(self, samples: Sequence[float]) -> None:
        self.received_samples.extend(samples)
        if self.partials and getattr(self, "_on_partial", None) is not None:
            self._on_partial(self.partials.pop(0))

    def finish_stream(self, language: str | None = None) -> TranscriptionResult:
        if self.failure is not None:
            raise self.failure
        self.requested_languages.append(language)
        text = self.responses.pop(0) if self.responses else ""
        return TranscriptionResult(text=text, duration_milliseconds=1, language=language)
