"""Microphone capture.

Reading the microphone. The guarantees callers rely on:

* capture is only "started" once real PCM has arrived, never merely because
  the stream opened,
* stream teardown is serialized and never blocks the caller indefinitely,
* a stream that silently stops delivering frames is detected and recovered.

PortAudio (via `sounddevice`) is the default backend because it works over
PipeWire, PulseAudio and bare ALSA. `parec`/`pw-record` is used as a fallback
when PortAudio is unavailable, since either binary ships with the sound server
itself.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

from ..services.audio_buffer_converter import PCMBuffer, mono_samples, peak, rms

TARGET_SAMPLE_RATE = 16_000
DEFAULT_BLOCK_FRAMES = 1024

#: Called with (mono samples at the target rate, rms, peak).
PCMHandler = Callable[[list[float], float, float], None]


class CaptureBackend(Protocol):
    def start(self, device_uid: str | None, on_pcm: PCMHandler) -> None: ...

    def stop(self) -> None: ...

    @property
    def is_running(self) -> bool: ...


class CaptureUnavailableError(Exception):
    pass


@dataclass
class CaptureFormat:
    sample_rate: float = 48_000.0
    channels: int = 1
    block_frames: int = DEFAULT_BLOCK_FRAMES


class SoundDeviceCaptureBackend:
    """PortAudio capture. Preferred backend on every desktop configuration."""

    def __init__(self, target_sample_rate: int = TARGET_SAMPLE_RATE) -> None:
        self.target_sample_rate = target_sample_rate
        self._stream = None
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return True

    def _resolve_device(self, device_uid: str | None):
        import sounddevice as sd

        if not device_uid:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        # PortAudio reports human names; match the PipeWire node name loosely
        # so a stored uid still selects the right hardware.
        needle = device_uid.lower()
        for index, info in enumerate(devices):
            if info.get("max_input_channels", 0) <= 0:
                continue
            name = str(info.get("name", "")).lower()
            if name == needle or name in needle or needle in name:
                return index
        return None

    def start(self, device_uid: str | None, on_pcm: PCMHandler) -> None:
        import sounddevice as sd

        with self._lock:
            if self._stream is not None:
                raise CaptureUnavailableError("Capture is already running.")
            device = self._resolve_device(device_uid)
            try:
                info = sd.query_devices(device, "input") if device is not None else sd.query_devices(
                    kind="input"
                )
                source_rate = float(info.get("default_samplerate") or 48_000)
                channels = min(2, max(1, int(info.get("max_input_channels") or 1)))
            except Exception as error:
                raise CaptureUnavailableError(str(error)) from error

            def callback(indata, frames, time_info, status) -> None:
                # `status` carries overflow/underflow flags; dropped frames are
                # expected under load and must not abort the capture.
                data = np.asarray(indata, dtype=np.float32)
                if data.ndim == 2:
                    data = data.T
                else:
                    data = data.reshape(1, -1)
                buffer = PCMBuffer(data, source_rate)
                samples = mono_samples(buffer, self.target_sample_rate)
                if samples:
                    on_pcm(samples, rms(samples), peak(samples))

            try:
                stream = sd.InputStream(
                    device=device,
                    channels=channels,
                    samplerate=source_rate,
                    dtype="float32",
                    blocksize=DEFAULT_BLOCK_FRAMES,
                    callback=callback,
                )
                stream.start()
            except Exception as error:
                raise CaptureUnavailableError(str(error)) from error
            self._stream = stream

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._stream is not None


class ProcessCaptureBackend:
    """Fallback capture that pipes raw float PCM from the sound server.

    Uses `pw-record` on PipeWire or `parec` on PulseAudio. Slower to start than
    PortAudio but available whenever the sound server itself is.
    """

    def __init__(self, target_sample_rate: int = TARGET_SAMPLE_RATE) -> None:
        self.target_sample_rate = target_sample_rate
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    @staticmethod
    def available_tool() -> str | None:
        for tool in ("pw-record", "parec"):
            if shutil.which(tool):
                return tool
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls.available_tool() is not None

    def _command(self, device_uid: str | None) -> list[str]:
        tool = self.available_tool()
        if tool is None:
            raise CaptureUnavailableError("No sound-server recording tool is installed.")
        if tool == "pw-record":
            command = [
                tool,
                "--rate", str(self.target_sample_rate),
                "--channels", "1",
                "--format", "f32",
            ]
            if device_uid:
                command += ["--target", device_uid]
            command.append("-")
            return command
        command = [
            tool,
            f"--rate={self.target_sample_rate}",
            "--channels=1",
            "--format=float32le",
            "--raw",
        ]
        if device_uid:
            command.append(f"--device={device_uid}")
        return command

    def start(self, device_uid: str | None, on_pcm: PCMHandler) -> None:
        with self._lock:
            if self._process is not None:
                raise CaptureUnavailableError("Capture is already running.")
            self._stopping.clear()
            try:
                process = subprocess.Popen(
                    self._command(device_uid),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as error:
                raise CaptureUnavailableError(str(error)) from error
            self._process = process

        chunk_bytes = DEFAULT_BLOCK_FRAMES * 4

        def pump() -> None:
            stream = process.stdout
            assert stream is not None
            while not self._stopping.is_set():
                data = stream.read(chunk_bytes)
                if not data:
                    break
                samples = np.frombuffer(data, dtype=np.float32)
                if samples.size == 0:
                    continue
                values = [float(value) for value in samples]
                on_pcm(values, rms(samples), peak(samples))

        thread = threading.Thread(target=pump, name="fluentry.capture", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            process = self._process
            self._process = None
            thread = self._thread
            self._thread = None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if thread is not None:
            thread.join(timeout=2)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None


def make_capture_backend(target_sample_rate: int = TARGET_SAMPLE_RATE) -> CaptureBackend:
    if SoundDeviceCaptureBackend.is_available():
        return SoundDeviceCaptureBackend(target_sample_rate)
    if ProcessCaptureBackend.is_available():
        return ProcessCaptureBackend(target_sample_rate)
    raise CaptureUnavailableError(
        "No audio capture backend is available. Install python3-sounddevice, "
        "pipewire-utils (pw-record), or pulseaudio-utils (parec)."
    )
