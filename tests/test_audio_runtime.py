"""Port of AudioBufferConverterTests, AudioEngineRetirementDrainTests and the
readiness/queue coverage in DirectAudioReliabilityTests."""

import threading
import time

import numpy as np
import pytest

from fluentry.services.audio_buffer_converter import (
    InvalidSourceFormatError,
    InvalidTargetSampleRateError,
    PCMBuffer,
    frame_count,
    mono_samples,
    peak,
    rms,
)
from fluentry.services.audio_runtime import (
    AudioCaptureReadinessGate,
    AudioCaptureReadinessResult,
    AudioEngineRetirementDrain,
    AudioEngineRetirementToken,
    BoundedAudioHardwareQueue,
    CancellationToken,
    HardwareCleanupFailed,
    HardwareRecovering,
    HardwareTimedOut,
    OperationCancelled,
    ThreadSafeAudioBuffer,
)


def stereo(frame_count_: int, left: float, right: float, sample_rate: float = 16_000) -> PCMBuffer:
    data = np.zeros((2, frame_count_), dtype=np.float32)
    data[0, :] = left
    data[1, :] = right
    return PCMBuffer(data, sample_rate)


# --- buffer conversion ------------------------------------------------------


def test_stereo_downmix_includes_right_channel():
    samples = mono_samples(stereo(16, left=0, right=1), 16_000)
    assert len(samples) == 16
    assert all(abs(sample - 0.5) < 1e-5 for sample in samples)


def test_stereo_downmix_includes_left_channel():
    samples = mono_samples(stereo(16, left=1, right=0), 16_000)
    assert len(samples) == 16
    assert all(abs(sample - 0.5) < 1e-5 for sample in samples)


def test_stereo_downmix_preserves_matching_channels():
    samples = mono_samples(stereo(16, left=1, right=1), 16_000)
    assert len(samples) == 16
    assert all(abs(sample - 1.0) < 1e-5 for sample in samples)


def test_mono_float_fast_path_preserves_samples():
    expected = [0.25, -0.5, 0.75, -1.0]
    samples = mono_samples(PCMBuffer.from_mono(expected, 16_000), 16_000)
    assert samples == expected


def test_stereo_downmix_and_resample_includes_right_channel():
    samples = mono_samples(stereo(480, left=0, right=1, sample_rate=48_000), 16_000)
    assert len(samples) == 160
    assert abs(sum(samples) / len(samples) - 0.5) < 0.01
    assert max(abs(sample) for sample in samples) > 0.4


def test_resampling_preserves_a_tone_roughly():
    sample_rate = 48_000
    duration = 0.05
    t = np.arange(int(sample_rate * duration)) / sample_rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    samples = mono_samples(PCMBuffer.from_mono(tone, sample_rate), 16_000)

    assert len(samples) == int(np.ceil(len(tone) / 3))
    assert 0.3 < max(abs(sample) for sample in samples) <= 0.51


def test_empty_buffer_converts_to_no_samples():
    assert mono_samples(PCMBuffer.silent(16_000, 1, 0), 16_000) == []


def test_invalid_formats_are_rejected():
    with pytest.raises(InvalidSourceFormatError):
        mono_samples(PCMBuffer.silent(0, 1, 16), 16_000)
    with pytest.raises(InvalidTargetSampleRateError):
        mono_samples(PCMBuffer.silent(16_000, 1, 16), 0)
    with pytest.raises(InvalidTargetSampleRateError):
        mono_samples(PCMBuffer.silent(16_000, 1, 16), float("nan"))


def test_interleaved_input_is_deinterleaved_before_downmix():
    buffer = PCMBuffer.from_interleaved([1.0, 0.0, 1.0, 0.0], 16_000, channels=2)
    assert buffer.channel_count == 2
    assert buffer.frame_length == 2
    assert mono_samples(buffer, 16_000) == [0.5, 0.5]


def test_frame_count_uses_the_actual_buffer_channel_layout():
    # Regression: a non-interleaved capture hands one buffer per channel.
    assert frame_count(512 * 4, 4, 1) == 512
    assert frame_count(512 * 8, 4, 2) == 512
    assert frame_count(512 * 12, 4, 3) == 512
    for _ in range(3):
        assert frame_count(512 * 4, 4, 1) == 512


def test_rms_and_peak_ignore_non_finite_samples():
    assert rms([]) == 0.0
    assert peak([]) == 0.0
    assert rms([float("nan"), 1.0, -1.0]) == pytest.approx(1.0)
    assert peak([float("inf"), 0.25]) == pytest.approx(0.25)


# --- thread-safe buffer -----------------------------------------------------


def test_thread_safe_buffer_accumulates_and_slices():
    buffer = ThreadSafeAudioBuffer()
    buffer.append([1.0, 2.0, 3.0])
    buffer.append([4.0])

    assert buffer.count == 4
    assert buffer.get_all() == [1.0, 2.0, 3.0, 4.0]
    assert buffer.get_prefix(2) == [1.0, 2.0]
    assert buffer.get_prefix(99) == [1.0, 2.0, 3.0, 4.0]
    assert buffer.get_range(1, 2) == [2.0, 3.0]
    assert buffer.get_range(3, 2) == [], "an incomplete range yields nothing"
    assert buffer.get_range(-1, 2) == []

    buffer.clear()
    assert buffer.count == 0


def test_thread_safe_buffer_survives_concurrent_appends():
    buffer = ThreadSafeAudioBuffer()

    def append_many() -> None:
        for _ in range(200):
            buffer.append([0.5])

    threads = [threading.Thread(target=append_many) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert buffer.count == 800


# --- retirement drain -------------------------------------------------------


class ReleaseProbe:
    def __init__(self, identifier: int, recorder: list) -> None:
        self.identifier = identifier
        self.recorder = recorder

    def close(self) -> None:
        self.recorder.append(
            (self.identifier, threading.current_thread() is threading.main_thread())
        )


def test_release_and_wait_completes_off_the_main_thread():
    drain = AudioEngineRetirementDrain(name="test.retirement.single")
    recorded: list = []
    drain.release_and_wait(AudioEngineRetirementToken(ReleaseProbe(1, recorded)))
    drain.shutdown()

    assert recorded == [(1, False)]


def test_awaited_release_runs_after_previously_scheduled_release():
    drain = AudioEngineRetirementDrain(name="test.retirement.serial")
    recorded: list = []
    drain.schedule(AudioEngineRetirementToken(ReleaseProbe(1, recorded)))
    drain.release_and_wait(AudioEngineRetirementToken(ReleaseProbe(2, recorded)))
    drain.shutdown()

    assert recorded == [(1, False), (2, False)]


def test_wait_for_scheduled_releases_completes_after_scheduled_release():
    drain = AudioEngineRetirementDrain(name="test.retirement.barrier")
    recorded: list = []
    drain.schedule(AudioEngineRetirementToken(ReleaseProbe(1, recorded)))
    drain.wait_for_scheduled_releases()
    drain.shutdown()

    assert recorded == [(1, False)]


def test_wait_for_scheduled_releases_returns_immediately_when_idle():
    drain = AudioEngineRetirementDrain(name="test.retirement.idle")
    started = time.monotonic()
    drain.wait_for_scheduled_releases()
    assert time.monotonic() - started < 0.5
    drain.shutdown()


# --- readiness gate ---------------------------------------------------------


def test_readiness_gate_reports_ready_when_pcm_arrives():
    gate = AudioCaptureReadinessGate()
    gate.arm(session_id=1, attempt_id=1)

    threading.Timer(0.01, lambda: gate.signal_first_pcm(1, 1)).start()
    assert gate.wait(1, 1, timeout=2) is AudioCaptureReadinessResult.READY


def test_readiness_gate_times_out_when_no_pcm_arrives():
    gate = AudioCaptureReadinessGate()
    gate.arm(session_id=1, attempt_id=1)
    assert gate.wait(1, 1, timeout=0.05) is AudioCaptureReadinessResult.TIMED_OUT


def test_readiness_gate_rejects_a_stale_attempt():
    gate = AudioCaptureReadinessGate()
    gate.arm(session_id=1, attempt_id=1)
    assert gate.wait(1, 2, timeout=0.05) is AudioCaptureReadinessResult.STALE_SESSION
    assert gate.wait(2, 1, timeout=0.05) is AudioCaptureReadinessResult.STALE_SESSION


def test_readiness_gate_reports_cancellation_and_format_invalidation():
    gate = AudioCaptureReadinessGate()
    gate.arm(1, 1)
    gate.cancel(1, 1)
    assert gate.wait(1, 1, timeout=0.05) is AudioCaptureReadinessResult.CANCELLED

    gate.arm(1, 2)
    gate.signal_format_invalidation(1, 2)
    assert gate.wait(1, 2, timeout=0.05) is AudioCaptureReadinessResult.FORMAT_INVALIDATED


def test_readiness_gate_keeps_the_first_result_for_an_attempt():
    gate = AudioCaptureReadinessGate()
    gate.arm(1, 1)
    gate.signal_first_pcm(1, 1)
    gate.cancel(1, 1)
    assert gate.wait(1, 1, timeout=0.05) is AudioCaptureReadinessResult.READY


def test_rearming_releases_a_waiter_from_the_superseded_attempt():
    gate = AudioCaptureReadinessGate()
    gate.arm(1, 1)
    results: list = []

    waiter = threading.Thread(target=lambda: results.append(gate.wait(1, 1, timeout=5)))
    waiter.start()
    time.sleep(0.05)
    gate.arm(1, 2)
    waiter.join(timeout=2)

    assert results == [AudioCaptureReadinessResult.CANCELLED]


# --- bounded hardware queue -------------------------------------------------


def always_recovers() -> bool:
    return True


def test_bounded_queue_runs_an_operation_and_returns_its_value():
    queue = BoundedAudioHardwareQueue()
    assert queue.run(lambda: "opened", recover=always_recovers) == "opened"
    assert queue.is_available


def test_bounded_queue_propagates_operation_failures():
    queue = BoundedAudioHardwareQueue()

    def boom():
        raise RuntimeError("device busy")

    with pytest.raises(RuntimeError, match="device busy"):
        queue.run(boom, recover=always_recovers)


def test_bounded_queue_times_out_without_aborting_the_native_call():
    queue = BoundedAudioHardwareQueue()
    finished = threading.Event()

    def slow():
        time.sleep(0.3)
        finished.set()
        return "late"

    with pytest.raises(HardwareTimedOut):
        queue.run(slow, recover=always_recovers, timeout=0.05)

    # The native call is never aborted, only the caller is released.
    assert finished.wait(2)


def test_bounded_queue_refuses_new_work_until_recovery_completes():
    queue = BoundedAudioHardwareQueue()
    release_recovery = threading.Event()

    def slow():
        time.sleep(0.5)
        return "late"

    def recover():
        release_recovery.wait(2)
        return True

    with pytest.raises(HardwareTimedOut):
        queue.run(slow, recover=recover, timeout=0.05)

    with pytest.raises(HardwareRecovering):
        queue.check_available()
    assert not queue.is_available

    release_recovery.set()
    assert queue.wait_until_available(timeout=3)
    queue.check_available()


def test_bounded_queue_latches_a_failed_cleanup_until_explicitly_cleared():
    queue = BoundedAudioHardwareQueue()

    def slow():
        time.sleep(0.3)
        return "late"

    with pytest.raises(HardwareTimedOut):
        queue.run(slow, recover=lambda: False, timeout=0.05)

    deadline = time.monotonic() + 3
    while queue.is_recovering and time.monotonic() < deadline:
        time.sleep(0.01)
    assert queue.has_failed_cleanup

    with pytest.raises(HardwareCleanupFailed):
        queue.check_available()
    with pytest.raises(HardwareCleanupFailed):
        queue.run(lambda: "nope", recover=always_recovers)

    assert queue.clear_failure_after_serialized_recovery()
    queue.check_available()
    assert not queue.clear_failure_after_serialized_recovery(), "clearing twice is a no-op"


def test_bounded_queue_rejects_an_already_cancelled_caller():
    queue = BoundedAudioHardwareQueue()
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelled):
        queue.run(lambda: "never", recover=always_recovers, cancellation=token)


def test_cancel_pending_operations_releases_the_caller():
    queue = BoundedAudioHardwareQueue()
    started = threading.Event()

    def slow():
        started.set()
        time.sleep(0.4)
        return "late"

    errors: list = []

    def caller():
        try:
            queue.run(slow, recover=always_recovers, timeout=5)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=caller)
    thread.start()
    assert started.wait(2)
    queue.cancel_pending_operations()
    thread.join(timeout=3)

    assert len(errors) == 1
    assert isinstance(errors[0], OperationCancelled)
