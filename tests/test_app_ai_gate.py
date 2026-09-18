"""The app only sends a dictation to a provider it has verified.

These run against a real `AppState` with no services started, so they
exercise the wiring between the app, the routing rules and the LLM client —
not a stand-in for it.
"""

from __future__ import annotations

import json

import pytest

from fluentry.app import AppState
from fluentry.persistence.history_store import TranscriptionHistoryStore
from fluentry.services.llm_client import HTTPResponse, LLMClient, LLMError, Transport
from fluentry.services.provider_routing import provider_fingerprint, provider_key
from fluentry.services.text_pipeline import PipelineContext

OPENAI_BASE = "https://api.openai.com/v1"


class RecordingTransport(Transport):
    """Answers in whichever shape the request asked for.

    A provider that is sent `"stream": true` replies with server-sent
    events, so a fake that always returns a whole JSON body would let a
    broken streaming path pass.
    """

    def __init__(self, content: str = "Enhanced text.") -> None:
        self.requests: list[tuple[str, dict[str, str], dict]] = []
        self.content = content

    def send(self, url, headers, body, timeout, stream):
        payload = json.loads(body.decode("utf-8"))
        self.requests.append((url, headers, payload))
        if payload.get("stream"):
            chunk = {"choices": [{"index": 0, "delta": {"content": self.content}}]}
            return HTTPResponse(
                status_code=200, lines=[f"data: {json.dumps(chunk)}", "data: [DONE]"]
            )
        whole = {"choices": [{"message": {"content": self.content}}]}
        return HTTPResponse(status_code=200, lines=[json.dumps(whole)])


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def app(settings, transport) -> AppState:
    state = AppState(
        settings=settings,
        history=TranscriptionHistoryStore(load=False),
        start_services=False,
    )
    state.llm = LLMClient(transport=transport)
    return state


def configure(app: AppState, api_key: str = "sk-test", verified: bool = True) -> None:
    app.settings.selected_provider_id = "openai"
    app.settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    app.keychain.store_key(api_key, "openai")
    if verified:
        fingerprints = app.settings.verified_provider_fingerprints
        fingerprints[provider_key("openai")] = provider_fingerprint(OPENAI_BASE, api_key)
        app.settings.verified_provider_fingerprints = fingerprints


def test_a_verified_provider_receives_the_dictation(app, transport):
    configure(app)
    assert app._enhance("hello there", PipelineContext()) == "Enhanced text."

    url, headers, body = transport.requests[0]
    assert url.startswith(OPENAI_BASE)
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][-1]["content"] == "hello there"


def test_an_unverified_provider_is_never_contacted(app, transport):
    configure(app, verified=False)
    with pytest.raises(LLMError):
        app._enhance("hello there", PipelineContext())
    assert transport.requests == []


def test_a_key_changed_since_verification_is_never_used(app, transport):
    configure(app)
    app.keychain.store_key("sk-rotated", "openai")
    with pytest.raises(LLMError):
        app._enhance("hello there", PipelineContext())
    assert transport.requests == []


def test_storing_a_new_key_drops_the_stale_verification(app):
    configure(app)
    app.store_api_key("sk-rotated")
    assert provider_key("openai") not in app.settings.verified_provider_fingerprints


def test_no_provider_at_all_is_never_contacted(app, transport):
    with pytest.raises(LLMError):
        app._enhance("hello there", PipelineContext())
    assert transport.requests == []


def test_verifying_records_the_endpoint_and_key_together(app):
    app.settings.selected_provider_id = "openai"
    app.settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    app.settings.selected_model = "gpt-4o-mini"
    app.keychain.store_key("sk-test", "openai")

    assert app.verify_provider().startswith("Connected.")
    stored = app.settings.verified_provider_fingerprints[provider_key("openai")]
    assert stored == provider_fingerprint(OPENAI_BASE, "sk-test")
    # The fingerprint is a hash, never the key itself.
    assert "sk-test" not in stored

    assert app._enhance("hello there", PipelineContext()) == "Enhanced text."


def test_a_per_app_prompt_provider_overrides_the_global_one(app, transport):
    from fluentry.persistence.settings_types import (
        DictationPromptConfiguration,
        DictationPromptSelection,
    )

    configure(app)
    app.keychain.store_key("gsk-test", "groq")
    groq_base = "https://api.groq.com/openai/v1"
    fingerprints = app.settings.verified_provider_fingerprints
    fingerprints[provider_key("groq")] = provider_fingerprint(groq_base, "gsk-test")
    app.settings.verified_provider_fingerprints = fingerprints

    app.settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="groq", model_name="llama-3.1-8b"),
        DictationPromptSelection.DEFAULT,
    )

    app._enhance("hello there", PipelineContext())
    url, headers, body = transport.requests[0]
    assert url.startswith(groq_base)
    assert body["model"] == "llama-3.1-8b"
    assert headers["Authorization"] == "Bearer gsk-test"


# --- the streaming preference (upstream PR #296) ----------------------------


def test_the_streaming_preference_reaches_the_provider(app, transport):
    """Some providers stream unless told otherwise; the flag is always sent."""
    configure(app)
    app.settings.enable_ai_streaming = True
    assert app._enhance("hello there", PipelineContext()) == "Enhanced text."
    assert transport.requests[-1][2]["stream"] is True


def test_turning_streaming_off_asks_for_a_whole_response(app, transport):
    configure(app)
    app.settings.enable_ai_streaming = False
    assert app._enhance("hello there", PipelineContext()) == "Enhanced text."
    assert transport.requests[-1][2]["stream"] is False
