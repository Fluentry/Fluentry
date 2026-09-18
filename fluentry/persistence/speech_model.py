"""Speech model catalog.

The macOS build ran Parakeet/Nemotron/Cohere through CoreML and offered Apple
Speech as a zero-download option. On Linux the same model families are reached
through different runtimes:

==========================  ====================  ==========================
macOS runtime               Linux runtime         Notes
==========================  ====================  ==========================
CoreML (FluidAudio)         ONNX Runtime          Parakeet / Nemotron / Cohere
whisper.cpp (Metal)         whisper.cpp (CPU/     identical GGUF weights
                            Vulkan/CUDA)
Apple Speech / Analyzer     —                     no analogue; kept for
                                                  decoding old backups only
==========================  ====================  ==========================

Raw values are unchanged so a backup from the macOS build still decodes; the
two Apple cases simply report `is_supported == False` and are migrated to the
platform default on load.
"""

from __future__ import annotations

from enum import Enum

from .settings_types import StrEnum


class SpeechBackend(Enum):
    WHISPER_CPP = "whisper.cpp"
    ONNX = "onnxruntime"
    UNSUPPORTED = "unsupported"


#: Qwen stays behind a preview flag, matching the macOS build.
QWEN_PREVIEW_ENABLED = False


class SpeechModel(StrEnum):
    # ONNX models (were CoreML on macOS)
    PARAKEET_TDT = "parakeet-tdt"
    PARAKEET_TDT_V2 = "parakeet-tdt-v2"
    PARAKEET_REALTIME = "parakeet-realtime"
    QWEN3_ASR = "qwen3-asr"
    COHERE_TRANSCRIBE_SIX_BIT = "cohere-transcribe-6bit"
    NEMOTRON_OFFLINE = "nemotron-3.5-offline"
    NEMOTRON_STREAMING = "nemotron-3.5-streaming"
    NEMOTRON_STREAMING_320 = "nemotron-3.5-streaming-320"

    # macOS-only, retained so old backups decode
    APPLE_SPEECH = "apple-speech"
    APPLE_SPEECH_ANALYZER = "apple-speech-analyzer"

    # whisper.cpp
    WHISPER_TINY = "whisper-tiny"
    WHISPER_BASE = "whisper-base"
    WHISPER_SMALL = "whisper-small"
    WHISPER_MEDIUM = "whisper-medium"
    WHISPER_LARGE_TURBO = "whisper-large-turbo"
    WHISPER_LARGE = "whisper-large"

    # --- classification ---------------------------------------------------

    @property
    def backend(self) -> SpeechBackend:
        if self in _APPLE_MODELS:
            return SpeechBackend.UNSUPPORTED
        if self.is_whisper_model:
            return SpeechBackend.WHISPER_CPP
        return SpeechBackend.ONNX

    @property
    def is_supported(self) -> bool:
        return self.backend is not SpeechBackend.UNSUPPORTED

    @property
    def is_whisper_model(self) -> bool:
        return self in _WHISPER_MODELS

    @property
    def is_streaming_model(self) -> bool:
        return self in {
            SpeechModel.PARAKEET_REALTIME,
            SpeechModel.NEMOTRON_STREAMING,
            SpeechModel.NEMOTRON_STREAMING_320,
        }

    # --- files ------------------------------------------------------------

    @property
    def whisper_model_file(self) -> str | None:
        return _WHISPER_FILES.get(self)

    @property
    def legacy_whisper_model_file(self) -> str | None:
        return _LEGACY_WHISPER_FILES.get(self)

    @property
    def whisper_model_name(self) -> str | None:
        return _WHISPER_NAMES.get(self)

    @property
    def onnx_repository(self) -> str | None:
        return _ONNX_REPOSITORIES.get(self)

    # --- display ----------------------------------------------------------

    @property
    def display_name(self) -> str:
        return _DISPLAY_NAMES[self]

    @property
    def human_readable_name(self) -> str:
        return _HUMAN_READABLE_NAMES[self]

    @property
    def language_support(self) -> str:
        return _LANGUAGE_SUPPORT[self]

    @property
    def download_size(self) -> str:
        return _DOWNLOAD_SIZES[self]

    @property
    def expected_download_bytes(self) -> int:
        return _EXPECTED_BYTES.get(self, 0)

    @property
    def required_memory_gb(self) -> float:
        return _REQUIRED_MEMORY_GB[self]

    @property
    def memory_warning(self) -> str | None:
        return _MEMORY_WARNINGS.get(self)

    @property
    def supported_language_codes(self) -> str | None:
        return _SUPPORTED_LANGUAGE_CODES.get(self)

    # --- availability -----------------------------------------------------

    @staticmethod
    def available_models() -> list["SpeechModel"]:
        """Models this machine can actually run."""
        from ..platform.system_capabilities import total_memory_gb

        memory = total_memory_gb()
        models: list[SpeechModel] = []
        for model in SpeechModel:
            if not model.is_supported:
                continue
            if model is SpeechModel.QWEN3_ASR and not QWEN_PREVIEW_ENABLED:
                continue
            if model is SpeechModel.NEMOTRON_STREAMING_320:
                continue
            # macOS hid the biggest Whisper builds from Intel Macs; the Linux
            # equivalent constraint is available RAM.
            if memory and model.required_memory_gb > memory:
                continue
            models.append(model)
        return models

    @staticmethod
    def default_model() -> "SpeechModel":
        """Parakeet when the machine can host it, Whisper Base otherwise."""
        from ..platform.system_capabilities import total_memory_gb

        memory = total_memory_gb()
        if memory is None or memory >= SpeechModel.PARAKEET_TDT.required_memory_gb:
            return SpeechModel.PARAKEET_TDT
        return SpeechModel.WHISPER_BASE

    @staticmethod
    def supported_or_default(model: "SpeechModel | None") -> "SpeechModel":
        """Replace an unsupported (macOS-only) selection with the platform default."""
        if model is None or not model.is_supported:
            return SpeechModel.default_model()
        return model


_APPLE_MODELS = frozenset({SpeechModel.APPLE_SPEECH, SpeechModel.APPLE_SPEECH_ANALYZER})

_WHISPER_MODELS = frozenset(
    {
        SpeechModel.WHISPER_TINY,
        SpeechModel.WHISPER_BASE,
        SpeechModel.WHISPER_SMALL,
        SpeechModel.WHISPER_MEDIUM,
        SpeechModel.WHISPER_LARGE_TURBO,
        SpeechModel.WHISPER_LARGE,
    }
)

_WHISPER_FILES = {
    SpeechModel.WHISPER_TINY: "whisper-tiny-Q8_0.gguf",
    SpeechModel.WHISPER_BASE: "whisper-base-Q8_0.gguf",
    SpeechModel.WHISPER_SMALL: "whisper-small-Q8_0.gguf",
    SpeechModel.WHISPER_MEDIUM: "whisper-medium-Q8_0.gguf",
    SpeechModel.WHISPER_LARGE_TURBO: "whisper-large-v3-turbo-Q8_0.gguf",
    SpeechModel.WHISPER_LARGE: "whisper-large-v3-Q8_0.gguf",
}

_LEGACY_WHISPER_FILES = {
    SpeechModel.WHISPER_TINY: "ggml-tiny.bin",
    SpeechModel.WHISPER_BASE: "ggml-base.bin",
    SpeechModel.WHISPER_SMALL: "ggml-small.bin",
    SpeechModel.WHISPER_MEDIUM: "ggml-medium.bin",
    SpeechModel.WHISPER_LARGE_TURBO: "ggml-large-v3-turbo.bin",
    SpeechModel.WHISPER_LARGE: "ggml-large-v3.bin",
}

_WHISPER_NAMES = {
    SpeechModel.WHISPER_TINY: "tiny",
    SpeechModel.WHISPER_BASE: "base",
    SpeechModel.WHISPER_SMALL: "small",
    SpeechModel.WHISPER_MEDIUM: "medium",
    SpeechModel.WHISPER_LARGE_TURBO: "large-v3-turbo",
    SpeechModel.WHISPER_LARGE: "large-v3",
}

_ONNX_REPOSITORIES = {
    SpeechModel.PARAKEET_TDT: "istupakov/parakeet-tdt-0.6b-v3-onnx",
    SpeechModel.PARAKEET_TDT_V2: "istupakov/parakeet-tdt-0.6b-v2-onnx",
    SpeechModel.PARAKEET_REALTIME: "nvidia/parakeet_realtime_eou_120m-v1",
    SpeechModel.QWEN3_ASR: "Qwen/Qwen3-ASR-onnx",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "CohereLabs/cohere-transcribe-onnx",
    SpeechModel.NEMOTRON_OFFLINE: "nvidia/nemotron-speech-3.5-onnx",
    SpeechModel.NEMOTRON_STREAMING: "nvidia/nemotron-speech-3.5-streaming-onnx",
    SpeechModel.NEMOTRON_STREAMING_320: "nvidia/nemotron-speech-3.5-streaming-onnx",
}

_DISPLAY_NAMES = {
    SpeechModel.PARAKEET_TDT: "Parakeet TDT v3 (Multilingual)",
    SpeechModel.PARAKEET_TDT_V2: "Parakeet TDT v2 (English Only)",
    SpeechModel.PARAKEET_REALTIME: "Parakeet Flash (Beta)",
    SpeechModel.QWEN3_ASR: "Qwen3 ASR (Beta)",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "Cohere Transcribe",
    SpeechModel.NEMOTRON_OFFLINE: "Nemotron 3.5 Multilingual",
    SpeechModel.NEMOTRON_STREAMING: "Nemotron Speech 3.5 - Ultra Fast Low Latency",
    SpeechModel.NEMOTRON_STREAMING_320: "Nemotron Speech 3.5 - Ultra Fast Low Latency",
    SpeechModel.APPLE_SPEECH: "Apple ASR Legacy (macOS only)",
    SpeechModel.APPLE_SPEECH_ANALYZER: "Apple Speech (macOS only)",
    SpeechModel.WHISPER_TINY: "Whisper Tiny",
    SpeechModel.WHISPER_BASE: "Whisper Base",
    SpeechModel.WHISPER_SMALL: "Whisper Small",
    SpeechModel.WHISPER_MEDIUM: "Whisper Medium",
    SpeechModel.WHISPER_LARGE_TURBO: "Whisper Large Turbo",
    SpeechModel.WHISPER_LARGE: "Whisper Large",
}

_HUMAN_READABLE_NAMES = {
    SpeechModel.PARAKEET_TDT: "Blazing Fast - Multilingual",
    SpeechModel.PARAKEET_TDT_V2: "Blazing Fast - English",
    SpeechModel.PARAKEET_REALTIME: "Flash Dictation",
    SpeechModel.QWEN3_ASR: "Qwen3 - Multilingual",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "Cohere - High Accuracy",
    SpeechModel.NEMOTRON_OFFLINE: "Nemotron 3.5 Multilingual",
    SpeechModel.NEMOTRON_STREAMING: "Nemotron Speech 3.5 - Ultra Fast Low Latency",
    SpeechModel.NEMOTRON_STREAMING_320: "Nemotron Speech 3.5 - Ultra Fast Low Latency",
    SpeechModel.APPLE_SPEECH: "Apple ASR Legacy",
    SpeechModel.APPLE_SPEECH_ANALYZER: "Apple Speech",
    SpeechModel.WHISPER_TINY: "Fast & Light",
    SpeechModel.WHISPER_BASE: "Standard Choice",
    SpeechModel.WHISPER_SMALL: "Balanced Speed & Accuracy",
    SpeechModel.WHISPER_MEDIUM: "Medium Quality",
    SpeechModel.WHISPER_LARGE_TURBO: "Higher Quality but Faster",
    SpeechModel.WHISPER_LARGE: "Maximum Accuracy",
}

_WHISPER_LANGUAGES = "99 Languages"
_LANGUAGE_SUPPORT = {
    SpeechModel.PARAKEET_TDT: "25 Languages",
    SpeechModel.PARAKEET_TDT_V2: "English Only (Higher Accuracy)",
    SpeechModel.PARAKEET_REALTIME: "English Only (Live Streaming)",
    SpeechModel.QWEN3_ASR: "30 Languages",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "14 Languages (Select Manually)",
    SpeechModel.NEMOTRON_OFFLINE: "Around 40 Languages",
    SpeechModel.NEMOTRON_STREAMING: "Around 40 Languages",
    SpeechModel.NEMOTRON_STREAMING_320: "Around 40 Languages",
    SpeechModel.APPLE_SPEECH: "Unavailable on Linux",
    SpeechModel.APPLE_SPEECH_ANALYZER: "Unavailable on Linux",
    SpeechModel.WHISPER_TINY: _WHISPER_LANGUAGES,
    SpeechModel.WHISPER_BASE: _WHISPER_LANGUAGES,
    SpeechModel.WHISPER_SMALL: _WHISPER_LANGUAGES,
    SpeechModel.WHISPER_MEDIUM: _WHISPER_LANGUAGES,
    SpeechModel.WHISPER_LARGE_TURBO: _WHISPER_LANGUAGES,
    SpeechModel.WHISPER_LARGE: _WHISPER_LANGUAGES,
}

_DOWNLOAD_SIZES = {
    SpeechModel.PARAKEET_TDT: "~460.9 MiB",
    SpeechModel.PARAKEET_TDT_V2: "~442.9 MiB",
    SpeechModel.PARAKEET_REALTIME: "~428.4 MiB",
    SpeechModel.QWEN3_ASR: "~2.0 GiB",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "~1.54 GiB",
    SpeechModel.NEMOTRON_OFFLINE: "~530.8 MiB",
    SpeechModel.NEMOTRON_STREAMING: "~668.2 MiB",
    SpeechModel.NEMOTRON_STREAMING_320: "~668.2 MiB",
    SpeechModel.APPLE_SPEECH: "Unavailable",
    SpeechModel.APPLE_SPEECH_ANALYZER: "Unavailable",
    SpeechModel.WHISPER_TINY: "~43.9 MiB",
    SpeechModel.WHISPER_BASE: "~81.0 MiB",
    SpeechModel.WHISPER_SMALL: "~257.3 MiB",
    SpeechModel.WHISPER_MEDIUM: "~793.0 MiB",
    SpeechModel.WHISPER_LARGE_TURBO: "~845.3 MiB",
    SpeechModel.WHISPER_LARGE: "~1.55 GiB",
}

_EXPECTED_BYTES = {
    SpeechModel.PARAKEET_TDT: 483_288_717,
    SpeechModel.PARAKEET_TDT_V2: 464_421_712,
    SpeechModel.PARAKEET_REALTIME: 449_190_189,
    SpeechModel.QWEN3_ASR: 2000 * 1024 * 1024,
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: 1_650_748_785,
    SpeechModel.NEMOTRON_OFFLINE: 556_552_620,
    SpeechModel.NEMOTRON_STREAMING: 700_685_415,
    SpeechModel.NEMOTRON_STREAMING_320: 700_685_415,
    SpeechModel.WHISPER_TINY: 45_981_088,
    SpeechModel.WHISPER_BASE: 84_962_880,
    SpeechModel.WHISPER_SMALL: 269_751_136,
    SpeechModel.WHISPER_MEDIUM: 831_538_144,
    SpeechModel.WHISPER_LARGE_TURBO: 886_381_760,
    SpeechModel.WHISPER_LARGE: 1_668_741_440,
    SpeechModel.APPLE_SPEECH: 0,
    SpeechModel.APPLE_SPEECH_ANALYZER: 0,
}

_REQUIRED_MEMORY_GB = {
    SpeechModel.PARAKEET_TDT: 4.0,
    SpeechModel.PARAKEET_TDT_V2: 4.0,
    SpeechModel.PARAKEET_REALTIME: 4.0,
    SpeechModel.QWEN3_ASR: 8.0,
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: 8.0,
    SpeechModel.NEMOTRON_OFFLINE: 8.0,
    SpeechModel.NEMOTRON_STREAMING: 8.0,
    SpeechModel.NEMOTRON_STREAMING_320: 8.0,
    SpeechModel.APPLE_SPEECH: 2.0,
    SpeechModel.APPLE_SPEECH_ANALYZER: 2.0,
    SpeechModel.WHISPER_TINY: 2.0,
    SpeechModel.WHISPER_BASE: 3.0,
    SpeechModel.WHISPER_SMALL: 4.0,
    SpeechModel.WHISPER_MEDIUM: 5.0,
    SpeechModel.WHISPER_LARGE_TURBO: 6.0,
    SpeechModel.WHISPER_LARGE: 8.0,
}

_MEMORY_WARNINGS = {
    SpeechModel.QWEN3_ASR: "⚠️ Requires 8GB+ RAM.",
    SpeechModel.WHISPER_LARGE: "⚠️ Requires 10GB+ RAM. May crash on systems with limited memory.",
    SpeechModel.WHISPER_LARGE_TURBO: "⚠️ Requires 8GB+ RAM. May be unstable on some systems.",
    SpeechModel.WHISPER_MEDIUM: "⚠️ Requires 5GB+ RAM.",
}

_SUPPORTED_LANGUAGE_CODES = {
    SpeechModel.PARAKEET_TDT: (
        "BG, HR, CS, DA, NL, EN, ET, FI, FR, DE, EL, HU, IT, LV, LT, MT, "
        "PL, PT, RO, SK, SL, ES, SV, RU, UK"
    ),
    SpeechModel.PARAKEET_REALTIME: "EN",
    SpeechModel.PARAKEET_TDT_V2: "EN",
    SpeechModel.COHERE_TRANSCRIBE_SIX_BIT: "AR, DE, EL, EN, ES, FR, IT, JA, KO, NL, PL, PT, VI, ZH",
}
