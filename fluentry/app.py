"""Application wiring.

The Linux counterpart of `AppServices` + `AppDelegate`: it owns the long-lived
objects, connects them, and exposes the handful of operations the UI needs.

Nothing here is Qt-aware, so the whole app can be driven headlessly — which
is what the tests do.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .analytics.database import AnalyticsDatabase
from .analytics.events import ActivityKind, ModelDescriptor, UsageMode
from .models.hotkey import HotkeyShortcut
from .persistence.backup_service import BackupService, InvalidBackupError
from .persistence.defaults import FileDefaults, data_home, state_home
from .persistence.history_database import TranscriptionHistoryWriter
from .persistence.history_store import TranscriptionHistoryStore
from .persistence.keychain import KeychainService, secret_backend_name
from .persistence.settings_store import SettingsStore
from .persistence.settings_types import (
    AccentColorOption,
    CustomDictionaryEntry,
    OverlayPosition,
    OverlaySize,
    ThemePreference,
)
from .persistence.speech_model import SpeechBackend, SpeechModel
from .platform.audio_capture import CaptureUnavailableError, make_capture_backend
from .platform.audio_devices import LinuxAudioDeviceManager
from .platform.clipboard import make_clipboard
from .platform.hotkey_listener import available_hotkey_backends, make_hotkey_backend
from .platform.paste_key import PasteKeyCodeCache
from .platform.system_capabilities import (
    SESSION_WAYLAND,
    detect_tools,
    input_devices_readable,
    screen_is_locked,
    session_type,
)
from .platform.text_injection import (
    TypingService,
    available_backends,
    make_injection_backend,
)
from .services.asr_service import ASRService
from .services.custom_dictionary import CustomDictionary
from .services.dictionary_transfer import (
    DictionaryTransferError,
    DictionaryTransferService,
    ImportMode,
)
from .services.global_hotkey_manager import GlobalHotkeyManager
from .services.llm_client import LLMClient, LLMConfig, LLMError
from .services.media_playback import MediaPlaybackService, make_media_transport
from .services.microphone_coordinator import MicrophonePreferenceCoordinator
from .services.model_downloader import ModelDownloadError
from .services.providers.base import TranscriptionProviderError
from .services.text_pipeline import PipelineContext, TextPipeline
from .services.vocabulary_store import VocabularyStore

APP_VERSION = "1.0.0"
APP_ID = "dev.fluentry.Fluentry"

DEFAULT_DICTATION_PROMPT = (
    "You are a voice-to-text dictation cleaner. Fix transcription errors, punctuation "
    "and capitalization. Return only the corrected text, with no commentary."
)


@dataclass
class ReadinessItem:
    label: str
    ok: bool
    detail: str


class AppState:
    """Everything the UI talks to."""

    def __init__(
        self,
        settings: SettingsStore | None = None,
        history: TranscriptionHistoryStore | None = None,
        start_services: bool = True,
    ) -> None:
        self.settings = settings or SettingsStore(defaults=FileDefaults())
        self.keychain = KeychainService()
        self.devices = LinuxAudioDeviceManager()
        self.microphones = MicrophonePreferenceCoordinator(
            settings=self.settings,
            devices=self.devices,
            present_notice=self._present_microphone_notice,
            should_show_onboarding=lambda: not self.settings.onboarding_completed,
        )

        self.history = history or TranscriptionHistoryStore(writer=TranscriptionHistoryWriter())
        self.vocabulary = VocabularyStore()
        self.dictionary = CustomDictionary(self.settings.custom_dictionary_entries)
        self.pipeline = TextPipeline(self.settings, self.dictionary)
        self.dictionary_transfer = DictionaryTransferService(
            settings=self.settings, vocabulary_store=self.vocabulary, dictionary=self.dictionary
        )
        self.backup = BackupService(settings=self.settings, history_store=self.history)

        self.clipboard = make_clipboard()
        self.paste_key = PasteKeyCodeCache()
        self.typing = TypingService(
            backend=make_injection_backend(),
            clipboard=self.clipboard,
            insertion_mode=self.settings.text_insertion_mode,
            # An escape hatch while the paste path is being diagnosed: with
            # the restore off, a transcript the paste failed to deliver stays
            # on the clipboard instead of vanishing a moment later.
            restore_clipboard_after_paste=not os.environ.get("FLUENTRY_KEEP_CLIPBOARD"),
        )
        self.llm = LLMClient()

        self.provider = None
        self.capture = None
        self.asr = ASRService(
            settings=self.settings,
            provider=None,
            capture_backend=None,
            pipeline=self.pipeline,
            typing_service=self.typing,
            clipboard=self.clipboard,
            history_store=self.history,
            enhance=self._enhance,
            focus_context=self._focus_context,
        )
        # The service is the only thing that knows when transcription has
        # finished and insertion is about to begin, and the UI needs that
        # moment to take the overlay down - while it is up it holds the
        # keyboard focus and the text lands on it instead of the user's
        # window. Its other states are already published from here, so only
        # this one is forwarded.
        self.asr.add_state_observer(
            lambda state: self._notify_state(state) if state == "inserting" else None
        )

        media_transport = make_media_transport()
        self.media = (
            MediaPlaybackService(transport=media_transport) if media_transport else None
        )
        self.analytics: AnalyticsDatabase | None = None

        self.hotkeys = GlobalHotkeyManager(
            backend=make_hotkey_backend(),
            mode=self.settings.hotkey_mode,
            is_recording=lambda: self.asr.is_running,
            on_start=lambda _mode: self.start_dictation(),
            on_stop=lambda _mode: self.stop_dictation(),
            on_cancel=self.cancel_dictation,
            on_paste_last=self.asr.paste_last_transcription,
            session_is_locked=screen_is_locked,
        )

        self._session_id = 0
        self._state_observers: list[Callable[[str], None]] = []
        self._notice_observers: list[Callable[[str, str], None]] = []
        self.tray_is_visible = False
        self.last_error: str | None = None

        if start_services:
            self.start()

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        # Anything installed on a previous run has to be on the path before
        # a provider is built, or the engine looks missing all over again.
        from .services.runtime_installer import activate_installed_runtimes

        activate_installed_runtimes()
        self.prune_expired_history()
        self.paste_key.start()
        self.microphones.migrate_microphone_priority_if_needed()
        self.reload_provider()
        self.apply_shortcuts()
        self.hotkeys.start()
        self._start_analytics()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        if self.media is not None:
            self.media.shutdown()
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
        if self.analytics is not None:
            self.analytics.close()
        self.history.finish_pending_writes()

    def add_state_observer(self, callback: Callable[[str], None]) -> None:
        self._state_observers.append(callback)

    def add_notice_observer(self, callback: Callable[[str, str], None]) -> None:
        self._notice_observers.append(callback)

    def _notify_state(self, state: str) -> None:
        for observer in list(self._state_observers):
            try:
                observer(state)
            except Exception:
                pass

    def _notify(self, title: str, message: str) -> None:
        for observer in list(self._notice_observers):
            try:
                observer(title, message)
            except Exception:
                pass

    def _present_microphone_notice(self, notice) -> None:
        if notice.current_name:
            self._notify("Microphone changed", f"Now using {notice.current_name}.")
        else:
            self._notify(
                "Microphone unavailable", f"{notice.previous_name or 'The microphone'} went away."
            )

    # --- dictation --------------------------------------------------------

    def start_dictation(self) -> bool:
        if self.asr.is_running:
            return False
        device = self.microphones.input_device_for_capture()
        if self.capture is None:
            try:
                self.capture = make_capture_backend()
                self.asr.capture_backend = self.capture
            except CaptureUnavailableError as error:
                self.last_error = str(error)
                self._notify("Cannot record", str(error))
                return False

        self._session_id += 1
        if self.media is not None:
            self.media.recording_started(
                self._session_id, enabled=self.settings.pause_media_during_transcription
            )

        started = self.asr.start_recording(device.uid if device else None)
        if not started:
            if self.media is not None:
                self.media.session_finished(self._session_id)
            self._notify("Cannot record", self.last_error or "The microphone is unavailable.")
            return False
        if device is not None:
            self.microphones.confirm_active_selection(device.uid, device.name)
        self._notify_state("recording")
        return True

    def stop_dictation(self) -> None:
        if not self.asr.is_running:
            return
        session_id = self._session_id
        if self.media is not None:
            self.media.recording_stopped(session_id)
        self._notify_state("transcribing")

        def work() -> None:
            outcome = self.asr.stop_recording_and_transcribe()
            if outcome.error:
                self.last_error = outcome.error
                self._notify("Dictation failed", outcome.error)
            elif outcome.ai_processing_error and self.settings.notify_ai_processing_failures:
                self._notify(
                    "AI enhancement failed",
                    f"{outcome.ai_processing_error} Your transcript was typed unchanged.",
                )
            self._record_usage(outcome)
            if self.media is not None:
                self.media.session_finished(session_id)
            self._notify_state("idle")

        threading.Thread(target=work, name="fluentry.dictation", daemon=True).start()

    def cancel_dictation(self) -> None:
        if not self.asr.is_running:
            return
        self.asr.cancel_recording()
        if self.media is not None:
            self.media.session_finished(self._session_id)
        self._notify_state("idle")

    def toggle_dictation(self) -> None:
        if self.asr.is_running:
            self.stop_dictation()
        else:
            self.start_dictation()

    # --- providers --------------------------------------------------------

    def reload_provider(self) -> None:
        model = self.settings.selected_speech_model
        try:
            self.provider = make_speech_provider(model)
        except TranscriptionProviderError as error:
            self.provider = None
            self.last_error = str(error)
        self.asr.provider = self.provider

    def model_is_ready(self, model: SpeechModel) -> bool:
        try:
            provider = make_speech_provider(model)
        except TranscriptionProviderError:
            return False
        checker = getattr(provider, "is_downloaded", None)
        if isinstance(checker, bool):
            return checker
        return bool(getattr(provider, "is_ready", False))

    def download_model(
        self,
        model: SpeechModel,
        completion: Callable[[str | None], None],
        runtime=None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """Fetch the weights, and first the runtime if the engine needs one.

        The two are deliberately one action: an engine whose runtime is
        missing cannot be made to work by downloading weights, so asking the
        user to perform two separate steps only invites them to do the
        useless one.
        """

        def work() -> None:
            try:
                if runtime is not None:
                    from .services.runtime_installer import install

                    failure = install(runtime, on_progress=on_progress)
                    if failure is not None:
                        completion(failure)
                        return
                provider = make_speech_provider(model)
                provider.prepare()
                if model == self.settings.selected_speech_model:
                    self.provider = provider
                    self.asr.provider = provider
                completion(None)
            except (TranscriptionProviderError, ModelDownloadError, OSError) as error:
                completion(str(error))
            except Exception as error:
                completion(str(error))

        threading.Thread(target=work, name="fluentry.model-download", daemon=True).start()

    # --- AI enhancement ---------------------------------------------------

    def api_keys(self) -> dict[str, str]:
        try:
            return self.keychain.fetch_all_keys()
        except Exception:
            return {}

    def dictation_route(self, app_bundle_id: str | None = None):
        from .services.provider_routing import resolve_route

        return resolve_route(
            self.settings, dictation_slot="primary", app_bundle_id=app_bundle_id,
            api_keys=self.api_keys(),
        )

    def _enhance(self, text: str, context: PipelineContext) -> str:
        from .services.provider_routing import route_is_configured

        route = self.dictation_route(context.bundle_id)
        # Fail closed: an unverified provider, or one whose endpoint or key
        # changed since it was verified, must not receive the dictation.
        if not route_is_configured(route, self.settings):
            raise LLMError("No verified AI provider is configured.")

        # A selected prompt profile, a per-app binding and "send custom
        # prompt only" all decide what goes above the transcript. Reading
        # the override alone would ignore every one of them.
        prompt = self.settings.effective_dictation_system_prompt(
            DEFAULT_DICTATION_PROMPT, context.bundle_id
        )
        messages = []
        if prompt.strip():
            messages.append({"role": "system", "content": prompt})
        messages.append({"role": "user", "content": text})

        config = LLMConfig(
            messages=messages,
            model=route.model,
            base_url=route.base_url,
            api_key=route.api_key,
            # Some providers stream unless told otherwise, and the reply then
            # fails to parse. The setting lets the user match their provider.
            streaming=self.settings.enable_ai_streaming,
            temperature=None if SettingsStore.is_temperature_unsupported(route.model) else 0.2,
        )
        response = self.llm.call(config)
        return response.content or text

    def active_base_url(self) -> str:
        from .services.provider_routing import default_base_url

        identifier = self.settings.selected_provider_id
        for provider in self.settings.saved_providers:
            if provider.id == identifier:
                return provider.base_url
        return default_base_url(identifier)

    def select_provider(self, identifier: str) -> None:
        self.settings.selected_provider_id = identifier

    def set_provider_base_url(self, base_url: str) -> None:
        from .persistence.settings_types import SavedProvider

        identifier = self.settings.selected_provider_id
        if not identifier:
            return
        providers = [p for p in self.settings.saved_providers if p.id != identifier]
        providers.append(SavedProvider(id=identifier, name=identifier, base_url=base_url))
        self.settings.saved_providers = providers

    def has_api_key(self) -> bool:
        try:
            return bool(self.keychain.fetch_key(self.settings.selected_provider_id))
        except Exception:
            return False

    def store_api_key(self, key: str) -> None:
        from .services.provider_routing import provider_key

        identifier = self.settings.selected_provider_id
        try:
            self.keychain.store_key(key, identifier)
        except Exception as error:
            self._notify("Could not save API key", str(error))
            return
        # The stored verification was for the old key, so it no longer holds.
        fingerprints = self.settings.verified_provider_fingerprints
        if fingerprints.pop(provider_key(identifier), None) is not None:
            self.settings.verified_provider_fingerprints = fingerprints

    def verify_provider(self) -> str:
        base_url = self.active_base_url()
        if not base_url:
            return "Set a base URL first."
        model = self.settings.selected_model
        if not model:
            return "Set a model first."
        try:
            response = self.llm.call(
                LLMConfig(
                    messages=[{"role": "user", "content": "Reply with OK."}],
                    model=model,
                    base_url=base_url,
                    api_key=self.keychain.fetch_key(self.settings.selected_provider_id) or "",
                    streaming=False,
                    max_retries=1,
                    timeout_seconds=15,
                )
            )
        except LLMError as error:
            return f"Failed: {error}"
        except Exception as error:
            return f"Failed: {error}"
        # Record what was verified — the endpoint and key together — so a
        # later change to either one re-opens the question.
        from .services.provider_routing import provider_fingerprint, provider_key

        identifier = self.settings.selected_provider_id
        fingerprint = provider_fingerprint(
            base_url, self.keychain.fetch_key(identifier) or ""
        )
        if fingerprint is not None:
            fingerprints = self.settings.verified_provider_fingerprints
            fingerprints[provider_key(identifier)] = fingerprint
            self.settings.verified_provider_fingerprints = fingerprints
        return f"Connected. Model replied: {response.content[:60] or '(empty)'}"

    # --- shortcuts and appearance ----------------------------------------

    def apply_shortcuts(self) -> None:
        from .models.keycodes import KEY_ESC

        self.hotkeys.configure(
            primary_shortcuts=self.settings.primary_dictation_shortcuts,
            mode=self.settings.hotkey_mode,
            cancel_shortcut=self.settings.cancel_recording_hotkey_shortcut,
            paste_last_shortcut=self.settings.paste_last_transcription_hotkey_shortcut,
            paste_last_enabled=self.settings.paste_last_transcription_shortcut_enabled,
        )

    def set_primary_shortcut(self, shortcut: HotkeyShortcut) -> None:
        self.settings.hotkey_shortcut = shortcut
        self.apply_shortcuts()

    def set_hotkey_mode(self, mode: str) -> None:
        self.settings.hotkey_mode = mode
        self.apply_shortcuts()

    def set_theme(self, preference: ThemePreference) -> None:
        self.settings.theme_preference = preference
        self._notify_state("appearance")

    def set_accent(self, accent: AccentColorOption) -> None:
        self.settings.accent_color_option = accent
        self._notify_state("appearance")

    def set_overlay_size(self, size: OverlaySize) -> None:
        self.settings.overlay_size = size
        self._notify_state("overlay")

    def set_overlay_position(self, position: OverlayPosition) -> None:
        self.settings.overlay_position = position
        self._notify_state("overlay")

    def set_overlay_offset(self, offset: float) -> None:
        self.settings.overlay_bottom_offset = offset
        self._notify_state("overlay")

    def set_launch_at_startup(self, enabled: bool) -> None:
        self.settings.launch_at_startup = enabled
        write_autostart_entry(enabled)

    def set_analytics_enabled(self, enabled: bool) -> None:
        self.settings.share_detailed_analytics = enabled
        if not enabled and self.analytics is not None:
            self.analytics.purge_detailed_analytics()

    # --- dictionary -------------------------------------------------------

    def prune_expired_history(self) -> int:
        """Apply the history retention window. Safe to call at any time."""
        try:
            self.history.wait_until_loaded(timeout=5)
            return self.history.prune_expired_entries(
                self.settings.history_auto_clear_interval
            )
        except Exception:
            # Housekeeping must never stop the app from starting.
            return 0

    def invalidate_dictionary_cache(self) -> None:
        """Re-read the dictionary after something changed it.

        The pipeline holds a compiled matcher, so a write that bypasses
        `add_dictionary_entry` — an import, a backup restore, the local API —
        has to say so explicitly.
        """
        self.dictionary.entries = self.settings.custom_dictionary_entries

    def add_dictionary_entry(self, triggers: str, replacement: str) -> None:
        from .services.custom_dictionary import CustomDictionaryManualEntry

        parsed = [part.strip() for part in triggers.split(",") if part.strip()]
        if not parsed:
            return
        entry = CustomDictionaryEntry(
            triggers=parsed,
            replacement=CustomDictionaryManualEntry.sanitized_replacement(replacement),
        )
        self.settings.custom_dictionary_entries = [
            *self.settings.custom_dictionary_entries,
            entry,
        ]
        self.invalidate_dictionary_cache()

    def remove_dictionary_entry(self, index: int) -> None:
        entries = self.settings.custom_dictionary_entries
        if not 0 <= index < len(entries):
            return
        del entries[index]
        self.settings.custom_dictionary_entries = entries
        self.invalidate_dictionary_cache()

    def import_dictionary(self, path: str) -> str:
        try:
            document = self.dictionary_transfer.decode(Path(path).read_bytes())
            summary = self.dictionary_transfer.restore(document, ImportMode.MERGE)
        except (DictionaryTransferError, OSError) as error:
            return f"Could not import: {error}"
        return (
            f"Imported {summary.replacement_count} replacements and "
            f"{summary.custom_word_count} custom words."
        )

    def export_dictionary(self, path: str) -> None:
        document = self.dictionary_transfer.make_export_document()
        Path(path).write_bytes(self.dictionary_transfer.encode(document))

    # --- backup -----------------------------------------------------------

    def export_backup(self, path: str) -> None:
        document = self.backup.make_backup_document()
        Path(path).write_bytes(self.backup.encode(document))

    def import_backup(self, path: str) -> str:
        try:
            document = self.backup.decode(Path(path).read_bytes())
            self.backup.restore(document)
        except (InvalidBackupError, OSError) as error:
            return f"Could not import: {error}"
        self.dictionary.entries = self.settings.custom_dictionary_entries
        self.apply_shortcuts()
        self.reload_provider()
        return "Settings restored."

    # --- environment ------------------------------------------------------

    def available_microphones(self):
        devices = self.devices.list_input_devices()
        self.settings.reconcile_microphone_priority(devices)
        ordered = []
        by_uid = {device.uid: device for device in devices}
        for entry in self.settings.microphone_priority:
            device = by_uid.pop(entry.uid, None)
            if device is not None:
                ordered.append(device)
        ordered.extend(by_uid.values())
        return ordered

    def readiness_report(self) -> list[tuple[str, bool, str]]:
        """What the Welcome page shows: can this machine actually dictate?"""
        items: list[ReadinessItem] = []

        model = self.settings.selected_speech_model
        ready = self.model_is_ready(model)
        items.append(
            ReadinessItem(
                "Speech model",
                ready,
                f"{model.display_name} is ready."
                if ready
                else f"{model.display_name} still needs to be downloaded.",
            )
        )

        microphones = self.devices.list_input_devices()
        items.append(
            ReadinessItem(
                "Microphone",
                bool(microphones),
                f"{len(microphones)} input device(s) available."
                if microphones
                else "No input device was found.",
            )
        )

        backends = available_backends()
        session = session_type()
        if backends:
            # Ask for the backend that would actually be used, rather than the
            # first one installed: `available_backends` is a fixed list, while
            # the chooser reorders it on Wayland. Reading [0] here named
            # xdotool while ydotool was doing the typing.
            chosen = make_injection_backend(session).name
            detail = f"Typing into other apps uses {chosen}."
            if session == SESSION_WAYLAND and chosen == "xdotool":
                detail += (
                    " On Wayland this only reaches XWayland apps — install ydotool or "
                    "wtype to reach native Wayland apps."
                )
        else:
            detail = (
                "No input tool found. Install xdotool (X11) or ydotool/wtype (Wayland) "
                "so text can be typed into other apps."
            )
        items.append(ReadinessItem("Text insertion", bool(backends), detail))

        hotkey_backends = available_hotkey_backends()
        if "evdev" in hotkey_backends:
            hotkey_ok, hotkey_detail = True, "Global hotkeys work in every app."
        elif hotkey_backends and session == SESSION_WAYLAND:
            # pynput listens through the X server. On Wayland the compositor
            # never routes keystrokes there, so the listener starts happily
            # and then sees nothing at all — worse than no backend, because
            # it looks like it works.
            hotkey_ok = False
            hotkey_detail = (
                "Global hotkeys cannot work on Wayland through pynput. Add yourself "
                "to the 'input' group (sudo usermod -aG input $USER, then log out "
                "and back in) so Fluentry can read the keyboard directly."
            )
        elif hotkey_backends:
            hotkey_ok = True
            hotkey_detail = "Global hotkeys work in X11 and XWayland apps."
        else:
            hotkey_ok, hotkey_detail = False, "No hotkey backend is available."
        items.append(ReadinessItem("Global hotkey", hotkey_ok, hotkey_detail))

        return [(item.label, item.ok, item.detail) for item in items]

    def capture_backend_description(self) -> str:
        try:
            backend = self.capture or make_capture_backend()
        except CaptureUnavailableError as error:
            return str(error)
        return f"Capturing through {type(backend).__name__.replace('CaptureBackend', '')}."

    def media_backend_description(self) -> str:
        if self.media is None:
            return "Install playerctl to let Fluentry pause your media."
        return "Media control is available through MPRIS."

    def log_location_description(self) -> str:
        return f"Logs are written to {state_home() / 'fluentry.log'}."

    def local_api_description(self) -> str:
        from .services.localapi.server import DEFAULT_PORT

        return f"The local API listens on 127.0.0.1:{DEFAULT_PORT} when enabled."

    def _focus_context(self) -> PipelineContext:
        from .platform.active_window import active_window_context

        window = active_window_context()
        return PipelineContext(
            app_name=window.app_name,
            bundle_id=window.app_id,
            window_title=window.title,
            preceding_text="",
        )

    # --- analytics --------------------------------------------------------

    def _start_analytics(self) -> None:
        if not self.settings.share_detailed_analytics:
            return
        try:
            from .analytics.identity import distinct_id

            self.analytics = AnalyticsDatabase(
                path=data_home() / "analytics.sqlite3",
                distinct_id=distinct_id(),
                app_version=APP_VERSION,
            )
            self.analytics.record_activity(ActivityKind.APP, datetime.now(timezone.utc))
        except Exception:
            self.analytics = None

    def _record_usage(self, outcome) -> None:
        if self.analytics is None or not outcome.final_text:
            return
        try:
            self.analytics.record_usage(
                mode=UsageMode.DICTATION,
                transcription_model=ModelDescriptor(
                    provider="local", model=self.settings.selected_speech_model.value
                ),
                ai_model=(
                    ModelDescriptor(
                        provider=self.settings.selected_provider_id,
                        model=self.settings.selected_model or "unknown",
                    )
                    if outcome.was_ai_processed
                    else None
                ),
                moment=datetime.now(timezone.utc),
            )
        except Exception:
            pass


def make_speech_provider(model: SpeechModel):
    """Pick the runtime that can serve this model on Linux."""
    from .services.providers.whisper import make_whisper_provider

    if model.backend is SpeechBackend.WHISPER_CPP:
        return make_whisper_provider(model)
    if model.backend is SpeechBackend.ONNX:
        from .services.providers.onnx_asr import make_onnx_provider

        return make_onnx_provider(model)
    raise TranscriptionProviderError(
        f"{model.display_name} is not available on Linux. Choose another model."
    )


AUTOSTART_ENTRY_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Fluentry
Comment=Voice-to-text dictation
Exec={command} --background
Icon=dev.fluentry.Fluentry
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def autostart_command() -> str:
    """How to launch this very installation.

    A bare `fluentry` only works when the console script is on the session's
    PATH, which it is not for a virtualenv or a source checkout — the entry
    would be written happily and then silently never start. Resolving the
    command that is actually running keeps the two in step.
    """
    import shlex
    import shutil
    import sys

    script = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    if script == "fluentry":
        resolved = shutil.which("fluentry")
        if resolved:
            return shlex.quote(resolved)
    # Running as `python -m fluentry`, so say exactly that.
    return f"{shlex.quote(sys.executable)} -m fluentry"


def autostart_entry() -> str:
    return AUTOSTART_ENTRY_TEMPLATE.format(command=autostart_command())


def autostart_path() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "fluentry.desktop"


def write_autostart_entry(enabled: bool) -> None:
    """Where the desktop looks for programs to start at login."""
    path = autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(autostart_entry(), encoding="utf-8")
