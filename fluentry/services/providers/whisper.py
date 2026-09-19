"""Whisper transcription.

The
same weights are served by CTranslate2 via `faster-whisper`, which ships
prebuilt wheels for x86-64 and aarch64 and is substantially faster on CPU
than a generic build of whisper.cpp. The model *identities* are unchanged —
`whisper-base` is still OpenAI's base checkpoint — so a user's model choice
means the same thing on both platforms.

A whisper.cpp binary is still honoured when one is installed, for users who
already have GGUF weights on disk.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from ...persistence.defaults import cache_home
from ...persistence.speech_model import SpeechModel
from .base import ModelNotReadyError, TranscriptionProviderError, TranscriptionResult

#: CTranslate2 model names on Hugging Face, keyed by the app's model ids.
FASTER_WHISPER_MODEL_NAMES = {
    SpeechModel.WHISPER_TINY: "tiny",
    SpeechModel.WHISPER_BASE: "base",
    SpeechModel.WHISPER_SMALL: "small",
    SpeechModel.WHISPER_MEDIUM: "medium",
    SpeechModel.WHISPER_LARGE_TURBO: "large-v3-turbo",
    SpeechModel.WHISPER_LARGE: "large-v3",
}


def whisper_model_directory() -> Path:
    return cache_home() / "models" / "whisper"


def write_wav(path: Path, samples: Sequence[float], sample_rate: int = 16_000) -> None:
    """16-bit mono PCM, the format every Whisper runtime accepts."""
    array = np.asarray(samples, dtype=np.float32)
    clipped = np.clip(array, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def read_wav_as_mono_float(path: Path, target_sample_rate: int = 16_000) -> list[float]:
    """Load a WAV as 16 kHz mono float32, resampling and downmixing as needed."""
    from ..audio_buffer_converter import PCMBuffer, mono_samples

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise TranscriptionProviderError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        usable = (data.size // channels) * channels
        data = data[:usable].reshape(-1, channels).T
    else:
        data = data.reshape(1, -1)
    return mono_samples(PCMBuffer(np.ascontiguousarray(data), float(sample_rate)), target_sample_rate)


@dataclass
class FasterWhisperProvider:
    """Default Whisper backend."""

    model: SpeechModel = SpeechModel.WHISPER_BASE
    compute_type: str = "int8"
    device: str = "cpu"
    download_root: Path = field(default_factory=whisper_model_directory)
    beam_size: int = 1
    _engine: object | None = field(default=None, repr=False)

    @property
    def model_id(self) -> str:
        return self.model.value

    @staticmethod
    def is_available() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def is_downloaded(self) -> bool:
        """Whether the weights are already on disk, so `prepare` is offline."""
        name = FASTER_WHISPER_MODEL_NAMES.get(self.model)
        if name is None:
            return False
        root = Path(self.download_root)
        if not root.exists():
            return False
        # faster-whisper stores each model in a Hugging Face cache folder.
        return any(
            entry.is_dir() and name in entry.name.lower() for entry in root.rglob("*")
            if entry.is_dir()
        )

    @property
    def is_ready(self) -> bool:
        return self._engine is not None

    def prepare(self) -> None:
        if self._engine is not None:
            return
        if not self.is_available():
            raise TranscriptionProviderError(
                "faster-whisper is not installed. Install it with: pip install faster-whisper"
            )
        name = FASTER_WHISPER_MODEL_NAMES.get(self.model)
        if name is None:
            raise ModelNotReadyError(self.model.value)
        from faster_whisper import WhisperModel

        Path(self.download_root).mkdir(parents=True, exist_ok=True)
        self._engine = WhisperModel(
            name,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(self.download_root),
        )

    def transcribe(self, samples: Sequence[float], language: str | None = None) -> TranscriptionResult:
        if self._engine is None:
            self.prepare()
        started = time.monotonic()
        audio = np.asarray(samples, dtype=np.float32)
        segments, info = self._engine.transcribe(  # type: ignore[union-attr]
            audio,
            language=language,
            beam_size=self.beam_size,
            vad_filter=False,
        )
        text = "".join(segment.text for segment in segments).strip()
        return TranscriptionResult(
            text=text,
            duration_milliseconds=int((time.monotonic() - started) * 1000),
            language=getattr(info, "language", language),
        )

    def release(self) -> None:
        self._engine = None


@dataclass
class WhisperCppProvider:
    """Runs an installed `whisper-cli`/`whisper.cpp` binary over GGUF weights."""

    model: SpeechModel = SpeechModel.WHISPER_BASE
    binary: str | None = None
    model_directory: Path = field(default_factory=whisper_model_directory)

    @property
    def model_id(self) -> str:
        return self.model.value

    @staticmethod
    def find_binary() -> str | None:
        for name in ("whisper-cli", "whisper-cpp", "whisper"):
            path = shutil.which(name)
            if path:
                return path
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls.find_binary() is not None

    @property
    def model_path(self) -> Path:
        file_name = self.model.whisper_model_file or f"{self.model.value}.gguf"
        return Path(self.model_directory) / file_name

    @property
    def legacy_model_path(self) -> Path:
        file_name = self.model.legacy_whisper_model_file or f"{self.model.value}.bin"
        return Path(self.model_directory) / file_name

    @property
    def is_ready(self) -> bool:
        return self.model_path.exists() or self.legacy_model_path.exists()

    def prepare(self) -> None:
        if not self.is_ready:
            raise ModelNotReadyError(self.model.value)
        if self.binary is None:
            self.binary = self.find_binary()
        if self.binary is None:
            raise TranscriptionProviderError("No whisper.cpp binary is installed.")

    def transcribe(self, samples: Sequence[float], language: str | None = None) -> TranscriptionResult:
        self.prepare()
        started = time.monotonic()
        weights = self.model_path if self.model_path.exists() else self.legacy_model_path
        with tempfile.TemporaryDirectory(prefix="fluentry-asr-") as directory:
            audio_path = Path(directory) / "input.wav"
            write_wav(audio_path, samples)
            command = [
                str(self.binary),
                "-m", str(weights),
                "-f", str(audio_path),
                "--output-json",
                "--no-timestamps",
                "-of", str(Path(directory) / "out"),
            ]
            if language:
                command += ["-l", language]
            try:
                subprocess.run(command, capture_output=True, timeout=300, check=True)
            except (OSError, subprocess.SubprocessError) as error:
                raise TranscriptionProviderError(str(error)) from error
            json_path = Path(directory) / "out.json"
            text = ""
            if json_path.exists():
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    text = "".join(
                        item.get("text", "") for item in payload.get("transcription") or []
                    ).strip()
                except ValueError:
                    text = ""
        return TranscriptionResult(
            text=text,
            duration_milliseconds=int((time.monotonic() - started) * 1000),
            language=language,
        )

    def release(self) -> None:
        return None


def whisper_runtime_is_available() -> bool:
    """Whether anything on this machine can run a Whisper model."""
    return FasterWhisperProvider.is_available() or WhisperCppProvider.is_available()


def make_whisper_provider(model: SpeechModel) -> object:
    """Prefer faster-whisper; fall back to an installed whisper.cpp binary."""
    if FasterWhisperProvider.is_available():
        return FasterWhisperProvider(model=model)
    if WhisperCppProvider.is_available():
        return WhisperCppProvider(model=model)
    raise TranscriptionProviderError(
        "No Whisper runtime is available. Install faster-whisper "
        "(pip install faster-whisper) or a whisper.cpp binary."
    )
