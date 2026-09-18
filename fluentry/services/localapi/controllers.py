"""The route handlers.

Ports of `HealthController`, `HistoryAPIController`, `DictionaryAPIController`
and `InferenceAPIController`. Where macOS reached for singletons, each
controller here takes the running `AppState`, so tests can hand it a
stand-in.
"""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path

from ...persistence.settings_types import CustomDictionaryEntry
from ..vocabulary_store import VocabularyTerm
from .models import Request, Response, bounded_limit, error, json_response

APPEND = "append"
REPLACE = "replace"


class HealthController:
    def __init__(self, version: str) -> None:
        self.version = version

    def handle(self, request: Request) -> Response:
        if request.method != "GET":
            return error("Method not allowed.", status=405)
        return json_response({"status": "ok", "version": self.version})


class HistoryAPIController:
    def __init__(self, app) -> None:
        self.app = app

    def handle(self, request: Request) -> Response:
        if request.method != "GET":
            return error("Method not allowed.", status=405)

        limit = bounded_limit(request)
        try:
            self.app.history.wait_until_loaded()
        except Exception:
            return error(
                "History is unavailable. Retry from History in Fluentry.", status=503
            )

        items = [self._item(entry) for entry in self.app.history.entries[:limit]]
        return json_response({"count": len(items), "items": items})

    @staticmethod
    def _item(entry) -> dict:
        return {
            "id": entry.id,
            "timestamp": entry.timestamp,
            # originalText/finalText are the names the API has always used;
            # rawText/processedText are the storage names, kept alongside.
            "originalText": entry.raw_text,
            "finalText": entry.processed_text,
            "rawText": entry.raw_text,
            "processedText": entry.processed_text,
            "appName": entry.app_name,
            "windowTitle": entry.window_title,
            "characterCount": entry.character_count,
            "wasAIProcessed": entry.was_ai_processed,
            "transcriptionDurationMilliseconds": entry.transcription_duration_milliseconds,
            "aiProcessingDurationMilliseconds": entry.ai_processing_duration_milliseconds,
            "aiTokensPerSecond": entry.ai_tokens_per_second,
            "aiProcessingError": entry.ai_processing_error,
        }


class DictionaryPayloadError(Exception):
    pass


class DictionaryAPIController:
    def __init__(self, app) -> None:
        self.app = app

    def handle(self, request: Request) -> Response:
        route = (request.method, request.path)
        if route == ("GET", "/v1/dictionary/replacements"):
            return self._get_replacements()
        if route == ("POST", "/v1/dictionary/replacements"):
            return self._write_replacements(request)
        if route == ("GET", "/v1/dictionary/custom-words"):
            return self._get_custom_words()
        if route == ("POST", "/v1/dictionary/custom-words"):
            return self._write_custom_words(request)
        return error("Route not found.", status=404)

    # --- replacements -----------------------------------------------------

    def _get_replacements(self) -> Response:
        entries = self.app.settings.custom_dictionary_entries
        return json_response(
            {
                "count": len(entries),
                "items": [
                    {"id": entry.id, "triggers": list(entry.triggers), "replacement": entry.replacement}
                    for entry in entries
                ],
            }
        )

    def _write_replacements(self, request: Request) -> Response:
        try:
            mode, incoming = self._replacement_entries(request)
        except DictionaryPayloadError as problem:
            return error(f"Invalid replacement payload: {problem}", status=400)

        stored = [] if mode == REPLACE else list(self.app.settings.custom_dictionary_entries)
        accepted: list[CustomDictionaryEntry] = []
        for raw in incoming:
            entry = self._store_entry(raw)
            # An empty replacement is meaningless; an all-whitespace one is
            # deliberate (it is how "new line" gets typed).
            if not entry.triggers or not CustomDictionaryEntry.sanitized_replacement(
                entry.replacement
            ):
                continue

            def is_duplicate(existing, raw=raw, entry=entry) -> bool:
                if raw.get("id") is not None and existing.id == raw["id"]:
                    return True
                return existing.replacement.casefold() == entry.replacement.casefold()

            stored = [item for item in stored if not is_duplicate(item)]
            accepted = [item for item in accepted if not is_duplicate(item)]
            accepted.append(entry)

        # The newest entries win, so they are matched first.
        self.app.settings.custom_dictionary_entries = accepted + stored
        self.app.invalidate_dictionary_cache()
        return self._get_replacements()

    def _replacement_entries(self, request: Request) -> tuple[str, list[dict]]:
        payload = request.decode_json()
        if not isinstance(payload, dict):
            raise DictionaryPayloadError("Expected a JSON object.")
        mode = payload.get("mode") or APPEND
        if mode not in (APPEND, REPLACE):
            raise DictionaryPayloadError(f"Unknown mode '{mode}'.")

        if "entries" in payload:
            entries = payload.get("entries") or []
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                raise DictionaryPayloadError("entries must be a list of objects.")
            return mode, entries

        triggers = payload.get("triggers") or []
        replacement = payload.get("replacement")
        if isinstance(replacement, str) and isinstance(triggers, list) and triggers:
            return mode, [{"triggers": triggers, "replacement": replacement}]
        raise DictionaryPayloadError("Expected entries or triggers/replacement.")

    @staticmethod
    def _store_entry(raw: dict) -> CustomDictionaryEntry:
        triggers = [str(trigger) for trigger in raw.get("triggers") or []]
        replacement = raw.get("replacement")
        replacement = replacement if isinstance(replacement, str) else ""
        identifier = raw.get("id")
        if identifier:
            return CustomDictionaryEntry(
                id=str(identifier), triggers=triggers, replacement=replacement
            )
        return CustomDictionaryEntry(triggers=triggers, replacement=replacement)

    # --- custom words -----------------------------------------------------

    def _get_custom_words(self) -> Response:
        try:
            terms = self.app.vocabulary.load_user_boost_terms()
        except Exception as problem:
            return error(f"Failed to load custom words: {problem}", status=500)
        return json_response(
            {
                "count": len(terms),
                "items": [
                    {"text": term.text, "weight": term.weight, "aliases": list(term.aliases)}
                    for term in terms
                ],
            }
        )

    def _write_custom_words(self, request: Request) -> Response:
        try:
            mode, incoming = self._custom_word_entries(request)
        except DictionaryPayloadError as problem:
            return error(f"Invalid custom words payload: {problem}", status=400)

        try:
            stored = [] if mode == REPLACE else list(self.app.vocabulary.load_user_boost_terms())
        except Exception as problem:
            return error(f"Failed to load custom words: {problem}", status=500)

        for raw in incoming:
            term = self._store_term(raw)
            if not term.text.strip():
                continue
            stored = [item for item in stored if item.text.casefold() != term.text.casefold()]
            stored.append(term)

        try:
            self.app.vocabulary.save_user_boost_terms(stored)
        except Exception as problem:
            return error(f"Failed to save custom words: {problem}", status=500)
        return self._get_custom_words()

    def _custom_word_entries(self, request: Request) -> tuple[str, list[dict]]:
        payload = request.decode_json()
        if not isinstance(payload, dict):
            raise DictionaryPayloadError("Expected a JSON object.")
        mode = payload.get("mode") or APPEND
        if mode not in (APPEND, REPLACE):
            raise DictionaryPayloadError(f"Unknown mode '{mode}'.")

        if "entries" in payload:
            entries = payload.get("entries") or []
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                raise DictionaryPayloadError("entries must be a list of objects.")
            return mode, entries

        if isinstance(payload.get("text"), str):
            return mode, [payload]
        raise DictionaryPayloadError("Expected entries or text.")

    @staticmethod
    def _store_term(raw: dict) -> VocabularyTerm:
        weight = raw.get("weight")
        return VocabularyTerm(
            text=str(raw.get("text") or ""),
            weight=float(weight) if isinstance(weight, (int, float)) else None,
            aliases=tuple(str(alias) for alias in raw.get("aliases") or []),
        )


class InferenceError(Exception):
    pass


class InferenceAPIController:
    def __init__(self, app) -> None:
        self.app = app

    def handle(self, request: Request) -> Response:
        if request.method != "POST":
            return error("Method not allowed.", status=405)
        if request.path == "/v1/transcribe":
            return self._transcribe(request)
        if request.path == "/v1/postprocess":
            return self._postprocess(request)
        return error("Route not found.", status=404)

    # --- transcription ----------------------------------------------------

    def _transcribe(self, request: Request) -> Response:
        temporary: Path | None = None
        try:
            path = self._decode_file_path(request)
            if path is None:
                path = temporary = self._decode_uploaded_audio(request)
            return self._transcribe_file(path)
        except InferenceError as problem:
            return error(str(problem), status=400)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _transcribe_file(self, path: Path) -> Response:
        from ..providers.whisper import read_wav_as_mono_float

        if not path.is_file():
            raise InferenceError(f"No such file: {path}")
        provider = self.app.provider
        if provider is None:
            raise InferenceError("No speech model is ready.")
        try:
            samples = read_wav_as_mono_float(path)
            if not provider.is_ready:
                provider.prepare()
            result = provider.transcribe(
                samples, language=self.app.settings.selected_whisper_language_code
            )
        except Exception as problem:
            raise InferenceError(str(problem)) from problem
        return json_response(
            {
                "text": result.text,
                "sampleCount": len(samples),
                "durationMilliseconds": result.duration_milliseconds,
                "provider": self.app.settings.selected_speech_model.display_name,
            }
        )

    def _decode_file_path(self, request: Request) -> Path | None:
        if not request.is_json:
            return None
        payload = request.decode_json()
        if payload is None:
            raise InferenceError("Invalid JSON audio payload.")
        if not isinstance(payload, dict):
            raise InferenceError("Invalid JSON audio payload.")
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return None
        return Path(path).expanduser()

    def _decode_uploaded_audio(self, request: Request) -> Path:
        if request.is_json:
            payload = request.decode_json()
            if not isinstance(payload, dict):
                raise InferenceError("Invalid JSON audio payload.")
            encoded = payload.get("audioBase64")
            if not isinstance(encoded, str):
                raise InferenceError("Missing audio path or audioBase64.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as problem:
                raise InferenceError("Invalid audio payload.") from problem
            suffix = Path(payload.get("filename") or "audio.wav").suffix or ".wav"
        else:
            if not request.body:
                raise InferenceError("Missing audio body.")
            data = request.body
            suffix = Path(request.headers.get("x-filename") or "audio.wav").suffix or ".wav"

        handle = tempfile.NamedTemporaryFile(prefix="fluentry-api-", suffix=suffix, delete=False)
        try:
            handle.write(data)
        finally:
            handle.close()
        return Path(handle.name)

    # --- post-processing --------------------------------------------------

    def _postprocess(self, request: Request) -> Response:
        try:
            text = self._decode_text(request)
        except InferenceError as problem:
            return error(str(problem), status=400)

        from ..text_pipeline import PipelineContext

        result = self.app.pipeline.run(text, context=PipelineContext())
        return json_response(
            {
                "text": result.final_text,
                "raw": result.raw_text,
                "cleaned": result.cleaned_text,
                "shouldSend": result.should_send,
                "provider": self.app.settings.selected_provider_id,
                "model": self.app.settings.selected_model or "",
            }
        )

    def _decode_text(self, request: Request) -> str:
        if request.is_json:
            payload = request.decode_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                raise InferenceError("Invalid JSON text payload.")
            return payload["text"]
        try:
            return request.body.decode("utf-8")
        except UnicodeDecodeError as problem:
            raise InferenceError("Text body must be UTF-8.") from problem
