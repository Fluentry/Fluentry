"""Unified LLM transport for every mode (dictation cleanup, command, rewrite).

A port of `LLMClient.swift`: HTTP + SSE streaming, thinking-token extraction,
tool-call assembly and retries. `URLSession` is replaced by a small `Transport`
protocol so tests can inject a fixture stream the way the Swift tests inject a
`URLProtocol`.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence
from urllib.parse import urlparse

from .thinking_parsers import (
    ThinkingParserState,
    create_parser,
    extra_parameters as model_extra_parameters,
    model_family,
)

DEFAULT_TIMEOUT_SECONDS = 30.0


# --- errors ----------------------------------------------------------------


class LLMError(Exception):
    """Base class for every transport-level failure."""


class InvalidURLError(LLMError):
    def __str__(self) -> str:
        return "Invalid URL"


class InvalidResponseError(LLMError):
    def __str__(self) -> str:
        return "Invalid response from LLM"


class HTTPStatusError(LLMError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(status_code, message)
        self.status_code = status_code
        self.message = message

    def __str__(self) -> str:
        return f"HTTP {self.status_code}: {self.message.strip()}"


class NetworkError(LLMError):
    def __init__(self, cause: BaseException | str) -> None:
        super().__init__(cause)
        self.cause = cause

    def __str__(self) -> str:
        return user_facing_network_message(self.cause)


class EncodingError(LLMError):
    def __str__(self) -> str:
        return "Failed to encode request"


class RequestTimeoutError(LLMError):
    def __init__(self, seconds: float) -> None:
        super().__init__(seconds)
        self.seconds = seconds

    def __str__(self) -> str:
        return f"Request timed out after {int(self.seconds)} seconds"


class InvalidRequestError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class CancellationError(Exception):
    """Raised when the caller cancels an in-flight request."""


def user_facing_network_message(cause: BaseException | str) -> str:
    text = str(cause).lower()
    if "name or service not known" in text or "nodename nor servname" in text:
        return "Network error: could not find API host."
    if "connection refused" in text or "failed to establish" in text:
        return "Network error: could not connect to API host."
    if "timed out" in text or "timeout" in text:
        return "Network error: request timed out."
    if "connection reset" in text or "connection aborted" in text:
        return "Network error: connection dropped during the request."
    if "certificate" in text or "ssl" in text:
        return "Network error: TLS certificate validation failed."
    if "network is unreachable" in text or "no route to host" in text:
        return "Network error: no internet connection."
    return f"Network error: {cause}"


# --- retry / fallback policy ------------------------------------------------


def should_retry_without_streaming(error: BaseException) -> bool:
    """Whether a failed streaming attempt is worth retrying unstreamed.

    Transport failures and cancellation are not: the same request would fail
    the same way. A protocol-level failure often means the provider simply
    does not support SSE for this model.
    """
    if isinstance(error, (CancellationError, NetworkError, RequestTimeoutError)):
        return False
    if isinstance(error, (InvalidURLError, EncodingError, InvalidRequestError)):
        return False
    if isinstance(error, (InvalidResponseError, HTTPStatusError)):
        return True
    return not isinstance(error, LLMError)


RETRYABLE_NETWORK_MARKERS = (
    "not connected",
    "timed out",
    "connection reset",
    "connection aborted",
    "name or service not known",
    "connection refused",
    "temporary failure in name resolution",
)


def is_retryable_network_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in RETRYABLE_NETWORK_MARKERS)


# --- values -----------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def get_string(self, key: str) -> str | None:
        value = self.arguments.get(key)
        return value if isinstance(value, str) else None

    def get_optional_string(self, key: str) -> str | None:
        value = self.get_string(key)
        return value or None


@dataclass(frozen=True)
class LLMResponse:
    thinking: str | None
    content: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass
class LLMConfig:
    messages: list[dict[str, Any]]
    model: str
    base_url: str
    api_key: str = ""
    streaming: bool = True
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    extra_parameters: dict[str, Any] = field(default_factory=dict)
    benchmark_id: str | None = None
    max_retries: int = 3
    retry_delay_ms: int = 200
    timeout_seconds: float | None = None

    on_thinking_start: Callable[[], None] | None = None
    on_thinking_chunk: Callable[[str], None] | None = None
    on_thinking_end: Callable[[], None] | None = None
    on_content_chunk: Callable[[str], None] | None = None
    on_tool_call_start: Callable[[str], None] | None = None
    is_cancelled: Callable[[], bool] | None = None


@dataclass(frozen=True)
class HTTPResponse:
    """What a transport returns: a status plus an iterator of body lines."""

    status_code: int
    lines: Iterable[str]


class Transport:
    def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float, stream: bool
    ) -> HTTPResponse:
        raise NotImplementedError


class RequestsTransport(Transport):
    """Default transport, backed by `requests`."""

    def __init__(self, session=None) -> None:
        self._session = session

    def _ensure_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float, stream: bool
    ) -> HTTPResponse:
        import requests

        session = self._ensure_session()
        try:
            response = session.post(url, headers=headers, data=body, timeout=timeout, stream=stream)
        except requests.exceptions.Timeout as error:
            raise RequestTimeoutError(timeout) from error
        except requests.exceptions.RequestException as error:
            raise NetworkError(error) from error

        if stream:
            lines: Iterable[str] = (
                line.decode("utf-8", "replace") if isinstance(line, bytes) else line
                for line in response.iter_lines()
            )
        else:
            lines = [response.text]
        return HTTPResponse(status_code=response.status_code, lines=lines)


# --- helpers ----------------------------------------------------------------

THINKING_TAG_PATTERN = re.compile(r"<think(?:ing)?>([\s\S]*?)</think(?:ing)?>")
ORPHAN_THINKING_PATTERN = re.compile(r"^([\s\S]*?)</think(?:ing)?>")

STRAY_TAGS = ("</think>", "</thinking>", "<think>", "<thinking>")


def uses_reasoning_completion_token_parameter(model: str) -> bool:
    family = model_family(model)
    return (
        family.startswith("gpt-5")
        or "gpt-5." in family
        or family.startswith("o1")
        or family.startswith("o3")
        or family.startswith("o4")
        or "gpt-oss" in family
        or ("deepseek" in family and "reasoner" in family)
    )


def _new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:8]


class LLMClient:
    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or RequestsTransport()

    # --- entry point ------------------------------------------------------

    def call(self, config: LLMConfig) -> LLMResponse:
        url, headers, body = self.build_request(config)
        timeout = config.timeout_seconds or DEFAULT_TIMEOUT_SECONDS

        last_error: BaseException | None = None
        for attempt in range(1, max(1, config.max_retries) + 1):
            self._check_cancelled(config)
            try:
                if config.streaming:
                    if self.is_responses_endpoint(url):
                        return self._process_responses_streaming(url, headers, body, timeout, config)
                    return self._process_streaming(url, headers, body, timeout, config)
                return self._process_non_streaming(url, headers, body, timeout, config)
            except CancellationError:
                raise
            except (NetworkError, RequestTimeoutError) as error:
                last_error = error
                if attempt >= config.max_retries or not is_retryable_network_error(error):
                    raise
                time.sleep(config.retry_delay_ms / 1000 * attempt)
            except HTTPStatusError as error:
                # 5xx and 429 are worth another attempt; 4xx will not change.
                last_error = error
                retryable = error.status_code >= 500 or error.status_code == 429
                if attempt >= config.max_retries or not retryable:
                    raise
                time.sleep(config.retry_delay_ms / 1000 * attempt)
        raise last_error if last_error else InvalidResponseError()

    def _check_cancelled(self, config: LLMConfig) -> None:
        if config.is_cancelled is not None and config.is_cancelled():
            raise CancellationError()

    # --- request building -------------------------------------------------

    def build_request(self, config: LLMConfig) -> tuple[str, dict[str, str], bytes]:
        base_url = config.base_url.strip()
        if not base_url:
            # Never silently fall back to OpenAI when the user left this blank.
            raise InvalidURLError()

        use_responses_api = self.should_use_responses_api(config, base_url)
        endpoint = self.endpoint(base_url, use_responses_api)
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise InvalidURLError()

        body_dict = (
            self.build_responses_body(config) if use_responses_api else self.build_chat_completions_body(config)
        )
        try:
            body = json.dumps(body_dict).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise EncodingError() from error

        headers = {"Content-Type": "application/json"}
        # Some localhost endpoints still require auth, so send the key whenever one exists.
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return endpoint, headers, body

    @staticmethod
    def _appending_path(path: str, base_url: str) -> str:
        return f"{base_url}{path}" if base_url.endswith("/") else f"{base_url}/{path}"

    def endpoint(self, base_url: str, use_responses_api: bool) -> str:
        if use_responses_api:
            if "/responses" in base_url:
                return base_url
            if "/chat/completions" in base_url:
                return base_url.replace("/chat/completions", "/responses")
            return self._appending_path("responses", base_url)

        if "/chat/completions" in base_url or "/api/chat" in base_url or "/api/generate" in base_url:
            return base_url
        return self._appending_path("chat/completions", base_url)

    def should_use_responses_api(self, config: LLMConfig, base_url: str) -> bool:
        if "/responses" in base_url:
            return True
        host = (urlparse(base_url).hostname or "").lower()
        if host != "api.openai.com":
            return False
        lowered = config.model.lower()
        return (
            lowered.startswith("gpt-5")
            or lowered.startswith("o1")
            or lowered.startswith("o3")
            or lowered.startswith("o4")
        )

    @staticmethod
    def is_responses_endpoint(url: str) -> bool:
        return "/responses" in (urlparse(url).path or "")

    def build_chat_completions_body(self, config: LLMConfig) -> dict[str, Any]:
        body: dict[str, Any] = {"model": config.model, "messages": config.messages}

        if config.temperature is not None:
            body["temperature"] = config.temperature

        if config.tools:
            body["tools"] = config.tools
            body["tool_choice"] = "auto"

        # Always send `stream` explicitly: Ollama-compatible providers treat an
        # absent key as true, which breaks non-streaming callers.
        body["stream"] = config.streaming

        body.update(model_extra_parameters(config.model))
        body.update(config.extra_parameters)

        if config.max_tokens is not None:
            if uses_reasoning_completion_token_parameter(config.model):
                body["max_completion_tokens"] = config.max_tokens
            else:
                body["max_tokens"] = config.max_tokens

        return body

    def build_responses_body(self, config: LLMConfig) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": config.model,
            "input": self.responses_input(config.messages),
            "store": False,
            "stream": config.streaming,
        }

        if config.tools:
            body["tools"] = self.responses_tools(config.tools)
            body["tool_choice"] = "auto"
        if config.max_tokens is not None:
            body["max_output_tokens"] = config.max_tokens
        if config.temperature is not None:
            body["temperature"] = config.temperature

        for key, value in model_extra_parameters(config.model).items():
            self._add_responses_extra_parameter(key, value, body)
        for key, value in config.extra_parameters.items():
            self._add_responses_extra_parameter(key, value, body)

        return body

    @staticmethod
    def _add_responses_extra_parameter(name: str, value: Any, body: dict[str, Any]) -> None:
        if name == "reasoning_effort":
            body["reasoning"] = {"effort": value}
        else:
            body[name] = value

    @staticmethod
    def responses_tools(chat_tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for chat_tool in chat_tools:
            if chat_tool.get("type") != "function":
                continue
            function = chat_tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            if not isinstance(name, str) or not isinstance(parameters, dict):
                continue
            tool: dict[str, Any] = {
                "type": "function",
                "name": name,
                "parameters": parameters,
                "strict": False,
            }
            description = function.get("description")
            if isinstance(description, str):
                tool["description"] = description
            tools.append(tool)
        return tools

    @staticmethod
    def responses_input(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role") or "user"
            if role == "tool":
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id") or "call_unknown",
                        "output": message.get("content") or "",
                    }
                )
                continue
            content = message.get("content")
            if isinstance(content, str) and content:
                result.append({"role": role, "content": content})
        return result

    # --- responses --------------------------------------------------------

    def _read(self, url, headers, body, timeout, config, stream: bool) -> HTTPResponse:
        response = self.transport.send(url, headers, body, timeout, stream)
        if response.status_code >= 400:
            text = "".join(response.lines)
            raise HTTPStatusError(response.status_code, text or "Unknown error")
        return response

    def _process_non_streaming(self, url, headers, body, timeout, config: LLMConfig) -> LLMResponse:
        response = self._read(url, headers, body, timeout, config, stream=False)
        raw = "".join(response.lines)
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise InvalidResponseError() from error
        if not isinstance(payload, dict):
            raise InvalidResponseError()

        if self.is_responses_endpoint(url):
            return self.parse_responses_response(payload)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise InvalidResponseError()
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise InvalidResponseError()
        return self.parse_message_response(message)

    @staticmethod
    def _sse_payloads(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
        for raw_line in lines:
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            if payload.strip() == "[DONE]":
                continue
            try:
                decoded = json.loads(payload)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                yield decoded

    def _process_streaming(self, url, headers, body, timeout, config: LLMConfig) -> LLMResponse:
        response = self._read(url, headers, body, timeout, config, stream=True)
        parser = create_parser(config.model)

        state = ThinkingParserState.INITIAL
        thinking_buffer: list[str] = []
        content_buffer: list[str] = []
        tag_buffer = ""
        uses_separate_reasoning_fields = False

        tool_call_id: str | None = None
        tool_call_name: str | None = None
        tool_call_arguments = ""

        for event in self._sse_payloads(response.lines):
            self._check_cancelled(config)
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta")
            if not isinstance(delta, dict):
                continue

            reasoning = _first_string(
                delta, ("reasoning_content", "reasoning", "thought", "thinking")
            )
            if reasoning is not None:
                uses_separate_reasoning_fields = True
                if state is ThinkingParserState.INITIAL:
                    state = ThinkingParserState.IN_THINKING
                    _invoke(config.on_thinking_start)
                thinking_buffer.append(reasoning)
                _invoke(config.on_thinking_chunk, reasoning)

            content = delta.get("content")
            if isinstance(content, str):
                if uses_separate_reasoning_fields:
                    if state is ThinkingParserState.IN_THINKING:
                        state = ThinkingParserState.IN_CONTENT
                        _invoke(config.on_thinking_end)
                    content_buffer.append(content)
                    _invoke(config.on_content_chunk, content)
                else:
                    previous_state = state
                    result, tag_buffer = parser.process_chunk(content, state, tag_buffer)
                    state = result.state
                    if previous_state is not ThinkingParserState.IN_THINKING and (
                        state is ThinkingParserState.IN_THINKING
                    ):
                        _invoke(config.on_thinking_start)
                    if previous_state is ThinkingParserState.IN_THINKING and (
                        state is ThinkingParserState.IN_CONTENT
                    ):
                        _invoke(config.on_thinking_end)
                    if result.thinking:
                        thinking_buffer.append(result.thinking)
                        _invoke(config.on_thinking_chunk, result.thinking)
                    if result.content:
                        content_buffer.append(result.content)
                        _invoke(config.on_content_chunk, result.content)

            # Tool calls arrive in fragments and are assembled across deltas.
            # This runs regardless of the content branch above: a delta can
            # carry an empty `content` string *and* a tool-call fragment.
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                first = tool_calls[0]
                if isinstance(first, dict):
                    if isinstance(first.get("id"), str):
                        tool_call_id = first["id"]
                    function = first.get("function")
                    if isinstance(function, dict):
                        name = function.get("name")
                        if isinstance(name, str):
                            tool_call_name = name
                            _invoke(config.on_tool_call_start, name)
                        arguments = function.get("arguments")
                        if isinstance(arguments, str):
                            tool_call_arguments += arguments

        if tag_buffer:
            if state is ThinkingParserState.IN_THINKING:
                thinking_buffer.append(tag_buffer)
                _invoke(config.on_thinking_chunk, tag_buffer)
            else:
                content_buffer.append(tag_buffer)
                _invoke(config.on_content_chunk, tag_buffer)

        finalized = parser.finalize(thinking_buffer, content_buffer, state)

        parsed_tool_calls: tuple[ToolCall, ...] = ()
        if tool_call_name:
            arguments = _decode_arguments(tool_call_arguments)
            if arguments is not None:
                parsed_tool_calls = (
                    ToolCall(id=tool_call_id or _new_call_id(), name=tool_call_name, arguments=arguments),
                )

        return LLMResponse(
            thinking=finalized.thinking or None,
            content=finalized.content,
            tool_calls=parsed_tool_calls,
        )

    def _process_responses_streaming(self, url, headers, body, timeout, config: LLMConfig) -> LLMResponse:
        response = self._read(url, headers, body, timeout, config, stream=True)
        content_buffer: list[str] = []
        calls_by_index: dict[int, dict[str, Any]] = {}

        for event in self._sse_payloads(response.lines):
            self._check_cancelled(config)
            event_type = event.get("type")
            if not isinstance(event_type, str):
                continue

            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    content_buffer.append(delta)
                    _invoke(config.on_content_chunk, delta)

            elif event_type in ("response.output_item.added", "response.output_item.done"):
                item = event.get("item")
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                index = event.get("output_index") or 0
                call = calls_by_index.setdefault(index, {"arguments": ""})
                call["id"] = item.get("id") or call.get("id")
                call["call_id"] = item.get("call_id") or call.get("call_id")
                call["name"] = item.get("name") or call.get("name")
                arguments = item.get("arguments")
                if isinstance(arguments, str) and arguments:
                    call["arguments"] = arguments
                if call.get("name"):
                    _invoke(config.on_tool_call_start, call["name"])

            elif event_type == "response.function_call_arguments.delta":
                index = event.get("output_index") or 0
                call = calls_by_index.setdefault(index, {"arguments": ""})
                call["arguments"] = call.get("arguments", "") + (event.get("delta") or "")

            elif event_type == "response.function_call_arguments.done":
                index = event.get("output_index") or 0
                call = calls_by_index.setdefault(index, {"arguments": ""})
                for source in (event, event.get("item") if isinstance(event.get("item"), dict) else {}):
                    call["id"] = source.get("item_id") or source.get("id") or call.get("id")
                    call["call_id"] = source.get("call_id") or call.get("call_id")
                    call["name"] = source.get("name") or call.get("name")
                    if isinstance(source.get("arguments"), str):
                        call["arguments"] = source["arguments"]

        tool_calls: list[ToolCall] = []
        for index in sorted(calls_by_index):
            call = calls_by_index[index]
            name = call.get("name")
            arguments = _decode_arguments(call.get("arguments", ""))
            if not name or arguments is None:
                continue
            tool_calls.append(
                ToolCall(
                    id=call.get("call_id") or call.get("id") or _new_call_id(),
                    name=name,
                    arguments=arguments,
                )
            )

        return LLMResponse(
            thinking=None,
            content="".join(content_buffer).strip(),
            tool_calls=tuple(tool_calls),
        )

    # --- parsing ----------------------------------------------------------

    def parse_responses_response(self, payload: dict[str, Any]) -> LLMResponse:
        output = payload.get("output")
        if not isinstance(output, list):
            raise InvalidResponseError()

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text = part.get("text")
                        if isinstance(text, str):
                            content_parts.append(text)
            elif item_type == "function_call":
                name = item.get("name")
                arguments = _decode_arguments(item.get("arguments") or "")
                if not isinstance(name, str) or arguments is None:
                    continue
                tool_calls.append(
                    ToolCall(
                        id=item.get("call_id") or item.get("id") or _new_call_id(),
                        name=name,
                        arguments=arguments,
                    )
                )

        raw_content = "".join(content_parts)
        thinking, cleaned = self.strip_thinking_tags(raw_content)
        return LLMResponse(
            thinking=thinking or None,
            content=cleaned or raw_content.strip(),
            tool_calls=tuple(tool_calls),
        )

    def parse_message_response(self, message: dict[str, Any]) -> LLMResponse:
        raw_content = message.get("content")
        raw_content = raw_content if isinstance(raw_content, str) else ""

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            arguments = _decode_arguments(function.get("arguments") or "")
            if not isinstance(name, str) or arguments is None:
                continue
            tool_calls.append(
                ToolCall(id=call.get("id") or _new_call_id(), name=name, arguments=arguments)
            )

        thinking, cleaned = self.strip_thinking_tags(raw_content)
        reasoning = _first_string(message, ("reasoning_content", "reasoning", "thought", "thinking"))
        final_thinking = "\n".join(part for part in (thinking, reasoning) if part)

        return LLMResponse(
            thinking=final_thinking or None,
            content=cleaned or raw_content,
            tool_calls=tuple(tool_calls),
        )

    def strip_thinking_tags(self, text: str) -> tuple[str, str]:
        """Split a full response into (thinking, visible content).

        A model that emitted a proper `<think>…</think>` block can still leak a
        stray `</think>` later on. The orphan pass is therefore only applied
        when no opening tag was ever present (the Nemotron shape); otherwise
        the answer text before the stray tag would be reclassified as thinking
        and disappear from the response.
        """
        working = text
        thinking_parts: list[str] = []
        has_opening_tag = "<think>" in text or "<thinking>" in text

        matches = list(THINKING_TAG_PATTERN.finditer(working))
        if matches:
            thinking_parts.extend(match.group(1) for match in matches)
            working = THINKING_TAG_PATTERN.sub("", working)

        if not has_opening_tag:
            orphan = ORPHAN_THINKING_PATTERN.search(working)
            if orphan is not None:
                thinking_parts.append(orphan.group(1))
                working = ORPHAN_THINKING_PATTERN.sub("", working, count=1)

        for tag in STRAY_TAGS:
            working = working.replace(tag, "")

        return "".join(thinking_parts), working.strip()


def _first_string(payload: dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _decode_arguments(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _invoke(callback: Callable | None, *args) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        # A misbehaving UI callback must never abort an in-flight response.
        pass
