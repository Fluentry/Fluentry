"""Parakeet, Nemotron and Cohere transcription through ONNX Runtime.

These checkpoints are published as ONNX exports, so they run through
`onnxruntime` — which has CPU wheels everywhere and optional CUDA and
ROCm execution providers.

Two runtimes can drive them, and they want different file layouts:

* **onnx-asr** reads the NVIDIA exports this app already points at (a fused
  `decoder_joint` model and a `vocab.txt`) and is what Parakeet uses.
* **sherpa-onnx** wants its own export layout — separate decoder and joiner
  plus a `tokens.txt` — so it is used only for models published that way.

Whichever runs, the weights are fetched from Hugging Face into the app's own
cache rather than the user's global one, so uninstalling Fluentry takes
its models with it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from ...i18n import tr
from ...persistence.defaults import cache_home
from ...persistence.speech_model import SpeechModel
from .base import ModelNotReadyError, TranscriptionProviderError, TranscriptionResult

TARGET_SAMPLE_RATE = 16_000


def onnx_cache_root() -> Path:
    return cache_home() / "models" / "onnx"


def onnx_model_directory(model: SpeechModel) -> Path:
    return onnx_cache_root() / model.value


#: The onnx-asr *architecture* each model is an export of. Both Parakeet
#: builds are NeMo conformer transducers with token-duration heads.
ONNX_ASR_MODEL_TYPES = {
    SpeechModel.PARAKEET_TDT: "nemo-conformer-tdt",
    SpeechModel.PARAKEET_TDT_V2: "nemo-conformer-tdt",
}

#: int8 everywhere: it is what the published download sizes describe, and on
#: a CPU it is both smaller and faster with no accuracy loss worth the bytes.
ONNX_ASR_QUANTIZATION = "int8"


@dataclass
class OnnxAsrProvider:
    """Parakeet through `onnx-asr`.

    `onnx-asr` reads the NVIDIA ONNX exports directly, so this is the path
    that actually works for the Parakeet repositories the model table names.
    """

    model: SpeechModel
    cache_root: Path = field(default=None)  # type: ignore[assignment]
    _recognizer: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.cache_root is None:
            self.cache_root = onnx_cache_root()

    @property
    def model_id(self) -> str:
        return self.model.value

    @staticmethod
    def is_available() -> bool:
        try:
            import onnx_asr  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def supports(model: SpeechModel) -> bool:
        return model in ONNX_ASR_MODEL_TYPES and model.onnx_repository is not None

    @property
    def repository(self) -> str:
        repository = self.model.onnx_repository
        if repository is None:
            raise TranscriptionProviderError(f"{self.model.display_name} has no ONNX export.")
        return repository

    #: Only what int8 inference reads. The float32 and fp16 copies in these
    #: repositories would quadruple the download for no CPU benefit.
    SNAPSHOT_PATTERNS = ["config.json", "vocab.txt", "tokens.txt", "*.int8.onnx"]

    def _snapshot(self, local_files_only: bool):
        from huggingface_hub import snapshot_download

        return Path(
            snapshot_download(
                repo_id=self.repository,
                cache_dir=str(self.cache_root),
                local_files_only=local_files_only,
                allow_patterns=self.SNAPSHOT_PATTERNS,
            )
        )

    @property
    def is_downloaded(self) -> bool:
        try:
            return self._snapshot(local_files_only=True).is_dir()
        except Exception:
            return False

    @property
    def is_ready(self) -> bool:
        return self._recognizer is not None

    def download(self) -> Path:
        """Fetch the weights. Safe to call when they are already here."""
        try:
            return self._snapshot(local_files_only=False)
        except Exception as error:
            raise ModelDownloadFailed(self.model, error) from error

    def prepare(self) -> None:
        if self._recognizer is not None:
            return
        if not self.is_available():
            raise TranscriptionProviderError(
                f"{self.model.display_name} needs the onnx-asr runtime. "
                "Install it with: pip install onnx-asr"
            )
        import onnx_asr

        path = self.download()
        self._recognizer = onnx_asr.load_model(
            ONNX_ASR_MODEL_TYPES[self.model],
            path=str(path),
            quantization=ONNX_ASR_QUANTIZATION,
        )

        # A runtime is only trusted after it transcribes a known recording.
        # Ubuntu 26.04's python3-onnxruntime loads this model cleanly and
        # then returns an empty string for everything - no exception, no
        # log, nothing. Every dictation "worked" and produced no text.
        from ..runtime_verification import verify

        if not verify(self):
            self._recognizer = None
            raise TranscriptionProviderError(
                "The installed onnxruntime loads the model but transcribes "
                "nothing - the distribution's build of it is faulty. "
                "Fluentry can install a working runtime from the Voice "
                "Engine screen."
            )

    def transcribe(
        self, samples: Sequence[float], language: str | None = None
    ) -> TranscriptionResult:
        if self._recognizer is None:
            self.prepare()
        started = time.monotonic()
        audio = np.asarray(samples, dtype=np.float32)
        text = self._recognizer.recognize(  # type: ignore[union-attr]
            audio, sample_rate=TARGET_SAMPLE_RATE
        )
        return TranscriptionResult(
            text=(text or "").strip(),
            duration_milliseconds=int((time.monotonic() - started) * 1000),
            language=language,
        )

    def release(self) -> None:
        self._recognizer = None


class ModelDownloadFailed(TranscriptionProviderError):
    def __init__(self, model: SpeechModel, cause: BaseException) -> None:
        super().__init__(f"Could not download {model.display_name}: {cause}")
        self.model = model
        self.cause = cause


@dataclass
class SherpaOnnxProvider:
    """Transducer decoding via `sherpa-onnx`."""

    model: SpeechModel
    directory: Path = field(default=None)  # type: ignore[assignment]
    _recognizer: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.directory is None:
            self.directory = onnx_model_directory(self.model)

    @property
    def model_id(self) -> str:
        return self.model.value

    @staticmethod
    def is_available() -> bool:
        try:
            import sherpa_onnx  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def is_downloaded(self) -> bool:
        directory = Path(self.directory)
        if not directory.is_dir():
            return False
        return any(directory.glob("*.onnx"))

    @property
    def is_ready(self) -> bool:
        return self._recognizer is not None

    def _component(self, *names: str) -> Path:
        directory = Path(self.directory)
        for name in names:
            matches = sorted(directory.glob(f"*{name}*.onnx"))
            if matches:
                return matches[0]
        raise ModelNotReadyError(self.model.value)

    def prepare(self) -> None:
        if self._recognizer is not None:
            return
        if not self.is_available():
            raise TranscriptionProviderError(
                "sherpa-onnx is not installed. Install it with: pip install sherpa-onnx"
            )
        if not self.is_downloaded:
            raise ModelNotReadyError(self.model.value)

        import sherpa_onnx

        tokens = Path(self.directory) / "tokens.txt"
        if not tokens.exists():
            raise ModelNotReadyError(self.model.value)

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(self._component("encoder")),
            decoder=str(self._component("decoder")),
            joiner=str(self._component("joiner")),
            tokens=str(tokens),
            num_threads=2,
            sample_rate=TARGET_SAMPLE_RATE,
            feature_dim=80,
        )

    def transcribe(
        self, samples: Sequence[float], language: str | None = None
    ) -> TranscriptionResult:
        if self._recognizer is None:
            self.prepare()
        started = time.monotonic()
        stream = self._recognizer.create_stream()  # type: ignore[union-attr]
        stream.accept_waveform(TARGET_SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
        self._recognizer.decode_stream(stream)  # type: ignore[union-attr]
        text = (stream.result.text or "").strip()
        return TranscriptionResult(
            text=text,
            duration_milliseconds=int((time.monotonic() - started) * 1000),
            language=language,
        )

    def release(self) -> None:
        self._recognizer = None


@dataclass
class UnavailableOnnxProvider:
    """Stands in when no ONNX runtime is installed.

    It reports clearly rather than failing at dictation time, so the Voice
    Engine screen can tell the user exactly what to install.
    """

    model: SpeechModel

    @property
    def model_id(self) -> str:
        return self.model.value

    @property
    def is_downloaded(self) -> bool:
        return False

    @property
    def is_ready(self) -> bool:
        return False

    def prepare(self) -> None:
        raise TranscriptionProviderError(missing_runtime_message(self.model))

    def transcribe(
        self, samples: Sequence[float], language: str | None = None
    ) -> TranscriptionResult:
        self.prepare()
        raise AssertionError("unreachable")

    def release(self) -> None:
        return None


def missing_runtime_message(model: SpeechModel) -> str:
    """What to install for this model, naming the runtime it actually needs."""
    if model in ONNX_ASR_MODEL_TYPES:
        package = "onnx-asr"
    else:
        package = "sherpa-onnx"
    return tr(
        "{model} needs the {package} runtime. Install it with: pip install "
        "{package} — or choose a Whisper model, which works out of the box."
    ).format(model=model.display_name, package=package)


def engine_is_installable(model: SpeechModel) -> bool:
    """Whether this machine has a runtime that can actually run the model.

    False means no amount of downloading will help — a package has to be
    installed first — so the UI can say that up front instead of letting the
    user pick an option that can only fail.
    """
    from ...persistence.speech_model import SpeechBackend

    if model.backend is SpeechBackend.WHISPER_CPP:
        from .whisper import whisper_runtime_is_available

        return whisper_runtime_is_available()
    if model.backend is not SpeechBackend.ONNX:
        return False
    if OnnxAsrProvider.supports(model):
        return OnnxAsrProvider.is_available()
    return SherpaOnnxProvider.is_available()


def make_onnx_provider(model: SpeechModel):
    """The runtime that can actually read this model's published export."""
    if OnnxAsrProvider.supports(model):
        if OnnxAsrProvider.is_available():
            return OnnxAsrProvider(model=model)
        return UnavailableOnnxProvider(model=model)
    if SherpaOnnxProvider.is_available():
        return SherpaOnnxProvider(model=model)
    return UnavailableOnnxProvider(model=model)
