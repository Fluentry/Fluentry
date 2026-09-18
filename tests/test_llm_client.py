"""Port of LLMClientRequestBodyTests, StripThinkingTagsTests and TemperatureSupportTests."""

import json

import pytest

from fluentry.persistence.settings_store import SettingsStore
from fluentry.services.llm_client import (
    CancellationError,
    EncodingError,
    HTTPResponse,
    HTTPStatusError,
    InvalidRequestError,
    InvalidResponseError,
    InvalidURLError,
    LLMClient,
    LLMConfig,
    NetworkError,
    RequestTimeoutError,
    Transport,
    should_retry_without_streaming,
)


class FixtureTransport(Transport):
    """Serves a canned SSE or JSON body, the way the Swift tests use URLProtocol."""

    def __init__(self, body: str, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.requests: list[dict] = []

    def send(self, url, headers, body, timeout, stream):
        self.requests.append(
            {"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout, "stream": stream}
        )
        if stream:
            return HTTPResponse(self.status_code, self.body.splitlines())
        return HTTPResponse(self.status_code, [self.body])


def config(streaming: bool = False, **overrides) -> LLMConfig:
    base = dict(
        messages=[{"role": "user", "content": "hello"}],
        model="llama3",
        base_url="http://localhost:11434/v1",
        api_key="",
        streaming=streaming,
    )
    base.update(overrides)
    return LLMConfig(**base)


# --- streaming fallback policy ---------------------------------------------


def test_dictation_streaming_fallback_skips_transport_failures_and_cancellation():
    assert not should_retry_without_streaming(NetworkError("network is unreachable"))
    assert not should_retry_without_streaming(CancellationError())
    assert not should_retry_without_streaming(InvalidRequestError("missing prompt"))
    assert not should_retry_without_streaming(RequestTimeoutError(30))
    assert not should_retry_without_streaming(InvalidURLError())
    assert not should_retry_without_streaming(EncodingError())


def test_dictation_streaming_fallback_retries_protocol_failure():
    assert should_retry_without_streaming(InvalidResponseError())
    assert should_retry_without_streaming(HTTPStatusError(400, "streaming unsupported"))


# --- request bodies ---------------------------------------------------------


def test_chat_completions_body_stream_false_key_is_present_and_false():
    body = LLMClient().build_chat_completions_body(config(streaming=False))
    assert "stream" in body, "an absent stream key breaks Ollama-compatible providers"
    assert body["stream"] is False


def test_chat_completions_body_stream_true_key_is_present_and_true():
    assert LLMClient().build_chat_completions_body(config(streaming=True))["stream"] is True


def test_responses_body_stream_false_key_is_present_and_false():
    body = LLMClient().build_responses_body(config(streaming=False))
    assert "stream" in body
    assert body["stream"] is False


def test_responses_body_stream_true_key_is_present_and_true():
    assert LLMClient().build_responses_body(config(streaming=True))["stream"] is True


def test_chat_completions_body_carries_tools_temperature_and_token_limit():
    tools = [{"type": "function", "function": {"name": "run", "parameters": {"type": "object"}}}]
    body = LLMClient().build_chat_completions_body(
        config(tools=tools, temperature=0.2, max_tokens=512)
    )
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 512


def test_reasoning_models_use_max_completion_tokens():
    body = LLMClient().build_chat_completions_body(config(model="gpt-5", max_tokens=512))
    assert body["max_completion_tokens"] == 512
    assert "max_tokens" not in body


def test_nemotron_models_request_thinking_explicitly():
    body = LLMClient().build_chat_completions_body(config(model="nemotron-4-340b"))
    assert body["enable_thinking"] is True


def test_responses_body_maps_reasoning_effort_and_tools():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run",
                "description": "Run it",
                "parameters": {"type": "object"},
            },
        }
    ]
    body = LLMClient().build_responses_body(
        config(tools=tools, extra_parameters={"reasoning_effort": "high"}, max_tokens=256)
    )
    assert body["reasoning"] == {"effort": "high"}
    assert body["tools"] == [
        {
            "type": "function",
            "name": "run",
            "parameters": {"type": "object"},
            "strict": False,
            "description": "Run it",
        }
    ]
    assert body["max_output_tokens"] == 256
    assert body["store"] is False


def test_responses_input_converts_tool_results():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
        {"role": "assistant", "content": ""},
    ]
    assert LLMClient.responses_input(messages) == [
        {"role": "user", "content": "hi"},
        {"type": "function_call_output", "call_id": "call_1", "output": "done"},
    ]


# --- endpoints --------------------------------------------------------------


def test_endpoint_resolution_appends_chat_completions_once():
    client = LLMClient()
    assert client.endpoint("http://localhost:11434/v1", False) == "http://localhost:11434/v1/chat/completions"
    assert client.endpoint("http://localhost:11434/v1/", False) == "http://localhost:11434/v1/chat/completions"
    already = "http://localhost:11434/v1/chat/completions"
    assert client.endpoint(already, False) == already
    assert client.endpoint("http://localhost:11434/api/chat", False) == "http://localhost:11434/api/chat"


def test_responses_api_only_for_openai_reasoning_models():
    client = LLMClient()
    assert client.should_use_responses_api(config(model="gpt-5", base_url="https://api.openai.com/v1"), "https://api.openai.com/v1")
    assert not client.should_use_responses_api(config(model="gpt-4o", base_url="https://api.openai.com/v1"), "https://api.openai.com/v1")
    assert not client.should_use_responses_api(config(model="gpt-5", base_url="http://localhost:1234/v1"), "http://localhost:1234/v1")
    assert client.should_use_responses_api(config(model="anything", base_url="https://x.test/v1/responses"), "https://x.test/v1/responses")


def test_blank_base_url_is_refused_rather_than_defaulting_to_openai():
    with pytest.raises(InvalidURLError):
        LLMClient().build_request(config(base_url="   "))


def test_api_key_is_sent_whenever_present():
    _, headers, _ = LLMClient().build_request(config(api_key="sk-test"))
    assert headers["Authorization"] == "Bearer sk-test"
    _, headers, _ = LLMClient().build_request(config(api_key=""))
    assert "Authorization" not in headers


# --- streaming --------------------------------------------------------------

SEPARATE_REASONING_FIXTURE = (
    'data: {"choices":[{"index":0,"delta":{"reasoning_content":"I should inspect the current directory.",'
    '"content":"","tool_calls":[{"index":0,"id":"call_445","type":"function","function":'
    '{"name":"run_terminal","arguments":"{\\"command\\":\\""}}]}}]}\n'
    '\n'
    'data: {"choices":[{"index":0,"delta":{"content":"","tool_calls":[{"index":0,"function":'
    '{"arguments":"pwd\\"}"}}]},"finish_reason":"tool_calls"}]}\n'
    '\n'
    'data: [DONE]\n'
)

TAG_PARSER_FIXTURE = (
    'data: {"choices":[{"index":0,"delta":{"content":"<think>Inspecting.</think>Ready.",'
    '"tool_calls":[{"index":0,"id":"call_tag_control","type":"function","function":'
    '{"name":"run_terminal","arguments":"{\\"command\\":\\""}}]}}]}\n'
    '\n'
    'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":'
    '{"arguments":"pwd\\"}"}}]},"finish_reason":"tool_calls"}]}\n'
    '\n'
    'data: [DONE]\n'
)


def test_reasoning_content_delta_preserves_chunked_tool_call():
    """Regression: an empty `content` alongside a tool-call fragment must not skip it."""
    client = LLMClient(transport=FixtureTransport(SEPARATE_REASONING_FIXTURE))
    response = client.call(
        config(streaming=True, model="qwen3.5:9b", base_url="https://issue-445.test/v1", max_retries=1)
    )

    assert response.thinking == "I should inspect the current directory."
    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_445"
    assert response.tool_calls[0].name == "run_terminal"
    assert response.tool_calls[0].get_string("command") == "pwd"


def test_tag_based_reasoning_still_preserves_chunked_tool_call():
    client = LLMClient(transport=FixtureTransport(TAG_PARSER_FIXTURE))
    response = client.call(
        config(
            streaming=True,
            model="qwen-thinking",
            base_url="https://issue-445.test/tag-parser/v1",
            max_retries=1,
        )
    )

    assert response.thinking == "Inspecting."
    assert response.content == "Ready."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_tag_control"
    assert response.tool_calls[0].get_string("command") == "pwd"


def test_streaming_invokes_content_callbacks():
    seen: list[str] = []
    client = LLMClient(transport=FixtureTransport(TAG_PARSER_FIXTURE))
    response = client.call(
        config(
            streaming=True,
            model="qwen-thinking",
            base_url="https://issue-445.test/tag-parser/v1",
            max_retries=1,
            on_content_chunk=seen.append,
        )
    )
    assert response.content == "Ready."
    assert "".join(seen) == "Ready."


def test_streaming_surfaces_http_errors_with_the_body():
    client = LLMClient(transport=FixtureTransport("upstream exploded", status_code=500))
    with pytest.raises(HTTPStatusError) as error:
        client.call(config(streaming=True, max_retries=1))
    assert error.value.status_code == 500
    assert "upstream exploded" in str(error.value)


def test_non_streaming_parses_message_and_tool_calls():
    payload = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": "<think>weighing</think>Done.",
                        "tool_calls": [
                            {
                                "id": "call_9",
                                "function": {"name": "run", "arguments": '{"command":"ls"}'},
                            }
                        ],
                    }
                }
            ]
        }
    )
    client = LLMClient(transport=FixtureTransport(payload))
    response = client.call(config(streaming=False, max_retries=1))

    assert response.thinking == "weighing"
    assert response.content == "Done."
    assert response.tool_calls[0].name == "run"
    assert response.tool_calls[0].get_string("command") == "ls"


def test_cancellation_is_checked_before_sending():
    client = LLMClient(transport=FixtureTransport(TAG_PARSER_FIXTURE))
    with pytest.raises(CancellationError):
        client.call(config(streaming=True, max_retries=1, is_cancelled=lambda: True))


# --- thinking tags ----------------------------------------------------------


def test_stray_close_after_real_think_block_keeps_content():
    thinking, content = LLMClient().strip_thinking_tags(
        "<think>reasoning here</think>The literal </think> tag is stray."
    )
    assert thinking == "reasoning here"
    assert "The literal" in content
    assert "tag is stray" in content


def test_orphan_close_without_opening_tag_still_splits():
    thinking, content = LLMClient().strip_thinking_tags("reasoning here</think>Hello world.")
    assert thinking == "reasoning here"
    assert content == "Hello world."


def test_orphan_close_without_opening_tag_supports_long_form_tag():
    thinking, content = LLMClient().strip_thinking_tags("thoughts</thinking>response")
    assert thinking == "thoughts"
    assert content == "response"


def test_proper_think_block_is_unchanged():
    thinking, content = LLMClient().strip_thinking_tags("<think>reasoning here</think>Hello world.")
    assert thinking == "reasoning here"
    assert content == "Hello world."


def test_plain_content_with_no_tags_is_unchanged():
    thinking, content = LLMClient().strip_thinking_tags("Just a normal response with no tags at all.")
    assert thinking == ""
    assert content == "Just a normal response with no tags at all."


# --- temperature support ----------------------------------------------------


def test_temperature_unsupported_newer_anthropic_models():
    for model in [
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-mythos-5",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-4.7",
        "anthropic/claude-opus-4.8",
        "anthropic/claude-opus-4.8-fast",
    ]:
        assert SettingsStore.is_temperature_unsupported(model), model


def test_temperature_unsupported_openai_reasoning_models():
    for model in ["o1", "o3-mini", "gpt-5", "openai/gpt-oss-120b"]:
        assert SettingsStore.is_temperature_unsupported(model), model


def test_is_reasoning_model_does_not_match_every_openai_model():
    for model in [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "chatgpt-4o-latest",
        "openai/gpt-4o",
        "openai/gpt-4.1",
        "openai/gpt-4o-mini",
        "openai/chatgpt-4o-latest",
    ]:
        assert not SettingsStore.is_reasoning_model(model), model

    for model in [
        "o1", "o3-mini", "o4-mini", "gpt-5", "gpt-5-mini",
        "openai/o3", "openai/o3-mini", "openai/gpt-5", "openai/gpt-oss-120b",
        "deepseek/deepseek-reasoner",
    ]:
        assert SettingsStore.is_reasoning_model(model), model


def test_temperature_supported_older_and_non_anthropic_models():
    for model in [
        "gpt-4.1",
        "claude-sonnet-4-6",
        "claude-sonnet-4-20250514",
        "gemini-2.5-flash",
        "llama3",
        "openai/gpt-4o",
        "openai/gpt-4.1",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-opus-4.5",
    ]:
        assert not SettingsStore.is_temperature_unsupported(model), model
