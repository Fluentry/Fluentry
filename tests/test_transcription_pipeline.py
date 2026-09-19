"""Audio in, transcript out, through the code that actually runs.

A playground dictation produced "Transcribing…" and then an empty page.
The log showed why the app could not say more — four seconds of audio at a
healthy peak, the model running for 3.6 seconds, and an empty string back
— but nothing in the suite exercised the path from captured samples to
transcript, so every layer could be individually correct while the whole
produced nothing.

These run the real provider against the real fixture. They are marked
`model` and deselected by default because they need the weights, and they
are the tests that would have caught this.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fluentry.app import make_speech_provider
from fluentry.persistence.speech_model import SpeechModel
from fluentry.services.audio_buffer_converter import PCMBuffer, mono_samples, resample
from fluentry.services.providers.whisper import read_wav_as_mono_float

FIXTURE = Path(__file__).parent / "resources" / "dictation_fixture.wav"
SPOKEN = "fluid"  # a word the fixture contains, whatever the casing


@pytest.fixture(scope="module")
def provider():
    engine = make_speech_provider(SpeechModel.default_model())
    engine.prepare()
    return engine


@pytest.fixture(scope="module")
def spoken() -> np.ndarray:
    return np.asarray(read_wav_as_mono_float(FIXTURE), dtype=np.float32)


@pytest.mark.model
def test_the_model_transcribes_the_fixture(provider, spoken):
    """The floor: if this fails nothing below it means anything."""
    assert SPOKEN in provider.transcribe(spoken, language=None).text.lower()


@pytest.mark.model
@pytest.mark.parametrize("language", [None, "en", "pt", "auto", ""])
def test_the_language_setting_does_not_silence_the_model(provider, spoken, language):
    """Parakeet is multilingual; a language code must not empty the result."""
    assert SPOKEN in provider.transcribe(spoken, language=language).text.lower()


@pytest.mark.model
def test_a_quiet_recording_is_still_transcribed(provider, spoken):
    """Real dictation came in at a peak of 0.13, not the fixture's 0.74."""
    quiet = spoken * 0.172
    assert max(abs(quiet)) < 0.15
    assert SPOKEN in provider.transcribe(quiet, language=None).text.lower()


@pytest.mark.model
def test_silence_around_the_words_does_not_hide_them(provider, spoken):
    """A four second recording is mostly the pause before and after."""
    padded = np.concatenate([np.zeros(16_000, np.float32), spoken, np.zeros(16_000, np.float32)])
    assert SPOKEN in provider.transcribe(padded, language=None).text.lower()


@pytest.mark.model
def test_audio_resampled_the_way_capture_does_it_still_transcribes(provider, spoken):
    """The microphone runs at 48kHz and every block is converted alone.

    Each block is interpolated independently and its length rounded up, so
    the seams between blocks are where a resampling fault would show. The
    words have to survive the trip.
    """
    at_48k = resample(spoken, 16_000, 48_000)

    whole = mono_samples(PCMBuffer(at_48k.reshape(1, -1), 48_000.0), 16_000)
    assert SPOKEN in provider.transcribe(whole, language=None).text.lower()

    block_size = 1024
    blocks: list[float] = []
    for start in range(0, at_48k.size, block_size):
        chunk = at_48k[start : start + block_size]
        blocks.extend(mono_samples(PCMBuffer(chunk.reshape(1, -1), 48_000.0), 16_000))
    assert SPOKEN in provider.transcribe(blocks, language=None).text.lower()


@pytest.mark.model
def test_a_stereo_microphone_is_mixed_down_without_losing_the_words(provider, spoken):
    """`channels = min(2, ...)`, so a stereo device is the common case."""
    stereo = np.vstack([spoken, spoken])
    mixed = mono_samples(PCMBuffer(stereo, 16_000.0), 16_000)
    assert SPOKEN in provider.transcribe(mixed, language=None).text.lower()


@pytest.mark.model
def test_the_whole_service_turns_captured_audio_into_a_transcript(settings, spoken):
    """The pipeline as the app runs it, not the provider on its own."""
    from fluentry.services.asr_service import ASRService

    service = ASRService(settings=settings, provider=make_speech_provider(
        SpeechModel.default_model()
    ))
    outcome = service.process_samples(list(spoken))
    assert outcome.error is None, outcome.error
    assert outcome.raw_text.strip(), "the model produced nothing at all"
    assert SPOKEN in outcome.final_text.lower()
