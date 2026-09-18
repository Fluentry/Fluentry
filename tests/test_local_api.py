"""The loopback HTTP API.

Exercised through the router (which is where every rule lives) and once
end-to-end over a real socket, to prove the server binds loopback-only and
speaks HTTP.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from fluentry.persistence.defaults import Defaults
from fluentry.persistence.history_entry import TranscriptionHistoryEntry
from fluentry.persistence.settings_store import SettingsStore
from fluentry.services.localapi.models import (
    DEFAULT_PORT,
    MAX_REQUEST_BYTES,
    Configuration,
    Request,
    bounded_limit,
)
from fluentry.services.localapi.router import LocalAPIRouter
from fluentry.services.localapi.server import LocalAPIServer
from fluentry.services.vocabulary_store import VocabularyStore


class FakeHistory:
    def __init__(self, entries=None, fails: bool = False) -> None:
        self.entries = entries or []
        self.fails = fails

    def wait_until_loaded(self, timeout: float = 60.0) -> None:
        if self.fails:
            raise TimeoutError("history never loaded")


@dataclass
class FakeDictionary:
    entries: list = field(default_factory=list)


@dataclass
class FakeApp:
    settings: SettingsStore
    vocabulary: VocabularyStore
    history: FakeHistory = field(default_factory=FakeHistory)
    dictionary: FakeDictionary = field(default_factory=FakeDictionary)
    provider: object | None = None
    pipeline: object | None = None
    invalidations: int = 0

    def invalidate_dictionary_cache(self) -> None:
        self.invalidations += 1
        self.dictionary.entries = self.settings.custom_dictionary_entries


@pytest.fixture
def app(tmp_path) -> FakeApp:
    return FakeApp(
        settings=SettingsStore(defaults=Defaults()),
        vocabulary=VocabularyStore(path=tmp_path / "vocabulary.json"),
    )


@pytest.fixture
def router(app: FakeApp) -> LocalAPIRouter:
    return LocalAPIRouter(app, version="1.6.0")


def get(router: LocalAPIRouter, path: str, **query):
    return router.route(Request(method="GET", path=path, query=query))


def post(router: LocalAPIRouter, path: str, payload, json_body: bool = True):
    body = json.dumps(payload).encode("utf-8") if json_body else payload
    headers = {"content-type": "application/json"} if json_body else {}
    return router.route(Request(method="POST", path=path, headers=headers, body=body))


def body_of(response):
    return json.loads(response.body.decode("utf-8"))


# --- configuration ----------------------------------------------------------


def test_the_api_is_disabled_until_it_is_switched_on():
    defaults = Defaults()
    assert Configuration.current(defaults) == Configuration(enabled=False, port=DEFAULT_PORT)


def test_a_stored_port_is_used_and_a_nonsense_one_is_not():
    defaults = Defaults()
    defaults.set("LocalAPIEnabled", True)
    defaults.set("LocalAPIPort", 51000)
    assert Configuration.current(defaults) == Configuration(enabled=True, port=51000)

    defaults.set("LocalAPIPort", 99999)
    assert Configuration.current(defaults).port == DEFAULT_PORT


def test_the_request_size_cap_is_half_a_gigabyte():
    assert MAX_REQUEST_BYTES == 500 * 1024 * 1024


def test_bounded_limit_clamps_into_range():
    def limit(raw):
        return bounded_limit(Request(method="GET", path="/", query={"limit": raw}))

    assert limit("5") == 5
    assert limit("0") == 1
    assert limit("100000") == 1000
    assert limit("nonsense") == 100
    assert bounded_limit(Request(method="GET", path="/")) == 100


# --- routing ----------------------------------------------------------------


def test_health_reports_the_running_version(router):
    response = get(router, "/v1/health")
    assert response.status == 200
    assert body_of(response) == {"status": "ok", "version": "1.6.0"}
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"


def test_an_unknown_path_is_a_404(router):
    response = get(router, "/v1/nope")
    assert response.status == 404
    assert body_of(response) == {"error": "Route not found."}


def test_a_known_path_with_the_wrong_method_is_a_405(router):
    response = post(router, "/v1/health", {})
    assert response.status == 405
    assert body_of(response) == {"error": "Method not allowed."}


# --- history ----------------------------------------------------------------


def make_entry(text: str) -> TranscriptionHistoryEntry:
    return TranscriptionHistoryEntry(
        raw_text=text,
        processed_text=text.upper(),
        timestamp=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc),
        app_name="Editor",
        window_title="notes.txt",
    )


def test_history_returns_entries_newest_first_within_the_limit(app, router):
    app.history.entries = [make_entry(f"entry {index}") for index in range(5)]
    response = get(router, "/v1/history", limit="2")
    payload = body_of(response)
    assert response.status == 200
    assert payload["count"] == 2
    assert [item["rawText"] for item in payload["items"]] == ["entry 0", "entry 1"]


def test_history_exposes_both_the_api_and_storage_field_names(app, router):
    app.history.entries = [make_entry("hello")]
    item = body_of(get(router, "/v1/history"))["items"][0]
    assert item["originalText"] == item["rawText"] == "hello"
    assert item["finalText"] == item["processedText"] == "HELLO"
    assert item["timestamp"].startswith("2026-09-18T12:00:00")


def test_history_that_cannot_load_answers_503(app, router):
    app.history.fails = True
    response = get(router, "/v1/history")
    assert response.status == 503
    assert "History is unavailable" in body_of(response)["error"]


# --- dictionary replacements ------------------------------------------------


def test_replacements_round_trip(app, router):
    post(
        router,
        "/v1/dictionary/replacements",
        {"entries": [{"triggers": ["fluid boys"], "replacement": "Fluentry"}]},
    )
    payload = body_of(get(router, "/v1/dictionary/replacements"))
    assert payload["count"] == 1
    assert payload["items"][0]["triggers"] == ["fluid boys"]
    assert payload["items"][0]["replacement"] == "Fluentry"


def test_a_whitespace_replacement_is_kept_and_an_empty_one_is_rejected(app, router):
    response = post(
        router,
        "/v1/dictionary/replacements",
        {
            "mode": "replace",
            "entries": [
                {"triggers": ["new line"], "replacement": "\n"},
                {"triggers": ["empty"], "replacement": ""},
            ],
        },
    )
    assert response.status == 200
    entries = app.settings.custom_dictionary_entries
    assert len(entries) == 1
    assert entries[0].replacement == "\n"


def test_an_entry_without_triggers_is_rejected(app, router):
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": [], "replacement": "x"}]})
    assert app.settings.custom_dictionary_entries == []


def test_append_is_the_default_mode(app, router):
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["a"], "replacement": "A"}]})
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["b"], "replacement": "B"}]})
    assert {entry.replacement for entry in app.settings.custom_dictionary_entries} == {"A", "B"}


def test_replace_mode_discards_what_was_there(app, router):
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["a"], "replacement": "A"}]})
    post(
        router,
        "/v1/dictionary/replacements",
        {"mode": "replace", "entries": [{"triggers": ["b"], "replacement": "B"}]},
    )
    assert [entry.replacement for entry in app.settings.custom_dictionary_entries] == ["B"]


def test_a_repeated_replacement_replaces_the_old_one_case_insensitively(app, router):
    post(
        router,
        "/v1/dictionary/replacements",
        {"entries": [{"triggers": ["old"], "replacement": "Fluentry"}]},
    )
    post(
        router,
        "/v1/dictionary/replacements",
        {"entries": [{"triggers": ["new"], "replacement": "fluentry"}]},
    )
    entries = app.settings.custom_dictionary_entries
    assert len(entries) == 1
    assert entries[0].triggers == ["new"]


def test_new_entries_are_matched_before_older_ones(app, router):
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["a"], "replacement": "A"}]})
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["b"], "replacement": "B"}]})
    assert app.settings.custom_dictionary_entries[0].replacement == "B"


def test_a_single_entry_may_be_posted_without_the_entries_wrapper(app, router):
    response = post(
        router, "/v1/dictionary/replacements", {"triggers": ["btw"], "replacement": "by the way"}
    )
    assert response.status == 200
    assert app.settings.custom_dictionary_entries[0].replacement == "by the way"


def test_a_payload_with_neither_form_is_rejected(app, router):
    response = post(router, "/v1/dictionary/replacements", {"mode": "append"})
    assert response.status == 400
    assert "Expected entries or triggers/replacement." in body_of(response)["error"]


def test_an_unknown_mode_is_rejected(app, router):
    response = post(router, "/v1/dictionary/replacements", {"mode": "obliterate", "entries": []})
    assert response.status == 400


def test_writing_replacements_refreshes_the_matcher(app, router):
    post(router, "/v1/dictionary/replacements", {"entries": [{"triggers": ["a"], "replacement": "A"}]})
    assert app.invalidations == 1
    assert [entry.replacement for entry in app.dictionary.entries] == ["A"]


# --- custom words -----------------------------------------------------------


def words(router) -> dict[str, dict]:
    """The stored custom words, keyed by text.

    A fresh vocabulary file is seeded with the built-in Fluentry term, so
    these tests look at what changed rather than at absolute counts.
    """
    payload = body_of(get(router, "/v1/dictionary/custom-words"))
    assert payload["count"] == len(payload["items"])
    return {item["text"]: item for item in payload["items"]}


def test_custom_words_round_trip(app, router):
    response = post(
        router,
        "/v1/dictionary/custom-words",
        {"entries": [{"text": "Parakeet", "weight": 2.0, "aliases": ["parakeat"]}]},
    )
    assert response.status == 200
    stored = words(router)["Parakeet"]
    assert stored["weight"] == 2.0
    assert stored["aliases"] == ["parakeat"]


def test_a_blank_custom_word_is_skipped(app, router):
    before = words(router)
    post(router, "/v1/dictionary/custom-words", {"entries": [{"text": "   "}]})
    assert words(router).keys() == before.keys()


def test_the_same_word_twice_keeps_only_the_newer_one(app, router):
    post(router, "/v1/dictionary/custom-words", {"entries": [{"text": "Nemotron", "weight": 1.0}]})
    post(router, "/v1/dictionary/custom-words", {"entries": [{"text": "nemotron", "weight": 3.0}]})
    stored = words(router)
    assert [text for text in stored if text.casefold() == "nemotron"] == ["nemotron"]
    assert stored["nemotron"]["weight"] == 3.0


def test_a_single_custom_word_may_be_posted_without_the_wrapper(app, router):
    response = post(router, "/v1/dictionary/custom-words", {"text": "Cohere"})
    assert response.status == 200
    assert "Cohere" in words(router)


def test_custom_words_replace_mode_clears_the_store(app, router):
    post(router, "/v1/dictionary/custom-words", {"entries": [{"text": "one"}]})
    post(router, "/v1/dictionary/custom-words", {"mode": "replace", "entries": [{"text": "two"}]})
    assert list(words(router)) == ["two"]


# --- inference --------------------------------------------------------------


class StubProvider:
    model_id = "stub"
    is_ready = True

    def prepare(self) -> None:
        return None

    def transcribe(self, samples, language=None):
        from fluentry.services.providers.base import TranscriptionResult

        return TranscriptionResult(text="hello fluid voice", duration_milliseconds=7)

    def release(self) -> None:
        return None


def test_transcribe_reads_a_wav_file_from_disk(app, router, tmp_path):
    from fluentry.services.providers.whisper import write_wav

    audio = tmp_path / "clip.wav"
    write_wav(audio, [0.0] * 1600, sample_rate=16_000)
    app.provider = StubProvider()

    response = post(router, "/v1/transcribe", {"path": str(audio)})
    payload = body_of(response)
    assert response.status == 200
    assert payload["text"] == "hello fluid voice"
    assert payload["sampleCount"] == 1600


def test_transcribe_accepts_base64_audio(app, router, tmp_path):
    import base64

    from fluentry.services.providers.whisper import write_wav

    audio = tmp_path / "clip.wav"
    write_wav(audio, [0.0] * 800, sample_rate=16_000)
    app.provider = StubProvider()

    response = post(
        router,
        "/v1/transcribe",
        {
            "audioBase64": base64.b64encode(audio.read_bytes()).decode("ascii"),
            "filename": "clip.wav",
        },
    )
    assert response.status == 200
    assert body_of(response)["sampleCount"] == 800


def test_transcribe_without_audio_is_a_400(app, router):
    app.provider = StubProvider()
    response = post(router, "/v1/transcribe", {})
    assert response.status == 400
    assert "Missing audio path or audioBase64." in body_of(response)["error"]


def test_transcribe_with_no_model_says_so(app, router, tmp_path):
    from fluentry.services.providers.whisper import write_wav

    audio = tmp_path / "clip.wav"
    write_wav(audio, [0.0] * 160, sample_rate=16_000)
    response = post(router, "/v1/transcribe", {"path": str(audio)})
    assert response.status == 400
    assert "No speech model is ready." in body_of(response)["error"]


def test_postprocess_runs_the_text_pipeline(app, router):
    from fluentry.services.custom_dictionary import CustomDictionary
    from fluentry.services.text_pipeline import TextPipeline

    app.settings.auto_convert_punctuation_enabled = True
    app.pipeline = TextPipeline(app.settings, CustomDictionary([]))

    # Spoken punctuation needs its prefix, so "comma" on its own stays a word.
    response = post(router, "/v1/postprocess", {"text": "hello literal comma world"})
    assert response.status == 200
    assert body_of(response)["text"] == "hello, world"


def test_postprocess_accepts_a_plain_text_body(app, router):
    from fluentry.services.custom_dictionary import CustomDictionary
    from fluentry.services.text_pipeline import TextPipeline

    app.pipeline = TextPipeline(app.settings, CustomDictionary([]))
    response = router.route(
        Request(method="POST", path="/v1/postprocess", body="plain words".encode("utf-8"))
    )
    assert response.status == 200
    assert body_of(response)["text"] == "plain words"


def test_postprocess_rejects_a_malformed_json_body(app, router):
    response = router.route(
        Request(
            method="POST",
            path="/v1/postprocess",
            headers={"content-type": "application/json"},
            body=b"{nope",
        )
    )
    assert response.status == 400
    assert "Invalid JSON text payload." in body_of(response)["error"]


# --- the socket -------------------------------------------------------------


def test_the_server_answers_over_loopback(app):
    server = LocalAPIServer(LocalAPIRouter(app, version="1.6.0"), port=0)
    server.start()
    try:
        port = server.bound_port
        assert server.is_running
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=5) as reply:
            assert reply.status == 200
            assert json.loads(reply.read())["status"] == "ok"

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/dictionary/replacements",
            data=json.dumps({"entries": [{"triggers": ["ok"], "replacement": "Okay"}]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as reply:
            assert json.loads(reply.read())["count"] == 1
    finally:
        server.stop()
    assert not server.is_running


def test_the_server_returns_the_error_status_over_http(app):
    server = LocalAPIServer(LocalAPIRouter(app, version="1.6.0"), port=0)
    server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as problem:
            urllib.request.urlopen(f"http://127.0.0.1:{server.bound_port}/v1/nope", timeout=5)
        assert problem.value.code == 404
    finally:
        server.stop()
