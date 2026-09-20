"""Port of the SettingsStore / BackupService coverage in HotkeyShortcutTests."""

import json

import pytest

from fluentry.models.hotkey import HotkeyShortcut
from fluentry.models.keycodes import (
    KEY_LEFTALT,
    KEY_LEFTMETA,
    KEY_RIGHTALT,
    ModifierFlags,
)
from fluentry.persistence.backup_service import (
    BackupFileVersion,
    BackupService,
    InvalidBackupError,
    UnsupportedSchemaVersionError,
)
from fluentry.persistence.settings_store import (
    DEFAULT_PRIVATE_AI_CONTEXT_TOKEN_LIMIT,
    MICROPHONE_PRIORITY_MIGRATION_VERSION,
    HotkeyActivationMode,
    Keys,
    SettingsStore,
)
from fluentry.persistence.settings_types import (
    MicrophonePriorityEntry,
    MicrophoneSelectionMode,
    SpokenFormattingAction,
    SpokenFormattingActionRule,
    TranscriptionStartSound,
)
from fluentry.persistence.speech_model import SpeechModel


def backup_round_trip(settings: SettingsStore, drop_keys: tuple[str, ...] = ()):
    """Encode a backup, optionally delete keys from it, and decode it again."""
    service = BackupService(settings=settings)
    document = service.make_backup_document()
    encoded = json.loads(service.encode(document))
    for key in drop_keys:
        encoded["settings"].pop(key, None)
    return service.decode(json.dumps(encoded))


# --- defaults that must survive legacy backups -----------------------------


def test_silent_recording_setting_round_trips_and_older_backups_still_decode(settings):
    settings.skip_silent_recordings_enabled = True
    document = BackupService(settings=settings).make_backup_document()
    assert document.settings.get("skipSilentRecordingsEnabled") is True

    decoded = backup_round_trip(settings, drop_keys=("skipSilentRecordingsEnabled",))
    assert decoded.settings.get("skipSilentRecordingsEnabled") is None


def test_incremental_parakeet_defaults_on_and_round_trips_without_breaking_legacy_backups(settings):
    settings.defaults.remove(Keys.experimental_parakeet_unified_final_enabled)
    assert settings.experimental_parakeet_unified_final_enabled is True

    settings.experimental_parakeet_unified_final_enabled = False
    document = BackupService(settings=settings).make_backup_document()
    assert document.settings.get("experimentalParakeetUnifiedFinalEnabled") is False

    decoded = backup_round_trip(settings, drop_keys=("experimentalParakeetUnifiedFinalEnabled",))
    assert decoded.settings.get("experimentalParakeetUnifiedFinalEnabled") is None


def test_history_performance_defaults_on_and_round_trips_without_breaking_legacy_backups(settings):
    settings.defaults.remove(Keys.show_history_performance_metrics)
    assert settings.show_history_performance_metrics is True

    settings.show_history_performance_metrics = False
    assert settings.show_history_performance_metrics is False, "An explicit opt-out must remain respected"
    opted_out = BackupService(settings=settings).make_backup_document()
    assert opted_out.settings.get("showHistoryPerformanceMetrics") is False

    settings.show_history_performance_metrics = True
    document = BackupService(settings=settings).make_backup_document()
    assert document.settings.get("showHistoryPerformanceMetrics") is True

    decoded = backup_round_trip(settings, drop_keys=("showHistoryPerformanceMetrics",))
    assert decoded.settings.get("showHistoryPerformanceMetrics") is None


def test_direct_audio_capture_is_enabled_when_legacy_preference_is_unset(settings):
    settings.defaults.remove(Keys.experimental_direct_audio_capture_enabled)
    assert settings.experimental_direct_audio_capture_enabled is True


def test_direct_audio_capture_ignores_stored_disabled_preference(settings):
    settings.defaults.set(Keys.experimental_direct_audio_capture_enabled, False)
    assert settings.experimental_direct_audio_capture_enabled is True


# --- microphone migration --------------------------------------------------


def test_legacy_system_mode_backup_queues_microphone_priority_migration(settings):
    service = BackupService(settings=settings)
    settings.microphone_selection_migration_version = 4
    encoded = json.loads(service.encode(service.make_backup_document()))
    encoded["settings"]["microphoneSelectionMode"] = MicrophoneSelectionMode.SYSTEM.value
    encoded["settings"]["preferredInputDeviceUID"] = "legacy-system-mic"
    encoded["settings"].pop("microphonePriority", None)

    backup = service.decode(json.dumps(encoded))
    settings.restore(backup.settings)

    assert settings.preferred_input_device_uid == "legacy-system-mic"
    assert settings.microphone_selection_mode is MicrophoneSelectionMode.SYSTEM
    assert settings.microphone_selection_migration_version == 0


def test_priority_backup_keeps_completed_microphone_migration(settings):
    document = BackupService(settings=settings).make_backup_document()
    settings.microphone_selection_migration_version = 4

    settings.restore(document.settings)

    assert settings.microphone_selection_migration_version == MICROPHONE_PRIORITY_MIGRATION_VERSION


def test_priority_backup_round_trips_removed_connected_microphones(settings):
    settings.microphone_priority = [MicrophonePriorityEntry(uid="kept-mic", name="Kept Microphone")]
    settings.suppressed_microphone_uids = {"removed-connected-mic"}
    document = BackupService(settings=settings).make_backup_document()

    assert document.settings.get("suppressedMicrophoneUIDs") == ["removed-connected-mic"]
    settings.suppressed_microphone_uids = set()
    settings.restore(document.settings)
    assert settings.suppressed_microphone_uids == {"removed-connected-mic"}


def test_legacy_system_mode_remains_readable_for_priority_migration(settings):
    assert settings.has_stored_mic_selection_mode_for_migration is False
    assert settings.stored_mic_selection_mode_for_migration is MicrophoneSelectionMode.SYSTEM
    # An unset key still reports `manual` to the runtime.
    assert settings.microphone_selection_mode is MicrophoneSelectionMode.MANUAL

    settings.microphone_selection_mode = MicrophoneSelectionMode.SYSTEM
    assert settings.has_stored_mic_selection_mode_for_migration is True
    assert settings.stored_mic_selection_mode_for_migration is MicrophoneSelectionMode.SYSTEM


def test_input_selection_persists_app_preference(settings):
    settings.record_input_device_selection("usb-mic", name="USB Microphone")

    assert settings.preferred_input_device_uid == "usb-mic"
    assert settings.microphone_selection_mode is MicrophoneSelectionMode.MANUAL
    assert [entry.uid for entry in settings.microphone_priority] == ["usb-mic"]


def test_new_microphone_enters_second_and_stays_after_disconnecting(settings):
    class Device:
        def __init__(self, uid, name):
            self.uid = uid
            self.name = name

    settings.record_input_device_selection("builtin", name="Built-in")
    settings.reconcile_microphone_priority([Device("builtin", "Built-in"), Device("headset", "Headset")])
    assert [entry.uid for entry in settings.microphone_priority] == ["builtin", "headset"]

    # Disconnecting must not reorder or drop the saved position.
    settings.reconcile_microphone_priority([Device("builtin", "Built-in")])
    assert [entry.uid for entry in settings.microphone_priority] == ["builtin", "headset"]


def test_removed_connected_microphone_stays_removed_after_reconnect(settings):
    class Device:
        def __init__(self, uid, name):
            self.uid = uid
            self.name = name

    settings.record_input_device_selection("builtin", name="Built-in")
    settings.reconcile_microphone_priority([Device("builtin", "Built-in"), Device("headset", "Headset")])
    settings.remove_microphone_from_priority("headset", is_connected=True)
    assert [entry.uid for entry in settings.microphone_priority] == ["builtin"]

    settings.reconcile_microphone_priority([Device("builtin", "Built-in"), Device("headset", "Headset")])
    assert [entry.uid for entry in settings.microphone_priority] == ["builtin"]


def test_selecting_or_restoring_microphone_clears_removal_suppression(settings):
    class Device:
        def __init__(self, uid, name):
            self.uid = uid
            self.name = name

    settings.record_input_device_selection("builtin", name="Built-in")
    settings.remove_microphone_from_priority("headset", is_connected=True)
    assert settings.suppressed_microphone_uids == {"headset"}

    settings.record_input_device_selection("headset", name="Headset")
    assert settings.suppressed_microphone_uids == set()
    assert settings.microphone_priority[0].uid == "headset"

    settings.remove_microphone_from_priority("headset", is_connected=True)
    settings.restore_removed_microphones([Device("builtin", "Built-in"), Device("headset", "Headset")])
    assert settings.suppressed_microphone_uids == set()
    assert {entry.uid for entry in settings.microphone_priority} == {"builtin", "headset"}


def test_disabling_microphone_change_alerts_preserves_microphone_priority(settings):
    settings.microphone_priority = [
        MicrophonePriorityEntry(uid="a", name="A"),
        MicrophonePriorityEntry(uid="b", name="B"),
    ]
    settings.show_microphone_change_alerts = False
    assert [entry.uid for entry in settings.microphone_priority] == ["a", "b"]


def test_microphone_priority_reordering_helpers(settings):
    settings.microphone_priority = [
        MicrophonePriorityEntry(uid="a", name="A"),
        MicrophonePriorityEntry(uid="b", name="B"),
        MicrophonePriorityEntry(uid="c", name="C"),
    ]
    settings.move_microphone_priority_before("c", "a")
    assert [entry.uid for entry in settings.microphone_priority] == ["c", "a", "b"]

    settings.move_microphone_priority_by("c", 1)
    assert [entry.uid for entry in settings.microphone_priority] == ["a", "c", "b"]

    # Moving past the ends clamps instead of raising.
    settings.move_microphone_priority_by("a", -5)
    assert [entry.uid for entry in settings.microphone_priority] == ["a", "c", "b"]


# --- primary dictation shortcuts -------------------------------------------


def test_primary_dictation_shortcuts_fallback_to_legacy_shortcut(settings):
    legacy = HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.NONE)
    settings.defaults.set(Keys.hotkey_shortcut_key, legacy.to_dict())

    assert settings.primary_dictation_shortcuts == [legacy]
    assert settings.hotkey_shortcut == legacy


def test_primary_dictation_shortcuts_persist_multiple_and_update_legacy_first(settings):
    first = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE)
    second = HotkeyShortcut.keyboard(KEY_LEFTMETA, ModifierFlags.NONE)
    settings.primary_dictation_shortcuts = [first, second, first]

    assert settings.primary_dictation_shortcuts == [first, second], "duplicates collapse"
    assert HotkeyShortcut.from_dict(settings.defaults.json(Keys.hotkey_shortcut_key)) == first
    assert settings.primary_dictation_shortcut_display_string == "Right Alt / Left Super"


def test_empty_primary_dictation_shortcuts_fall_back_instead_of_unbinding(settings):
    settings.primary_dictation_shortcuts = []
    assert settings.primary_dictation_shortcuts == [SettingsStore.default_primary_dictation_shortcut()]


def test_paste_last_transcription_shortcut_defaults_to_unbound_and_disabled(settings):
    assert settings.paste_last_transcription_hotkey_shortcut is None
    assert settings.paste_last_transcription_shortcut_enabled is False


def test_paste_last_transcription_shortcut_persists_and_clears(settings):
    shortcut = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.SUPER)
    settings.paste_last_transcription_hotkey_shortcut = shortcut
    settings.paste_last_transcription_shortcut_enabled = True

    assert settings.paste_last_transcription_hotkey_shortcut == shortcut
    assert settings.paste_last_transcription_shortcut_enabled is True

    settings.paste_last_transcription_hotkey_shortcut = None
    assert settings.paste_last_transcription_hotkey_shortcut is None


def test_paste_last_transcription_shortcut_supports_mouse_button(settings):
    shortcut = HotkeyShortcut.mouse(3, ModifierFlags.NONE)
    settings.paste_last_transcription_hotkey_shortcut = shortcut
    assert settings.paste_last_transcription_hotkey_shortcut == shortcut
    assert settings.paste_last_transcription_hotkey_shortcut.is_mouse_shortcut


def test_older_backup_does_not_wipe_a_configured_paste_shortcut(settings):
    shortcut = HotkeyShortcut.mouse(3, ModifierFlags.NONE)
    settings.paste_last_transcription_hotkey_shortcut = shortcut
    settings.paste_last_transcription_shortcut_enabled = True

    decoded = backup_round_trip(
        settings,
        drop_keys=("pasteLastTranscriptionHotkeyShortcut", "pasteLastTranscriptionShortcutEnabled"),
    )
    settings.restore(decoded.settings)

    assert settings.paste_last_transcription_hotkey_shortcut == shortcut
    assert settings.paste_last_transcription_shortcut_enabled is True


def test_hotkey_mode_falls_back_to_legacy_press_and_hold_flag(settings):
    settings.defaults.set(Keys.press_and_hold_mode, True)
    assert settings.hotkey_mode == HotkeyActivationMode.HOLD
    assert settings.press_and_hold_mode is True

    settings.hotkey_mode = HotkeyActivationMode.AUTOMATIC
    assert settings.press_and_hold_mode is False
    assert settings.defaults.bool(Keys.press_and_hold_mode) is False


# --- sounds ----------------------------------------------------------------


def test_transcription_start_sound_none_option_has_no_file(settings):
    assert TranscriptionStartSound.NONE.start_sound_file_name is None
    assert TranscriptionStartSound.NONE.stop_sound_file_name is None


def test_transcription_start_sound_legacy_disabled_toggle_migrates_to_none(defaults):
    defaults.set(Keys.enable_transcription_sounds, False)
    settings = SettingsStore(defaults=defaults)
    assert settings.transcription_start_sound is TranscriptionStartSound.NONE


def test_transcription_start_sound_legacy_enabled_toggle_keeps_selected_sound(defaults):
    defaults.set(Keys.enable_transcription_sounds, True)
    settings = SettingsStore(defaults=defaults)
    assert settings.transcription_start_sound is TranscriptionStartSound.FLUID_SFX_0
    assert settings.transcription_start_sound.start_sound_file_name == "FV_start_0"
    assert settings.transcription_start_sound.stop_sound_file_name == "FV_end_0"


# --- private AI budgeting --------------------------------------------------


def test_private_ai_provider_prefix_kv_cache_defaults_on_and_persists_toggle(settings):
    assert settings.private_ai_prefix_kv_cache_enabled is True
    settings.private_ai_prefix_kv_cache_enabled = False
    assert settings.private_ai_prefix_kv_cache_enabled is False


def test_private_ai_provider_boost_defaults_on_and_persists_toggle(settings):
    assert settings.private_ai_boost_enabled is True
    settings.private_ai_boost_enabled = False
    assert settings.private_ai_boost_enabled is False


def test_private_ai_provider_context_token_limit_defaults_persists_and_clamps(settings):
    assert settings.private_ai_context_token_limit == DEFAULT_PRIVATE_AI_CONTEXT_TOKEN_LIMIT

    settings.private_ai_context_token_limit = 6144
    assert settings.private_ai_context_token_limit == 6144

    settings.private_ai_context_token_limit = 99_999
    assert settings.private_ai_context_token_limit == 8192

    settings.private_ai_context_token_limit = 1
    assert settings.private_ai_context_token_limit == 2048


def test_private_ai_context_default_migrates_2k_installs_to_4k(defaults):
    defaults.set(Keys.private_ai_context_token_limit, 2048)
    settings = SettingsStore(defaults=defaults)
    assert settings.private_ai_context_token_limit == 4096

    # A deliberate non-default choice is preserved by the migration.
    other = SettingsStore.__new__(SettingsStore)
    other.defaults = defaults
    other._observers = []
    other.private_ai_context_token_limit = 6144
    other.migrate_private_ai_context_default_to_4k_if_needed()
    assert other.private_ai_context_token_limit == 6144


# --- backup document -------------------------------------------------------


def test_backup_document_round_trips_through_json(settings):
    settings.spoken_send_phrase = "ship it"
    settings.overlay_bottom_offset = 120.0
    service = BackupService(settings=settings)

    decoded = service.decode(service.encode(service.make_backup_document()))

    assert decoded.schema_version == BackupFileVersion.current()
    assert decoded.settings.get("spokenSendPhrase") == "ship it"
    assert decoded.settings.get("overlayBottomOffset") == 120.0


def test_backup_rejects_a_future_schema_version(settings):
    service = BackupService(settings=settings)
    encoded = json.loads(service.encode(service.make_backup_document()))
    encoded["schemaVersion"] = {"major": 2, "minor": 0}

    with pytest.raises(UnsupportedSchemaVersionError):
        service.decode(json.dumps(encoded))


def test_backup_rejects_invalid_json(settings):
    with pytest.raises(InvalidBackupError):
        BackupService(settings=settings).decode("not a backup")


def test_backup_migrates_the_legacy_private_ai_prefix_cache_key(settings):
    service = BackupService(settings=settings)
    encoded = json.loads(service.encode(service.make_backup_document()))
    encoded["settings"].pop("privateAIPrefixKVCacheEnabled")
    encoded["settings"]["fluidIntelligencePrefixKVCacheEnabled"] = False

    decoded = service.decode(json.dumps(encoded))
    assert decoded.settings.get("privateAIPrefixKVCacheEnabled") is False


def test_suggested_filename_uses_minute_resolution(settings):
    from datetime import datetime, timezone

    moment = datetime(2026, 3, 8, 18, 5, tzinfo=timezone.utc)
    assert (
        BackupService(settings=settings).suggested_filename(moment)
        == "Fluentry_Backup_2026-03-08_18-05.json"
    )


# --- speech model ----------------------------------------------------------


def test_a_retired_speech_model_falls_back_to_the_default(settings):
    """A selection this build no longer knows must not break startup."""
    settings.defaults.set(Keys.selected_speech_model, "apple-speech")
    assert settings.selected_speech_model is SpeechModel.default_model()

    settings.defaults.set(Keys.selected_speech_model, "some-model-from-the-future")
    assert settings.selected_speech_model is SpeechModel.default_model()

    settings.defaults.set(Keys.selected_speech_model, SpeechModel.WHISPER_SMALL.value)
    assert settings.selected_speech_model is SpeechModel.WHISPER_SMALL


def test_available_models_exclude_unsupported_and_gated_entries():
    available = SpeechModel.available_models()
    assert SpeechModel.QWEN3_ASR not in available
    assert SpeechModel.NEMOTRON_STREAMING_320 not in available
    assert SpeechModel.WHISPER_TINY in available


def test_whisper_language_backup_value_maps_none_to_automatic(settings):
    assert settings.selected_whisper_language_code is None
    assert SettingsStore.whisper_language_backup_value(None) == "auto"
    assert SettingsStore.whisper_language_code_from_backup_value("auto") is None

    settings.selected_whisper_language_code = "pt"
    assert settings.selected_whisper_language_code == "pt"
    assert SettingsStore.whisper_language_backup_value("pt") == "pt"


# --- formatting rules ------------------------------------------------------


def test_spoken_formatting_actions_use_shared_prefix_and_default_aliases(settings):
    rules = settings.spoken_formatting_action_rules
    assert [rule.action for rule in rules] == list(SpokenFormattingAction)
    new_line = next(rule for rule in rules if rule.action is SpokenFormattingAction.NEW_LINE)
    assert new_line.aliases == ["new line", "next line"]
    assert settings.punctuation_dictionary_prefix == "literal"


def test_spoken_formatting_action_rules_round_trip_and_legacy_backups_preserve_current_rules(settings):
    settings.spoken_formatting_action_rules = [
        SpokenFormattingActionRule(SpokenFormattingAction.NEW_LINE, ["break line"]),
    ]
    stored = settings.spoken_formatting_action_rules
    assert next(
        rule for rule in stored if rule.action is SpokenFormattingAction.NEW_LINE
    ).aliases == ["break line"]
    # Actions missing from the assignment are stored disabled with no aliases.
    tab = next(rule for rule in stored if rule.action is SpokenFormattingAction.TAB)
    assert tab.aliases == [] and tab.is_enabled is False

    decoded = backup_round_trip(settings, drop_keys=("spokenFormattingActionRules",))
    settings.restore(decoded.settings)
    assert next(
        rule for rule in settings.spoken_formatting_action_rules
        if rule.action is SpokenFormattingAction.NEW_LINE
    ).aliases == ["break line"]


def test_spoken_formatting_action_aliases_reject_punctuation_and_action_conflicts(settings):
    settings.spoken_formatting_action_rules = [
        # "comma" is claimed by the punctuation dictionary and must not be stolen.
        SpokenFormattingActionRule(SpokenFormattingAction.NEW_LINE, ["comma", "break"]),
        # "break" is already claimed by the rule above.
        SpokenFormattingActionRule(SpokenFormattingAction.TAB, ["break", "indent"]),
    ]
    rules = {rule.action: rule for rule in settings.spoken_formatting_action_rules}
    assert rules[SpokenFormattingAction.NEW_LINE].aliases == ["break"]
    assert rules[SpokenFormattingAction.TAB].aliases == ["indent"]


def test_spoken_formatting_actions_can_be_customized_and_unset(settings):
    settings.spoken_formatting_action_rules = [
        SpokenFormattingActionRule(SpokenFormattingAction.SPACE, [], is_enabled=True),
    ]
    space = next(
        rule for rule in settings.spoken_formatting_action_rules
        if rule.action is SpokenFormattingAction.SPACE
    )
    assert space.aliases == []
    assert space.is_enabled is False, "An action with no aliases cannot stay enabled"


def test_punctuation_prefix_normalizes_and_falls_back(settings):
    settings.punctuation_dictionary_prefix = "  LITERAL  "
    assert settings.punctuation_dictionary_prefix == "literal"

    settings.punctuation_dictionary_prefix = "   "
    assert settings.punctuation_dictionary_prefix == "literal"

    settings.punctuation_dictionary_prefix = "Symbol"
    assert settings.punctuation_dictionary_prefix == "symbol"


def test_gaav_split_toggles_default_to_the_legacy_combined_setting(settings):
    assert settings.gaav_mode_enabled is False
    assert settings.gaav_lowercase_first_letter_enabled is False

    settings.gaav_mode_enabled = True
    assert settings.gaav_lowercase_first_letter_enabled is True
    assert settings.gaav_remove_trailing_period_enabled is True

    settings.gaav_remove_trailing_period_enabled = False
    assert settings.gaav_lowercase_first_letter_enabled is True
    assert settings.gaav_remove_trailing_period_enabled is False


def test_continuous_dictation_split_toggles_default_to_the_legacy_combined_setting(settings):
    assert settings.needs_dictation_formatting_context is False

    settings.continuous_dictation_mode_enabled = True
    assert settings.continuous_dictation_spacing_enabled is True
    assert settings.context_aware_capitalization_enabled is True
    assert settings.needs_dictation_formatting_context is True


# --- preview limit ---------------------------------------------------------


def test_transcription_preview_char_limit_snaps_and_clamps(settings):
    assert settings.transcription_preview_char_limit == 150

    settings.transcription_preview_char_limit = 173
    assert settings.transcription_preview_char_limit == 150

    settings.transcription_preview_char_limit = 176
    assert settings.transcription_preview_char_limit == 200

    settings.transcription_preview_char_limit = 10_000
    assert settings.transcription_preview_char_limit == 800

    settings.transcription_preview_char_limit = 0
    assert settings.transcription_preview_char_limit == 50


def test_visualizer_noise_threshold_defaults_and_clamps(settings):
    assert settings.visualizer_noise_threshold == pytest.approx(0.4)

    settings.visualizer_noise_threshold = 2.0
    assert settings.visualizer_noise_threshold == pytest.approx(0.95)

    settings.visualizer_noise_threshold = -1.0
    # Zero reads back as "unset", which is the documented 0.4 default.
    assert settings.visualizer_noise_threshold == pytest.approx(0.4)


def test_hotkey_mode_defaults_to_automatic(settings):
    """Hold-to-talk should work out of the box.

    A pure toggle default surprised people who held the key expecting it to
    record while held and stop on release. Automatic gives them that - a
    held key is push-to-talk - while a tap still toggles.
    """
    from fluentry.persistence.settings_store import HotkeyActivationMode

    # Nothing set: the fresh-install default.
    assert settings.hotkey_mode == HotkeyActivationMode.AUTOMATIC
