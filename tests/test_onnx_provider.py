"""Choosing and running an ONNX speech engine.

The Parakeet exports this app points at use a fused `decoder_joint` model
and a `vocab.txt`, which `onnx-asr` reads and `sherpa-onnx` does not, so the
runtime has to be chosen per model rather than globally.
"""

from __future__ import annotations

import pytest

from fluentry.persistence.speech_model import SpeechBackend, SpeechModel
from fluentry.services.providers.onnx_asr import (
    ONNX_ASR_MODEL_TYPES,
    OnnxAsrProvider,
    SherpaOnnxProvider,
    UnavailableOnnxProvider,
    engine_is_installable,
    make_onnx_provider,
    missing_runtime_message,
    onnx_cache_root,
)

PARAKEET = (SpeechModel.PARAKEET_TDT, SpeechModel.PARAKEET_TDT_V2)


# --- picking a runtime ------------------------------------------------------


def test_parakeet_is_served_by_onnx_asr():
    for model in PARAKEET:
        assert OnnxAsrProvider.supports(model) is True


def test_the_other_onnx_models_are_not_claimed_by_onnx_asr():
    """Their exports are in a layout onnx-asr cannot read."""
    for model in (
        SpeechModel.NEMOTRON_OFFLINE,
        SpeechModel.NEMOTRON_STREAMING,
        SpeechModel.COHERE_TRANSCRIBE_SIX_BIT,
        SpeechModel.PARAKEET_REALTIME,
    ):
        assert OnnxAsrProvider.supports(model) is False


def test_every_claimed_model_has_a_repository_and_a_type():
    for model in ONNX_ASR_MODEL_TYPES:
        assert model.onnx_repository
        assert ONNX_ASR_MODEL_TYPES[model]


def test_the_provider_matches_the_runtime_the_model_needs(monkeypatch):
    monkeypatch.setattr(OnnxAsrProvider, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(SherpaOnnxProvider, "is_available", staticmethod(lambda: True))

    assert isinstance(make_onnx_provider(SpeechModel.PARAKEET_TDT), OnnxAsrProvider)
    assert isinstance(make_onnx_provider(SpeechModel.NEMOTRON_OFFLINE), SherpaOnnxProvider)


def test_a_missing_runtime_yields_a_provider_that_explains_itself(monkeypatch):
    monkeypatch.setattr(OnnxAsrProvider, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(SherpaOnnxProvider, "is_available", staticmethod(lambda: False))

    for model in (SpeechModel.PARAKEET_TDT, SpeechModel.NEMOTRON_OFFLINE):
        provider = make_onnx_provider(model)
        assert isinstance(provider, UnavailableOnnxProvider)
        assert provider.is_ready is False
        with pytest.raises(Exception) as problem:
            provider.prepare()
        assert "pip install" in str(problem.value)


def test_the_message_names_the_runtime_that_model_actually_needs():
    assert "onnx-asr" in missing_runtime_message(SpeechModel.PARAKEET_TDT)
    assert "sherpa-onnx" in missing_runtime_message(SpeechModel.NEMOTRON_OFFLINE)
    # Whisper is always offered as the way out.
    assert "Whisper" in missing_runtime_message(SpeechModel.PARAKEET_TDT)


# --- what the UI asks before offering a download ----------------------------


def test_an_engine_with_no_runtime_is_not_installable(monkeypatch):
    monkeypatch.setattr(OnnxAsrProvider, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(SherpaOnnxProvider, "is_available", staticmethod(lambda: False))
    assert engine_is_installable(SpeechModel.PARAKEET_TDT) is False


def test_an_engine_with_its_runtime_is_installable(monkeypatch):
    monkeypatch.setattr(OnnxAsrProvider, "is_available", staticmethod(lambda: True))
    assert engine_is_installable(SpeechModel.PARAKEET_TDT) is True


def test_whisper_depends_on_a_whisper_runtime(monkeypatch):
    from fluentry.services.providers import whisper as whisper_module

    monkeypatch.setattr(whisper_module, "whisper_runtime_is_available", lambda: False)
    assert engine_is_installable(SpeechModel.WHISPER_SMALL) is False
    monkeypatch.setattr(whisper_module, "whisper_runtime_is_available", lambda: True)
    assert engine_is_installable(SpeechModel.WHISPER_SMALL) is True


def test_every_shipped_model_has_a_runtime_that_can_serve_it():
    """Nothing is offered that no runtime could ever run."""
    for model in SpeechModel:
        assert model.backend is not SpeechBackend.UNSUPPORTED, model.value
        assert model.is_supported, model.value


# --- the download -----------------------------------------------------------


def test_the_weights_land_in_the_app_cache_not_the_global_one(tmp_path, monkeypatch):
    """Uninstalling Fluentry should take its models with it."""
    assert onnx_cache_root() == (tmp_path / "cache" / "fluentry" / "models" / "onnx")


def test_only_the_int8_files_are_fetched():
    patterns = OnnxAsrProvider.SNAPSHOT_PATTERNS
    assert "*.int8.onnx" in patterns
    # The float32 copies would quadruple the download for no CPU benefit.
    assert not any(pattern == "*.onnx" for pattern in patterns)


def test_a_download_failure_names_the_model(monkeypatch):
    from fluentry.services.providers.onnx_asr import ModelDownloadFailed

    provider = OnnxAsrProvider(model=SpeechModel.PARAKEET_TDT)

    def explode(local_files_only):
        raise OSError("the network went away")

    monkeypatch.setattr(provider, "_snapshot", explode)
    with pytest.raises(ModelDownloadFailed) as problem:
        provider.download()
    assert "Parakeet TDT v3" in str(problem.value)
    assert "the network went away" in str(problem.value)


def test_an_absent_model_reports_as_not_downloaded(monkeypatch):
    provider = OnnxAsrProvider(model=SpeechModel.PARAKEET_TDT)
    monkeypatch.setattr(
        provider, "_snapshot", lambda local_files_only: (_ for _ in ()).throw(OSError("nope"))
    )
    assert provider.is_downloaded is False


def test_preparing_without_the_runtime_explains_rather_than_crashes(monkeypatch):
    monkeypatch.setattr(OnnxAsrProvider, "is_available", staticmethod(lambda: False))
    provider = OnnxAsrProvider(model=SpeechModel.PARAKEET_TDT)
    with pytest.raises(Exception) as problem:
        provider.prepare()
    assert "pip install onnx-asr" in str(problem.value)


# --- the real model ---------------------------------------------------------


@pytest.mark.model
def test_parakeet_transcribes_the_fixture():
    """Downloads Parakeet if it is not cached, then runs it for real."""
    from pathlib import Path

    from fluentry.services.providers.whisper import read_wav_as_mono_float

    if not OnnxAsrProvider.is_available():
        pytest.skip("onnx-asr is not installed")

    # The user's real cache, so the weights are fetched at most once here.
    cache = Path.home() / ".cache" / "fluentry" / "models" / "onnx"
    provider = OnnxAsrProvider(model=SpeechModel.PARAKEET_TDT, cache_root=cache)
    if not provider.is_downloaded:
        pytest.skip("Parakeet is not downloaded; run the app to fetch it")

    provider.prepare()
    fixture = Path(__file__).parent / "resources" / "dictation_fixture.wav"
    result = provider.transcribe(read_wav_as_mono_float(fixture), language="en")

    assert "hello" in result.text.lower()
    assert "fluid" in result.text.lower()
    assert result.duration_milliseconds >= 0
