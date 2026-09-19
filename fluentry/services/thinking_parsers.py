"""Parsers for the thinking tokens different model families emit.

Three shapes exist in the wild:

* `<think>…</think>` inline tags (Qwen, most open models),
* thinking with **no** opening tag, using `</think>` purely as a separator
  (Nemotron/Nemo),
* a separate `reasoning_content` field (OpenAI o-series/gpt-5, DeepSeek API).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Characters held back while streaming so a tag split across chunks is not
#: emitted as visible text before it can be recognised.
PARTIAL_TAG_GUARD = 15


class ThinkingParserState(Enum):
    INITIAL = "initial"
    IN_THINKING = "inThinking"
    IN_CONTENT = "inContent"


@dataclass
class ChunkResult:
    state: ThinkingParserState
    thinking: str
    content: str


@dataclass
class FinalizedThinking:
    thinking: str
    content: str


def _find_tag(buffer: str, tags: tuple[str, ...]) -> tuple[int, int] | None:
    for tag in tags:
        index = buffer.find(tag)
        if index != -1:
            return (index, index + len(tag))
    return None


OPEN_TAGS = ("<think>", "<thinking>")
CLOSE_TAGS = ("</think>", "</thinking>")


class ThinkingParser:
    """Interface shared by every parser."""

    def process_chunk(self, chunk: str, state: ThinkingParserState, buffer: str) -> tuple[ChunkResult, str]:
        raise NotImplementedError

    def finalize(
        self, thinking_buffer: list[str], content_buffer: list[str], final_state: ThinkingParserState
    ) -> FinalizedThinking:
        raise NotImplementedError


class StandardThinkingParser(ThinkingParser):
    """`<think>…</think>` / `<thinking>…</thinking>` inline tags."""

    def process_chunk(self, chunk: str, state: ThinkingParserState, buffer: str) -> tuple[ChunkResult, str]:
        buffer += chunk
        thinking_chunk = ""
        content_chunk = ""
        new_state = state

        if new_state is not ThinkingParserState.IN_THINKING:
            open_range = _find_tag(buffer, OPEN_TAGS)
            if open_range is not None:
                start, end = open_range
                before = buffer[:start]
                if before:
                    content_chunk += before
                buffer = buffer[end:]
                new_state = ThinkingParserState.IN_THINKING

        if new_state is ThinkingParserState.IN_THINKING:
            close_range = _find_tag(buffer, CLOSE_TAGS)
            if close_range is not None:
                start, end = close_range
                before_close = buffer[:start]
                if before_close:
                    thinking_chunk += before_close
                buffer = buffer[end:]
                new_state = ThinkingParserState.IN_CONTENT
                if buffer:
                    content_chunk += buffer
                    buffer = ""
            else:
                safe_length = max(0, len(buffer) - PARTIAL_TAG_GUARD)
                if safe_length > 0:
                    thinking_chunk = buffer[:safe_length]
                    buffer = buffer[safe_length:]
        else:
            safe_length = max(0, len(buffer) - PARTIAL_TAG_GUARD)
            if safe_length > 0:
                content_chunk = buffer[:safe_length]
                buffer = buffer[safe_length:]

        return ChunkResult(new_state, thinking_chunk, content_chunk), buffer

    def finalize(
        self, thinking_buffer: list[str], content_buffer: list[str], final_state: ThinkingParserState
    ) -> FinalizedThinking:
        thinking = "".join(thinking_buffer)
        content = "".join(content_buffer)
        for tag in ("</think>", "</thinking>", "<think>", "<thinking>"):
            content = content.replace(tag, "")
        return FinalizedThinking(thinking, content.strip())


class NemoThinkingParser(ThinkingParser):
    """Thinking with no opening tag: `thoughts</think>response`."""

    def process_chunk(self, chunk: str, state: ThinkingParserState, buffer: str) -> tuple[ChunkResult, str]:
        buffer += chunk
        thinking_chunk = ""
        content_chunk = ""
        new_state = state

        if new_state is ThinkingParserState.INITIAL:
            new_state = ThinkingParserState.IN_THINKING

        if new_state is ThinkingParserState.IN_THINKING:
            close_range = _find_tag(buffer, CLOSE_TAGS)
            if close_range is not None:
                start, end = close_range
                before_close = buffer[:start]
                if before_close:
                    thinking_chunk = before_close
                buffer = buffer[end:]
                new_state = ThinkingParserState.IN_CONTENT
                if buffer:
                    content_chunk = buffer
                    buffer = ""
            else:
                safe_length = max(0, len(buffer) - PARTIAL_TAG_GUARD)
                if safe_length > 0:
                    thinking_chunk = buffer[:safe_length]
                    buffer = buffer[safe_length:]
        elif new_state is ThinkingParserState.IN_CONTENT:
            content_chunk = buffer
            buffer = ""

        return ChunkResult(new_state, thinking_chunk, content_chunk), buffer

    def finalize(
        self, thinking_buffer: list[str], content_buffer: list[str], final_state: ThinkingParserState
    ) -> FinalizedThinking:
        thinking = "".join(thinking_buffer)
        content = "".join(content_buffer)
        # Still "in thinking" at the end means no `</think>` ever arrived, so
        # thinking was off and everything the model said is the answer.
        if final_state is ThinkingParserState.IN_THINKING:
            content = thinking + content
            thinking = ""
        for tag in ("</think>", "</thinking>"):
            content = content.replace(tag, "")
        return FinalizedThinking(thinking, content.strip())


class NoThinkingParser(ThinkingParser):
    """Models with no thinking tokens at all."""

    def process_chunk(self, chunk: str, state: ThinkingParserState, buffer: str) -> tuple[ChunkResult, str]:
        return ChunkResult(ThinkingParserState.IN_CONTENT, "", chunk), buffer

    def finalize(
        self, thinking_buffer: list[str], content_buffer: list[str], final_state: ThinkingParserState
    ) -> FinalizedThinking:
        return FinalizedThinking("", "".join(content_buffer))


class SeparateFieldThinkingParser(ThinkingParser):
    """Thinking arrives in its own field, so any content chunk is content."""

    def process_chunk(self, chunk: str, state: ThinkingParserState, buffer: str) -> tuple[ChunkResult, str]:
        return ChunkResult(ThinkingParserState.IN_CONTENT, "", chunk), buffer

    def finalize(
        self, thinking_buffer: list[str], content_buffer: list[str], final_state: ThinkingParserState
    ) -> FinalizedThinking:
        return FinalizedThinking("".join(thinking_buffer), "".join(content_buffer).strip())


def model_family(model: str) -> str:
    """Strip a provider namespace such as OpenRouter's `openai/`."""
    lowered = model.lower()
    _, separator, remainder = lowered.partition("/")
    return remainder if separator else lowered


def is_openai_reasoning_model(model: str) -> bool:
    family = model_family(model)
    return (
        family.startswith("gpt-5")
        or "gpt-5." in family
        or family.startswith("o1")
        or family.startswith("o3")
        or family.startswith("o4")
        or "gpt-oss" in family
    )


def create_parser(model: str) -> ThinkingParser:
    lowered = model.lower()

    if "nemotron" in lowered or "nemo" in lowered:
        return NemoThinkingParser()
    if "qwen" in lowered and ("think" in lowered or "qwq" in lowered):
        return StandardThinkingParser()
    if "deepseek" in lowered:
        return SeparateFieldThinkingParser()
    if is_openai_reasoning_model(model):
        return SeparateFieldThinkingParser()
    return StandardThinkingParser()


def extra_parameters(model: str) -> dict[str, Any]:
    """Model-specific request parameters beyond model/messages/temperature."""
    lowered = model.lower()
    if "nemotron" in lowered or "nemo" in lowered:
        return {"enable_thinking": True}
    if "deepseek" in lowered and "r1" in lowered:
        return {"enable_reasoning": True}
    return {}
