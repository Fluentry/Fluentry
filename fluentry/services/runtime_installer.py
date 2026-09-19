"""Install a speech engine's runtime when the user asks for the engine.

Some engines need a Python package that cannot be shipped in the .deb:
`ctranslate2` and `tokenizers` are compiled against one CPython ABI, so a
single architecture-independent package cannot carry them. Rather than
leaving somebody with an engine that can only fail, the app fetches the
runtime the first time it is wanted.

It goes into a virtual environment the app owns, under
`$XDG_DATA_HOME/fluentry/runtimes/`. Debian marks its own Python
externally managed, so installing into it is refused outright — and
`--break-system-packages` is exactly what its name says. The environment
is created with `--system-site-packages`, so everything the distribution
already provides (numpy, onnxruntime, av) is used rather than downloaded
again: the difference is roughly 150 MB against 420 MB.

Removing a runtime is deleting its directory.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..logging_setup import get_logger
from ..persistence.defaults import data_home
from ..persistence.speech_model import SpeechBackend, SpeechModel

_log = get_logger("runtime")

#: How long a runtime install may take before it is treated as wedged.
INSTALL_TIMEOUT_SECONDS = 1800.0


@dataclass(frozen=True)
class Runtime:
    """A Python runtime an engine needs, and what it costs to fetch."""

    key: str
    name: str
    packages: tuple[str, ...]
    #: Rough download size, for telling the user before they agree to it.
    megabytes: int
    #: Modules that prove it is present.
    provides: tuple[str, ...]
    #: A way to satisfy this without downloading anything, where one exists.
    alternative: str | None = None

    def is_installed(self) -> bool:
        import importlib.util

        return all(importlib.util.find_spec(module) is not None for module in self.provides)

    @property
    def directory(self) -> Path:
        return data_home() / "runtimes" / self.key

    @property
    def site_packages(self) -> Path | None:
        pattern = f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        candidate = self.directory / pattern
        return candidate if candidate.is_dir() else None


FASTER_WHISPER = Runtime(
    key="faster-whisper",
    name="faster-whisper",
    packages=("faster-whisper",),
    megabytes=150,
    provides=("faster_whisper", "ctranslate2"),
    alternative="a whisper.cpp binary on your PATH (whisper-cli)",
)

SHERPA_ONNX = Runtime(
    key="sherpa-onnx",
    name="sherpa-onnx",
    packages=("sherpa-onnx",),
    megabytes=60,
    provides=("sherpa_onnx",),
)

#: Ships with the .deb, so this is for a pip install without the extra.
ONNX_ASR = Runtime(
    key="onnx-asr",
    name="onnx-asr",
    packages=("onnx-asr>=0.12", "onnxruntime>=1.17", "huggingface_hub>=0.24"),
    megabytes=90,
    provides=("onnx_asr", "onnxruntime"),
)

RUNTIMES = (FASTER_WHISPER, SHERPA_ONNX, ONNX_ASR)


def runtime_for(model: SpeechModel) -> Runtime | None:
    """The runtime this engine needs, or None when nothing is missing.

    Returns None for an engine that already works, so a caller can treat a
    result as "there is something to install".
    """
    if model.backend is SpeechBackend.WHISPER_CPP:
        from .providers.whisper import WhisperCppProvider

        if FASTER_WHISPER.is_installed() or WhisperCppProvider.is_available():
            return None
        return FASTER_WHISPER
    if model.backend is SpeechBackend.ONNX:
        from .providers.onnx_asr import OnnxAsrProvider

        if OnnxAsrProvider.supports(model):
            return None if OnnxAsrProvider.is_available() else ONNX_ASR
        return None if SHERPA_ONNX.is_installed() else SHERPA_ONNX
    return None


def activate_installed_runtimes() -> list[str]:
    """Put previously installed runtimes on the path. Call once at startup."""
    activated = []
    for runtime in RUNTIMES:
        packages = runtime.site_packages
        if packages is None:
            continue
        path = str(packages)
        if path not in sys.path:
            # Appended, never prepended: a distribution package must keep
            # precedence over anything fetched here.
            sys.path.append(path)
        activated.append(runtime.key)
    if activated:
        _log.info("activated runtimes: %s", ", ".join(activated))
    return activated


def install(
    runtime: Runtime,
    on_progress: Callable[[str], None] | None = None,
    python: str | None = None,
) -> str | None:
    """Fetch a runtime. Returns None on success, or a message explaining why not.

    The work is a `python -m venv` followed by a `pip install`, both of
    which can take minutes on a slow connection; `on_progress` is called
    with a line of explanation before each.
    """

    def report(message: str) -> None:
        _log.info("%s", message)
        if on_progress is not None:
            on_progress(message)

    interpreter = python or sys.executable
    directory = runtime.directory
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"Could not create {directory.parent}: {error}"

    if runtime.site_packages is None:
        report(f"Preparing a place for {runtime.name}…")
        created = _run(
            [interpreter, "-m", "venv", "--system-site-packages", str(directory)],
            timeout=180,
        )
        if created is not None:
            return f"Could not create the environment: {created}"

    pip = directory / "bin" / "pip"
    if not pip.exists():
        return f"The environment at {directory} has no pip."

    report(f"Downloading {runtime.name} (about {runtime.megabytes} MB)…")
    failed = _run(
        [str(pip), "install", "--disable-pip-version-check", *runtime.packages],
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if failed is not None:
        return f"Could not install {runtime.name}: {failed}"

    activate_installed_runtimes()
    if not runtime.is_installed():
        return (
            f"{runtime.name} installed but cannot be imported. "
            f"Its files are in {directory}."
        )
    report(f"{runtime.name} is ready.")
    return None


def _run(command: list[str], timeout: float) -> str | None:
    """None when the command succeeded, otherwise why it did not."""
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"{command[0]} timed out after {timeout:.0f}s"
    except OSError as error:
        return str(error)
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        return detail.splitlines()[-1] if detail else f"exit {result.returncode}"
    return None
