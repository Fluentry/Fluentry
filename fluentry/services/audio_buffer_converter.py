"""Convert captured PCM to the mono float stream the ASR models expect.

A port of `AudioBufferConverter`. macOS used `AVAudioConverter`; here the same
two operations — downmix to mono and resample — are done with NumPy, which is
already a hard dependency of the model runtimes.

Downmix averages the channels, matching `AVAudioConverter.downmix`, so a signal
present on only one channel of a stereo pair arrives at half amplitude.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


class AudioConversionError(Exception):
    pass


class InvalidSourceFormatError(AudioConversionError):
    def __str__(self) -> str:
        return "Audio buffer has an invalid sample rate or channel count."


class InvalidTargetSampleRateError(AudioConversionError):
    def __str__(self) -> str:
        return "Target audio sample rate must be greater than zero."


class InaccessibleChannelDataError(AudioConversionError):
    def __str__(self) -> str:
        return "Could not access Float32 audio channel data."


@dataclass(frozen=True)
class PCMBuffer:
    """Non-interleaved float32 frames, one row per channel."""

    data: np.ndarray
    sample_rate: float

    @property
    def channel_count(self) -> int:
        return int(self.data.shape[0]) if self.data.ndim == 2 else 1

    @property
    def frame_length(self) -> int:
        if self.data.ndim == 2:
            return int(self.data.shape[1])
        return int(self.data.shape[0])

    @staticmethod
    def silent(sample_rate: float, channels: int, frame_count: int) -> "PCMBuffer":
        return PCMBuffer(np.zeros((channels, frame_count), dtype=np.float32), sample_rate)

    @staticmethod
    def from_mono(samples: Sequence[float], sample_rate: float) -> "PCMBuffer":
        array = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        return PCMBuffer(array, sample_rate)

    @staticmethod
    def from_interleaved(samples: Sequence[float], sample_rate: float, channels: int) -> "PCMBuffer":
        array = np.asarray(samples, dtype=np.float32)
        if channels <= 0:
            raise InvalidSourceFormatError()
        frames = array.size // channels
        deinterleaved = array[: frames * channels].reshape(frames, channels).T
        return PCMBuffer(np.ascontiguousarray(deinterleaved), sample_rate)

    def channel(self, index: int) -> np.ndarray:
        if self.data.ndim == 1:
            if index != 0:
                raise InaccessibleChannelDataError()
            return self.data
        return self.data[index]


def resample(samples: np.ndarray, source_rate: float, target_rate: float) -> np.ndarray:
    """Linear resample, matching the frame count `AVAudioConverter` produced."""
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    ratio = target_rate / source_rate
    target_length = int(np.ceil(samples.size * ratio))
    if target_length <= 0:
        return np.zeros(0, dtype=np.float32)
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(target_length, dtype=np.float64) / ratio
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def mono_samples(buffer: PCMBuffer, target_sample_rate: float) -> list[float]:
    if buffer.sample_rate <= 0 or buffer.channel_count <= 0:
        raise InvalidSourceFormatError()
    if not np.isfinite(target_sample_rate) or target_sample_rate <= 0:
        raise InvalidTargetSampleRateError()
    if buffer.frame_length == 0:
        return []

    if buffer.sample_rate == target_sample_rate and buffer.channel_count == 1:
        return [float(value) for value in buffer.channel(0)]

    if buffer.data.ndim == 2 and buffer.channel_count > 1:
        mixed = buffer.data.mean(axis=0, dtype=np.float64).astype(np.float32)
    else:
        mixed = np.asarray(buffer.channel(0), dtype=np.float32)

    converted = resample(mixed, buffer.sample_rate, target_sample_rate)
    return [float(value) for value in converted]


def rms(samples: Sequence[float] | np.ndarray) -> float:
    array = np.asarray(samples, dtype=np.float32)
    if array.size == 0:
        return 0.0
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(finite, dtype=np.float64))))


def peak(samples: Sequence[float] | np.ndarray) -> float:
    array = np.asarray(samples, dtype=np.float32)
    if array.size == 0:
        return 0.0
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return 0.0
    return float(np.max(np.abs(finite)))


def frame_count(byte_count: int, bytes_per_sample: int, channels: int) -> int:
    """Frames in a buffer, using the buffer's *actual* channel layout.

    A non-interleaved capture hands over one buffer per channel, each holding
    a single channel's frames. Dividing by the stream's channel count instead
    of the buffer's own would under-report every frame count — the bug that
    turned 512-frame buffers into 170 frames in the field.
    """
    divisor = bytes_per_sample * max(1, channels)
    if divisor <= 0:
        return 0
    return byte_count // divisor
