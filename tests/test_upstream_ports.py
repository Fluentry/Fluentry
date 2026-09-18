"""Behaviour taken from the upstream FluidVoice tracker.

Each group names the issue or pull request it came from, so the origin of
the rule is traceable when the two projects drift.
"""

from __future__ import annotations

import json

import pytest

from fluentry.persistence.settings_types import (
    CustomDictionaryEntry,
    TextInsertionMode,
)
from fluentry.services.custom_dictionary import apply_custom_dictionary


def deleting(trigger: str):
    return [CustomDictionaryEntry(triggers=[trigger], replacement="")]


# --- PR #513: an empty replacement deletes the trigger ----------------------


def test_an_empty_replacement_removes_the_word():
    assert apply_custom_dictionary("basically yes", deleting("basically")) == "yes"


def test_a_deleted_word_leaves_one_space_between_its_neighbours():
    """Upstream leaves both spaces, so "I basically agree" gains a gap."""
    assert apply_custom_dictionary("I basically agree", deleting("basically")) == "I agree"


def test_a_deleted_word_at_either_end_leaves_no_stray_space():
    assert apply_custom_dictionary("yes basically", deleting("basically")) == "yes"
    assert apply_custom_dictionary("basically yes", deleting("basically")) == "yes"


def test_every_occurrence_is_deleted():
    assert apply_custom_dictionary("a um b um c", deleting("um")) == "a b c"


def test_deletion_still_respects_word_boundaries():
    """"um" must not be cut out of "umbrella"."""
    assert apply_custom_dictionary("an umbrella", deleting("um")) == "an umbrella"


def test_a_whitespace_replacement_is_not_treated_as_a_deletion():
    entries = [CustomDictionaryEntry(triggers=["new line"], replacement="\n")]
    assert apply_custom_dictionary("a new line b", entries) == "a\nb"


def test_a_punctuation_trigger_can_be_deleted():
    """A trigger with no word character has no boundary to anchor to."""
    assert apply_custom_dictionary("¿que?", deleting("¿")) == "que?"


# --- PR #484: copy to clipboard without inserting ---------------------------


def test_the_new_mode_is_offered_alongside_the_others():
    assert TextInsertionMode.CLIPBOARD_ONLY.value == "clipboardOnly"
    assert TextInsertionMode.CLIPBOARD_ONLY.display_name == "Copy to Clipboard Only"
    assert TextInsertionMode.CLIPBOARD_ONLY.inserts_into_the_focused_app is False
    for mode in (TextInsertionMode.STANDARD, TextInsertionMode.RELIABLE_PASTE):
        assert mode.inserts_into_the_focused_app is True


def test_a_stored_mode_from_an_older_build_still_decodes():
    assert TextInsertionMode("standard") is TextInsertionMode.STANDARD
    assert TextInsertionMode("reliablePaste") is TextInsertionMode.RELIABLE_PASTE


# --- issue #985: an autostart entry that names the wrong program ------------


def test_the_autostart_entry_launches_this_installation():
    """A bare `fluentry` is not on PATH for a virtualenv or a checkout."""
    import shutil
    import sys

    from fluentry.app import autostart_command, autostart_entry

    command = autostart_command()
    entry = autostart_entry()
    assert f"Exec={command} --background" in entry
    assert "Type=Application" in entry

    if shutil.which("fluentry") is None:
        # Nothing on PATH, so it must name the interpreter it is running on.
        assert sys.executable in command
        assert "-m fluentry" in command


def test_the_autostart_command_quotes_an_awkward_path(monkeypatch):
    import sys

    from fluentry import app as app_module

    monkeypatch.setattr(sys, "argv", ["python"])
    monkeypatch.setattr(sys, "executable", "/opt/my apps/python")
    assert "'/opt/my apps/python'" in app_module.autostart_command()


# --- PR #504: history that clears itself ------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from fluentry.persistence.history_database import TranscriptionHistoryWriter  # noqa: E402
from fluentry.persistence.history_store import TranscriptionHistoryStore  # noqa: E402
from fluentry.persistence.settings_types import HistoryAutoClearInterval  # noqa: E402


@pytest.fixture
def history(tmp_path):
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    store = TranscriptionHistoryStore(writer=writer)
    store.wait_until_loaded()
    yield store
    writer.shutdown()


def add(store, text: str, days_ago: float):
    when = datetime.now().astimezone() - timedelta(days=days_ago)
    return store.add_entry(
        raw_text=text,
        processed_text=text,
        app_name="Editor",
        window_title="notes.txt",
        timestamp=when,
    )


def test_never_is_the_default_so_nothing_disappears_unasked(settings, history):
    assert settings.history_auto_clear_interval is HistoryAutoClearInterval.NEVER
    add(history, "old", days_ago=400)
    assert history.prune_expired_entries(settings.history_auto_clear_interval) == 0
    assert len(history.entries) == 1


@pytest.mark.parametrize(
    ("interval", "kept_age", "dropped_age"),
    [
        (HistoryAutoClearInterval.END_OF_DAY, 0.1, 1.5),
        (HistoryAutoClearInterval.AFTER_WEEK, 3, 9),
        (HistoryAutoClearInterval.AFTER_MONTH, 20, 40),
        (HistoryAutoClearInterval.AFTER_QUARTER, 60, 120),
    ],
)
def test_each_window_keeps_the_recent_and_drops_the_old(
    history, interval, kept_age, dropped_age
):
    add(history, "keep me", days_ago=kept_age)
    add(history, "drop me", days_ago=dropped_age)

    assert history.prune_expired_entries(interval) == 1
    assert [entry.raw_text for entry in history.entries] == ["keep me"]


def test_pruning_deletes_the_audio_too(tmp_path, history):
    """History entries can carry a recording; that has to go with them."""

    class FakeAudioStore:
        def __init__(self):
            self.deleted = []

        def delete_audio(self, file_name):
            self.deleted.append(file_name)

        def delete_all_audio_files(self):
            pass

        def audio_usage_bytes(self):
            return 0

        def delete_unreferenced_audio_files(self, referenced):
            return 0, 0

    from fluentry.persistence.history_entry import DictationAudioMetadata

    audio_store = FakeAudioStore()
    history.audio_store = audio_store
    entry = add(history, "old one", days_ago=90)
    # The entry is frozen, so attach the recording by replacing it.
    import dataclasses

    history.entries[0] = dataclasses.replace(
        entry,
        audio=DictationAudioMetadata(
            file_name="old.wav",
            duration_milliseconds=1,
            byte_count=1,
            sample_rate=16000,
            channels=1,
        ),
    )

    history.prune_expired_entries(HistoryAutoClearInterval.AFTER_WEEK)
    assert audio_store.deleted == ["old.wav"]


def test_pruning_survives_a_restart(tmp_path, settings):
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    store = TranscriptionHistoryStore(writer=writer)
    store.wait_until_loaded()
    add(store, "keep me", days_ago=1)
    add(store, "drop me", days_ago=99)
    store.prune_expired_entries(HistoryAutoClearInterval.AFTER_WEEK)
    store.finish_pending_writes()
    writer.shutdown()

    reopened_writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    reopened = TranscriptionHistoryStore(writer=reopened_writer)
    reopened.wait_until_loaded()
    assert [entry.raw_text for entry in reopened.entries] == ["keep me"]
    reopened_writer.shutdown()


def test_the_selection_moves_off_an_entry_that_was_cleared(history):
    old = add(history, "drop me", days_ago=99)
    add(history, "keep me", days_ago=1)
    history.selected_entry_id = old.id

    history.prune_expired_entries(HistoryAutoClearInterval.AFTER_WEEK)
    assert history.selected_entry_id != old.id
    assert history.selected_entry_id == history.entries[0].id


def test_the_window_is_counted_from_the_start_of_the_day():
    """"7 days" means seven whole days, not a rolling 168 hours."""
    now = datetime(2026, 9, 18, 15, 30).astimezone()
    cutoff = HistoryAutoClearInterval.AFTER_WEEK.cutoff(now)
    assert cutoff.hour == 0 and cutoff.minute == 0
    assert (now.date() - cutoff.date()).days == 7
    assert HistoryAutoClearInterval.NEVER.cutoff(now) is None


def test_the_setting_travels_in_a_backup(settings):
    settings.history_auto_clear_interval = HistoryAutoClearInterval.AFTER_MONTH
    payload = settings.make_backup_payload()
    assert payload.values["historyAutoClearInterval"] == "afterMonth"

    settings.history_auto_clear_interval = HistoryAutoClearInterval.NEVER
    settings.restore(payload)
    assert settings.history_auto_clear_interval is HistoryAutoClearInterval.AFTER_MONTH


def test_an_unknown_stored_interval_falls_back_to_never(settings):
    settings.defaults.set("HistoryAutoClearInterval", "afterCentury")
    assert settings.history_auto_clear_interval is HistoryAutoClearInterval.NEVER


# --- PRs #503 / #474: shortcut changes reach the live listener -------------

from fluentry.models.hotkey import HotkeyShortcut  # noqa: E402
from fluentry.models.keycodes import (  # noqa: E402
    KEY_F12,
    KEY_LEFTSHIFT,
    KEY_RIGHTALT,
    KEY_V,
    ModifierFlags,
)
from fluentry.persistence.settings_store import HotkeyActivationMode  # noqa: E402
from fluentry.platform.hotkey_listener import SyntheticHotkeyBackend  # noqa: E402
from fluentry.services.global_hotkey_manager import GlobalHotkeyManager  # noqa: E402

RIGHT_ALT = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])


@pytest.fixture
def hotkeys():
    calls: list[str] = []
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(
        backend=backend,
        mode=HotkeyActivationMode.TOGGLE,
        is_recording=lambda: False,
        on_start=lambda _mode: calls.append("start"),
        on_stop=lambda _mode: calls.append("stop"),
    )
    manager.configure(primary_shortcuts=[RIGHT_ALT])
    manager.start()
    return manager, backend, calls


def tap(backend, *codes):
    for code in codes:
        backend.press_key(code)
    for code in reversed(codes):
        backend.release_key(code)


def test_a_replaced_shortcut_stops_firing_immediately(hotkeys):
    manager, backend, calls = hotkeys
    manager.configure(
        primary_shortcuts=[HotkeyShortcut.keyboard(KEY_F12, ModifierFlags.NONE)]
    )
    calls.clear()

    tap(backend, KEY_RIGHTALT)
    assert calls == []
    tap(backend, KEY_F12)
    assert calls == ["start"]


def test_deleting_a_modifier_only_shortcut_mid_press_disarms_it(hotkeys):
    """It must not fire on the release that follows its deletion."""
    manager, backend, calls = hotkeys
    backend.press_key(KEY_RIGHTALT)
    calls.clear()

    manager.configure(primary_shortcuts=[])
    backend.release_key(KEY_RIGHTALT)
    assert calls == []


def test_deleting_every_shortcut_leaves_nothing_listening(hotkeys):
    manager, backend, calls = hotkeys
    manager.configure(primary_shortcuts=[])
    calls.clear()

    tap(backend, KEY_RIGHTALT)
    tap(backend, KEY_F12)
    tap(backend, KEY_LEFTSHIFT, KEY_V)
    assert calls == []


# --- issue #858: a key combination nobody configured ------------------------


def test_an_unconfigured_combination_never_starts_dictation():
    calls: list[str] = []
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(
        backend=backend,
        mode=HotkeyActivationMode.TOGGLE,
        is_recording=lambda: False,
        on_start=lambda _mode: calls.append("start"),
    )
    manager.configure(primary_shortcuts=[])
    manager.start()

    tap(backend, KEY_LEFTSHIFT, KEY_V)
    assert calls == []


# --- issue #884: the shortcut must be inert on a locked screen -------------


def test_a_locked_session_ignores_the_shortcut():
    calls: list[str] = []
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(
        backend=backend,
        mode=HotkeyActivationMode.TOGGLE,
        is_recording=lambda: False,
        on_start=lambda _mode: calls.append("start"),
        session_is_locked=lambda: True,
    )
    manager.configure(primary_shortcuts=[RIGHT_ALT])
    manager.start()

    tap(backend, KEY_RIGHTALT)
    assert calls == []


# --- issues #929 / #962: the clipboard is borrowed, not taken --------------


def test_a_paste_insert_gives_the_clipboard_back():
    from fluentry.platform.clipboard import InMemoryClipboard
    from fluentry.platform.text_injection import RecordingBackend, TypingService

    clipboard = InMemoryClipboard()
    clipboard.write_text("something the user copied")
    typing = TypingService(
        backend=RecordingBackend(), clipboard=clipboard, paste_settle_seconds=0
    )

    typing.insert_via_clipboard("dictated words")
    assert clipboard.read_text() == "something the user copied"


def test_a_failed_direct_insert_does_not_strand_text_on_the_clipboard():
    from fluentry.platform.clipboard import InMemoryClipboard
    from fluentry.platform.text_injection import RecordingBackend, TypingService

    clipboard = InMemoryClipboard()
    clipboard.write_text("something the user copied")
    typing = TypingService(
        backend=RecordingBackend(succeeds=False),
        clipboard=clipboard,
        paste_settle_seconds=0,
    )

    typing.type_text("second dictation", mode=TextInsertionMode.STANDARD)
    assert clipboard.read_text() == "something the user copied"


# --- issue #977: AI enhancement on or off per app --------------------------

from fluentry.persistence.settings_types import (  # noqa: E402
    AppAIEnhancement,
    AppPromptBinding,
    PromptMode,
)


def bind(app_id: str, override: AppAIEnhancement) -> AppPromptBinding:
    return AppPromptBinding(
        mode=PromptMode.DICTATE,
        app_bundle_id=app_id,
        app_name=app_id,
        prompt_id=None,
        ai_enhancement=override,
    )


def test_an_app_with_no_binding_follows_the_global_switch(settings):
    settings.enable_ai_processing = True
    assert settings.ai_enhancement_enabled_for_app("org.gnome.Text") is True
    settings.enable_ai_processing = False
    assert settings.ai_enhancement_enabled_for_app("org.gnome.Text") is False


def test_inherit_is_the_default_for_a_new_binding(settings):
    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [
        AppPromptBinding(
            mode=PromptMode.DICTATE,
            app_bundle_id="org.gnome.text",
            app_name="Text",
            prompt_id=None,
        )
    ]
    assert settings.ai_enhancement_enabled_for_app("org.gnome.Text") is True


def test_an_app_can_opt_out_while_the_rest_stay_on(settings):
    """The example from the request: chat cleaned up, coding agent raw."""
    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [bind("dev.codex.app", AppAIEnhancement.OFF)]

    assert settings.ai_enhancement_enabled_for_app("dev.codex.app") is False
    assert settings.ai_enhancement_enabled_for_app("com.slack.Slack") is True


def test_an_app_can_opt_in_while_the_global_switch_is_off(settings):
    settings.enable_ai_processing = False
    settings.app_prompt_bindings = [bind("com.slack.Slack", AppAIEnhancement.ON)]

    assert settings.ai_enhancement_enabled_for_app("com.slack.Slack") is True
    assert settings.ai_enhancement_enabled_for_app("dev.codex.app") is False


def test_the_override_matches_the_app_id_case_insensitively(settings):
    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [bind("org.gnome.text", AppAIEnhancement.OFF)]
    assert settings.ai_enhancement_enabled_for_app("ORG.GNOME.Text") is False


def test_an_unknown_focused_app_falls_back_to_the_global_switch(settings):
    """Most Wayland compositors do not report the focused window at all."""
    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [bind("dev.codex.app", AppAIEnhancement.OFF)]
    assert settings.ai_enhancement_enabled_for_app(None) is True


def test_the_override_survives_a_round_trip(settings):
    settings.app_prompt_bindings = [bind("dev.codex.app", AppAIEnhancement.OFF)]
    assert settings.app_prompt_bindings[0].ai_enhancement is AppAIEnhancement.OFF


def test_a_binding_written_before_this_existed_still_loads():
    payload = bind("dev.codex.app", AppAIEnhancement.OFF).to_dict()
    del payload["aiEnhancement"]
    assert AppPromptBinding.from_dict(payload).ai_enhancement is AppAIEnhancement.INHERIT


def test_the_dictation_skips_the_model_for_an_opted_out_app(settings, tmp_path):
    """End to end: no provider call, and the raw transcript is typed."""
    from fluentry.persistence.history_store import TranscriptionHistoryStore
    from fluentry.platform.text_injection import RecordingBackend, TypingService
    from fluentry.services.asr_service import ASRService
    from fluentry.services.providers.base import ScriptedTranscriptionProvider
    from fluentry.services.text_pipeline import PipelineContext

    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [bind("dev.codex.app", AppAIEnhancement.OFF)]

    enhanced_calls: list[str] = []
    backend = RecordingBackend()
    service = ASRService(
        settings=settings,
        provider=ScriptedTranscriptionProvider(responses=["raw words"]),
        typing_service=TypingService(backend=backend, paste_settle_seconds=0),
        history_store=TranscriptionHistoryStore(load=False),
        enhance=lambda text, context: enhanced_calls.append(text) or "REWRITTEN",
        focus_context=lambda: PipelineContext(
            app_name="Codex", bundle_id="dev.codex.app", window_title="main.py"
        ),
    )

    outcome = service.process_samples([0.2] * 16_000)
    assert enhanced_calls == [], "the model was called for an opted-out app"
    assert outcome.was_ai_processed is False
    assert backend.typed == ["raw words"]


def test_the_dictation_still_enhances_elsewhere(settings, tmp_path):
    from fluentry.persistence.history_store import TranscriptionHistoryStore
    from fluentry.platform.text_injection import RecordingBackend, TypingService
    from fluentry.services.asr_service import ASRService
    from fluentry.services.providers.base import ScriptedTranscriptionProvider
    from fluentry.services.text_pipeline import PipelineContext

    settings.enable_ai_processing = True
    settings.app_prompt_bindings = [bind("dev.codex.app", AppAIEnhancement.OFF)]

    backend = RecordingBackend()
    service = ASRService(
        settings=settings,
        provider=ScriptedTranscriptionProvider(responses=["raw words"]),
        typing_service=TypingService(backend=backend, paste_settle_seconds=0),
        history_store=TranscriptionHistoryStore(load=False),
        enhance=lambda text, context: "REWRITTEN",
        focus_context=lambda: PipelineContext(
            app_name="Slack", bundle_id="com.slack.Slack", window_title="general"
        ),
    )

    outcome = service.process_samples([0.2] * 16_000)
    assert outcome.was_ai_processed is True
    assert backend.typed == ["REWRITTEN"]
