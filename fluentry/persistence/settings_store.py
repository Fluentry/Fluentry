"""Application settings.

Every setting the app has, in one place. Keys and defaults are stable, so
a backup restores
loss, and the getter semantics ("unset" vs "explicitly false") are reproduced
exactly — several features depend on that distinction.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..analytics.events import OnboardingOrigin
from ..models.hotkey import HotkeyShortcut
from ..models.keycodes import KEY_ESC, KEY_RIGHTALT, KEY_RIGHTMETA, ModifierFlags
from .defaults import Defaults, FileDefaults
from .nemotron_language import NemotronLanguage
from .speech_model import SpeechModel
from .settings_types import (
    AUTOMATIC_WHISPER_LANGUAGE_CODE,
    DEFAULT_FILLER_WORDS,
    DEFAULT_PROMPT_CONFIGURATION_KEY,
    DEFAULT_PUNCTUATION_DICTIONARY_PREFIX,
    DEFAULT_PUNCTUATION_DICTIONARY_RULES,
    DEFAULT_SPOKEN_FORMATTING_ACTION_RULES,
    PRIVATE_AI_PROMPT_CONFIGURATION_KEY,
    AccentColorOption,
    AppPromptBinding,
    AutomaticDictionarySuggestionFrequency,
    CohereLanguage,
    CustomDictionaryEntry,
    DictationPromptConfiguration,
    DictationPromptProfile,
    DictationPromptSelection,
    DictationShortcutSlot,
    MicrophonePriorityEntry,
    MicrophoneSelectionMode,
    HistoryAutoClearInterval,
    ModelReasoningConfig,
    NotchPresentationMode,
    OverlayPosition,
    OverlaySize,
    PrivateAIBackendPreference,
    PromptMode,
    PromptRoutingScope,
    PunctuationDictionaryRule,
    SavedProvider,
    SpokenFormattingAction,
    SpokenFormattingActionRule,
    SpokenSendKey,
    TextInsertionMode,
    ThemePreference,
    TranscriptionStartSound,
    normalized_alias,
    normalized_aliases,
    normalized_symbol,
)


class HotkeyActivationMode:
    TOGGLE = "toggle"
    HOLD = "hold"
    AUTOMATIC = "automatic"

    ALL = (TOGGLE, HOLD, AUTOMATIC)

    DISPLAY_NAMES = {
        TOGGLE: "Toggle",
        HOLD: "Hold",
        AUTOMATIC: "Automatic (Both)",
    }

    DESCRIPTIONS = {
        TOGGLE: "Tap once to start, tap again to stop.",
        HOLD: "Record only while the shortcut is held.",
        AUTOMATIC: "Tap to toggle, hold for push-to-talk.",
    }


#: The onboarding flow has six steps, numbered 0-5.
LAST_ONBOARDING_STEP = 5

#: When 1.6.2 shipped the migration that forced everyone through onboarding.
#: Anyone whose first launch predates this is an existing user.
FORCED_ONBOARDING_RESET_INTRODUCED_AT = 1_782_091_732.0


class Keys:
    enable_ai_processing = "EnableAIProcessing"
    show_main_window_at_login_launch = "ShowMainWindowAtLoginLaunch"
    file_transcription_speaker_labels_enabled = "FileTranscriptionSpeakerLabelsEnabled"
    file_transcription_expected_speaker_count = "FileTranscriptionExpectedSpeakerCount"
    dictation_prompt_off = "DictationPromptOff"
    enable_debug_logs = "EnableDebugLogs"
    available_ai_models = "AvailableAIModels"
    available_models_by_provider = "AvailableModelsByProvider"
    selected_ai_model = "SelectedAIModel"
    selected_model_by_provider = "SelectedModelByProvider"
    selected_provider_id = "SelectedProviderID"
    private_ai_prefix_kv_cache_enabled = "PrivateAIProviderPrefixKVCacheEnabled"
    private_ai_boost_enabled = "PrivateAIProviderBoostEnabled"
    private_ai_backend_preference = "FluidIntelligenceBackendPreference"
    private_ai_context_token_limit = "PrivateAIProviderContextTokenLimit"
    private_ai_context_default_migrated_to_4k = "PrivateAIProviderContextDefaultMigratedTo4K"
    provider_api_keys = "ProviderAPIKeys"
    provider_api_key_identifiers = "ProviderAPIKeyIdentifiers"
    saved_providers = "SavedProviders"
    verified_provider_fingerprints = "VerifiedProviderFingerprints"
    verified_private_ai_model_fingerprints = "VerifiedPrivateAIModelFingerprints"
    share_anonymous_analytics = "ShareAnonymousAnalytics"
    private_ai_interest_captured = "PrivateAIProviderInterestCaptured"
    hotkey_shortcut_key = "HotkeyShortcutKey"
    primary_dictation_shortcuts_key = "PrimaryDictationShortcuts"
    preferred_input_device_uid = "PreferredInputDeviceUID"
    microphone_priority = "MicrophonePriority"
    suppressed_microphone_uids = "SuppressedMicrophoneUIDs"
    preferred_output_device_uid = "PreferredOutputDeviceUID"
    microphone_selection_mode = "MicrophoneSelectionMode"
    microphone_selection_migration_version = "AppOnlyMicrophoneSelectionMigrationVersion"
    show_microphone_change_alerts = "ShowMicrophoneChangeAlerts"
    visualizer_noise_threshold = "VisualizerNoiseThreshold"
    launch_at_startup = "LaunchAtStartup"
    show_in_dock = "ShowInDock"
    accent_color_option = "AccentColorOption"
    theme_preference = "ThemePreference"
    enable_transcription_sounds = "EnableTranscriptionSounds"
    transcription_start_sound = "TranscriptionStartSound"
    transcription_sound_volume = "TranscriptionSoundVolume"
    transcription_sound_independent_volume = "TranscriptionSoundIndependentVolume"
    press_and_hold_mode = "PressAndHoldMode"
    hotkey_mode = "HotkeyMode"
    enable_streaming_preview = "EnableStreamingPreview"
    experimental_parakeet_unified_final_enabled = "ExperimentalParakeetUnifiedFinalEnabled"
    experimental_direct_audio_capture_enabled = "ExperimentalDirectAudioCaptureEnabled"
    show_history_performance_metrics = "ShowHistoryPerformanceMetrics"
    skip_silent_recordings_enabled = "SkipSilentRecordingsEnabled"
    enable_ai_streaming = "EnableAIStreaming"
    copy_transcription_to_clipboard = "CopyTranscriptionToClipboard"
    text_insertion_mode = "TextInsertionMode"
    spoken_send_enabled = "SpokenSendEnabled"
    spoken_send_immediately_enabled = "SpokenSendImmediatelyEnabled"
    spoken_send_phrase = "SpokenSendPhrase"
    spoken_send_key = "SpokenSendKey"
    auto_update_check_enabled = "AutoUpdateCheckEnabled"
    beta_releases_enabled = "BetaReleasesEnabled"
    last_update_check_date = "LastUpdateCheckDate"
    update_prompt_snoozed_until = "UpdatePromptSnoozedUntil"
    snoozed_update_version = "SnoozedUpdateVersion"
    playground_used = "PlaygroundUsed"
    onboarding_completed = "OnboardingCompleted"
    onboarding_generation = "OnboardingGeneration"
    manual_onboarding_reset_requested = "ManualOnboardingResetRequested"
    manual_onboarding_reset_requested_at = "ManualOnboardingResetRequestedAt"
    onboarding_current_step = "OnboardingCurrentStep"
    onboarding_ai_skipped = "OnboardingAISkipped"
    onboarding_playground_validated = "OnboardingPlaygroundValidated"
    onboarding_playground_skipped = "OnboardingPlaygroundSkipped"
    onboarding_selected_language_id = "OnboardingSelectedLanguageID"

    command_mode_selected_model = "CommandModeSelectedModel"
    command_mode_selected_provider_id = "CommandModeSelectedProviderID"
    command_mode_hotkey_shortcut = "CommandModeHotkeyShortcut"
    command_mode_confirm_before_execute = "CommandModeConfirmBeforeExecute"
    cancel_recording_hotkey_shortcut = "CancelRecordingHotkeyShortcut"
    paste_last_transcription_hotkey_shortcut = "PasteLastTranscriptionHotkeyShortcut"
    paste_last_transcription_shortcut_enabled = "PasteLastTranscriptionShortcutEnabled"
    command_mode_linked_to_global = "CommandModeLinkedToGlobal"
    command_mode_shortcut_enabled = "CommandModeShortcutEnabled"

    prompt_mode_hotkey_shortcut = "PromptModeHotkeyShortcut"
    prompt_mode_shortcut_enabled = "PromptModeShortcutEnabled"
    prompt_mode_selected_prompt_id = "PromptModeSelectedPromptID"
    secondary_dictation_prompt_off = "SecondaryDictationPromptOff"
    secondary_prompt_shortcut_removed = "SecondaryPromptShortcutRemoved"
    legacy_secondary_prompt_shortcut_retired = "LegacySecondaryPromptShortcutRetired"
    dictation_prompt_configurations = "DictationPromptConfigurations"

    rewrite_mode_hotkey_shortcut = "RewriteModeHotkeyShortcut"
    rewrite_mode_selected_model = "RewriteModeSelectedModel"
    rewrite_mode_selected_provider_id = "RewriteModeSelectedProviderID"
    rewrite_mode_linked_to_global = "RewriteModeLinkedToGlobal"

    model_reasoning_configs = "ModelReasoningConfigs"
    rewrite_mode_shortcut_enabled = "RewriteModeShortcutEnabled"
    show_thinking_tokens = "ShowThinkingTokens"

    user_typing_wpm = "UserTypingWPM"
    save_transcription_history = "SaveTranscriptionHistory"
    save_audio_with_transcription_history = "SaveAudioWithTranscriptionHistory"
    audio_history_budget_gb = "AudioHistoryBudgetGB"
    history_auto_clear_interval = "HistoryAutoClearInterval"
    notify_ai_processing_failures = "NotifyAIProcessingFailures"

    filler_words = "FillerWords"
    remove_filler_words_enabled = "RemoveFillerWordsEnabled"
    auto_convert_punctuation_enabled = "AutoConvertPunctuationEnabled"
    literal_dictation_formatting_enabled = "LiteralDictationFormattingEnabled"
    punctuation_dictionary_prefix = "PunctuationDictionaryPrefix"
    punctuation_dictionary_rules = "PunctuationDictionaryRules"
    spoken_formatting_action_rules = "SpokenFormattingActionRules"

    gaav_mode_enabled = "GAAVModeEnabled"
    gaav_lowercase_first_letter_enabled = "GAAVLowercaseFirstLetterEnabled"
    gaav_remove_trailing_period_enabled = "GAAVRemoveTrailingPeriodEnabled"

    continuous_dictation_mode_enabled = "ContinuousDictationModeEnabled"
    continuous_dictation_spacing_enabled = "ContinuousDictationSpacingEnabled"
    context_aware_capitalization_enabled = "ContextAwareCapitalizationEnabled"

    custom_dictionary_entries = "CustomDictionaryEntries"
    automatic_dictionary_learning_enabled = "AutomaticDictionaryLearningEnabled"
    automatic_dictionary_suggestion_frequency = "AutomaticDictionarySuggestionFrequency"
    vocabulary_boosting_enabled = "VocabularyBoostingEnabled"
    pronunciation_matching_enabled = "PronunciationMatchingEnabled"

    selected_transcription_provider = "SelectedTranscriptionProvider"
    whisper_model_size = "WhisperModelSize"
    selected_speech_model = "SelectedSpeechModel"
    selected_whisper_language_code = "SelectedWhisperLanguageCode"
    selected_cohere_language = "SelectedCohereLanguage"
    selected_nemotron_language = "SelectedNemotronLanguage"
    external_model_artifacts_directories = "ExternalModelArtifactsDirectories"

    overlay_position = "OverlayPosition"
    notch_presentation_mode = "NotchPresentationMode"
    overlay_bottom_offset = "OverlayBottomOffset"
    overlay_bottom_offset_migrated_to_50 = "OverlayBottomOffsetMigratedTo50"
    overlay_size = "OverlaySize"
    transcription_preview_char_limit = "TranscriptionPreviewCharLimit"

    pause_media_during_transcription = "PauseMediaDuringTranscription"
    custom_dictation_prompt = "CustomDictationPrompt"

    dictation_prompt_profiles = "DictationPromptProfiles"
    app_prompt_bindings = "AppPromptBindings"
    selected_dictation_prompt_id = "SelectedDictationPromptID"
    send_custom_prompt_only = "SendCustomPromptOnly"
    edit_prompt_off = "EditPromptOff"
    selected_edit_prompt_id = "SelectedEditPromptID"
    selected_write_prompt_id = "SelectedWritePromptID"
    selected_rewrite_prompt_id = "SelectedRewritePromptID"

    default_dictation_prompt_override = "DefaultDictationPromptOverride"
    default_edit_prompt_override = "DefaultEditPromptOverride"
    default_write_prompt_override = "DefaultWritePromptOverride"
    default_rewrite_prompt_override = "DefaultRewritePromptOverride"

    dictation_prompt_routing_scope = "DictationPromptRoutingScope"
    edit_prompt_routing_scope = "EditPromptRoutingScope"

    weekends_dont_break_streak = "WeekendsDontBreakStreak"


# Private-AI budgeting constants.
PRIVATE_AI_CONTEXT_TOKEN_LIMIT_RANGE = (2048, 8192)
PRIVATE_AI_CONTEXT_TOKEN_LIMIT_STEP = 512
DEFAULT_PRIVATE_AI_CONTEXT_TOKEN_LIMIT = 4096
PRIVATE_AI_DICTATION_SYSTEM_OVERHEAD_TOKENS = 1280
PRIVATE_AI_DICTATION_MINIMUM_OUTPUT_TOKENS = 256
PRIVATE_AI_DICTATION_ROUND_TRIP_TOKEN_COST = 2.75
PRIVATE_AI_DENSE_SEGMENT_BYTE_THRESHOLD = 12
PRIVATE_AI_DENSE_BYTES_PER_TOKEN = 2

TRANSCRIPTION_PREVIEW_CHAR_LIMIT_RANGE = (50, 800)
TRANSCRIPTION_PREVIEW_CHAR_LIMIT_STEP = 50
DEFAULT_TRANSCRIPTION_PREVIEW_CHAR_LIMIT = 150

MICROPHONE_PRIORITY_MIGRATION_VERSION = 4

#: The id the Private AI provider registers under. The shipped build has
#: no local model runtime, so nothing claims it, but a stored selection
#: naming it still has to resolve rather than error.
PRIVATE_AI_PROVIDER_ID = "__private_ai_provider__"
PRIVATE_AI_PROMPT_SELECTION_ID = "__PRIVATE_AI_PROVIDER__"

#: Namespace for per-profile prompt configurations.
PROFILE_CONFIGURATION_KEY_PREFIX = "profile:"


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _round_half_up(value: float) -> int:
    """Round halves away from zero, which Python's round() does not do."""
    import math

    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


@dataclass(frozen=True)
class PrivateAIDictationTokenBudget:
    max_output_tokens: int
    has_sufficient_headroom: bool


@dataclass
class SettingsBackupPayload:
    """The settings half of a backup file.

    Fields that are `None` mean "absent from this backup file", which restore
    treats as "leave the current value alone" — that is how older backups keep
    working when a new setting is added.
    """

    values: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name == "values":
            raise AttributeError(name)
        try:
            return self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def get(self, name: str, fallback: Any = None) -> Any:
        return self.values.get(name, fallback)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SettingsBackupPayload":
        return SettingsBackupPayload(dict(payload))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SettingsBackupPayload):
            return self.values == other.values
        return NotImplemented


class SettingsStore:
    """Typed accessors over a `Defaults` domain."""

    microphone_priority_migration_version = MICROPHONE_PRIORITY_MIGRATION_VERSION

    _shared: "SettingsStore | None" = None
    _shared_lock = threading.Lock()

    def __init__(self, defaults: Defaults | None = None, run_migrations: bool = True) -> None:
        self.defaults = defaults if defaults is not None else FileDefaults()
        self._observers: list[Callable[[], None]] = []
        if run_migrations:
            self.migrate_transcription_start_sound_if_needed()
            self.normalize_prompt_selections_if_needed()
            self.migrate_overlay_bottom_offset_to_50_if_needed()
            self.migrate_private_ai_context_default_to_4k_if_needed()

    @classmethod
    def shared(cls) -> "SettingsStore":
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = SettingsStore()
            return cls._shared

    @classmethod
    def reset_shared(cls, store: "SettingsStore | None" = None) -> None:
        """Test hook: swap or clear the process-wide store."""
        with cls._shared_lock:
            cls._shared = store

    # --- change notification ---------------------------------------------

    def add_observer(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._observers.append(callback)

        def remove() -> None:
            if callback in self._observers:
                self._observers.remove(callback)

        return remove

    def object_will_change(self) -> None:
        for observer in list(self._observers):
            try:
                observer()
            except Exception:
                pass

    # --- generic helpers --------------------------------------------------

    def _bool_default(self, key: str, fallback: bool) -> bool:
        value = self.defaults.object(key)
        return value if isinstance(value, bool) else fallback

    def _set(self, key: str, value: Any) -> None:
        self.object_will_change()
        self.defaults.set(key, value)

    def _enum(self, key: str, enum_cls, fallback):
        return enum_cls.from_raw(self.defaults.string(key), fallback)

    def _set_enum(self, key: str, value) -> None:
        self._set(key, value.value)

    def _shortcut(self, key: str, fallback: HotkeyShortcut | None) -> HotkeyShortcut | None:
        payload = self.defaults.json(key)
        if isinstance(payload, dict):
            try:
                return HotkeyShortcut.from_dict(payload)
            except (KeyError, ValueError, TypeError):
                return fallback
        return fallback

    def _set_shortcut(self, key: str, shortcut: HotkeyShortcut | None) -> None:
        if shortcut is None:
            self.object_will_change()
            self.defaults.remove(key)
            return
        self._set(key, shortcut.to_dict())

    # =====================================================================
    # AI processing
    # =====================================================================

    @property
    def enable_ai_processing(self) -> bool:
        return self.defaults.bool(Keys.enable_ai_processing)

    @enable_ai_processing.setter
    def enable_ai_processing(self, value: bool) -> None:
        self._set(Keys.enable_ai_processing, value)

    @property
    def enable_ai_streaming(self) -> bool:
        return self._bool_default(Keys.enable_ai_streaming, True)

    @enable_ai_streaming.setter
    def enable_ai_streaming(self, value: bool) -> None:
        self._set(Keys.enable_ai_streaming, value)

    @property
    def show_thinking_tokens(self) -> bool:
        return self._bool_default(Keys.show_thinking_tokens, True)

    @show_thinking_tokens.setter
    def show_thinking_tokens(self, value: bool) -> None:
        self._set(Keys.show_thinking_tokens, value)

    @property
    def notify_ai_processing_failures(self) -> bool:
        return self._bool_default(Keys.notify_ai_processing_failures, True)

    @notify_ai_processing_failures.setter
    def notify_ai_processing_failures(self, value: bool) -> None:
        self._set(Keys.notify_ai_processing_failures, value)

    # =====================================================================
    # Providers and models
    # =====================================================================

    @property
    def available_models(self) -> list[str]:
        return self.defaults.string_array(Keys.available_ai_models) or []

    @available_models.setter
    def available_models(self, value: list[str]) -> None:
        self._set(Keys.available_ai_models, list(value))

    @property
    def available_models_by_provider(self) -> dict[str, list[str]]:
        stored = self.defaults.dictionary(Keys.available_models_by_provider) or {}
        return {key: list(value) for key, value in stored.items() if isinstance(value, list)}

    @available_models_by_provider.setter
    def available_models_by_provider(self, value: dict[str, list[str]]) -> None:
        self._set(Keys.available_models_by_provider, {key: list(item) for key, item in value.items()})

    @property
    def selected_model(self) -> str | None:
        return self.defaults.string(Keys.selected_ai_model)

    @selected_model.setter
    def selected_model(self, value: str | None) -> None:
        self._set(Keys.selected_ai_model, value)

    @property
    def selected_model_by_provider(self) -> dict[str, str]:
        stored = self.defaults.dictionary(Keys.selected_model_by_provider) or {}
        return {key: value for key, value in stored.items() if isinstance(value, str)}

    @selected_model_by_provider.setter
    def selected_model_by_provider(self, value: dict[str, str]) -> None:
        self._set(Keys.selected_model_by_provider, dict(value))

    @property
    def saved_providers(self) -> list[SavedProvider]:
        payload = self.defaults.json(Keys.saved_providers)
        if not isinstance(payload, list):
            return []
        providers: list[SavedProvider] = []
        for item in payload:
            if isinstance(item, dict) and item.get("id"):
                providers.append(SavedProvider.from_dict(item))
        return providers

    @saved_providers.setter
    def saved_providers(self, value: list[SavedProvider]) -> None:
        self._set(Keys.saved_providers, [provider.to_dict() for provider in value])

    @property
    def selected_provider_id(self) -> str:
        return self.available_selected_provider_id(self.defaults.string(Keys.selected_provider_id))

    @selected_provider_id.setter
    def selected_provider_id(self, value: str) -> None:
        self._set(Keys.selected_provider_id, value)

    def available_selected_provider_id(self, raw: str | None) -> str:
        """Blank out a stored provider that no longer exists."""
        identifier = (raw or "").strip()
        if not identifier:
            return ""
        if identifier in BUILT_IN_PROVIDER_IDS:
            return identifier
        if identifier == PRIVATE_AI_PROVIDER_ID:
            return identifier
        if any(provider.id == identifier for provider in self.saved_providers):
            return identifier
        return ""

    @property
    def model_reasoning_configs(self) -> dict[str, ModelReasoningConfig]:
        payload = self.defaults.json(Keys.model_reasoning_configs)
        if not isinstance(payload, dict):
            return {}
        return {
            key: ModelReasoningConfig.from_dict(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    @model_reasoning_configs.setter
    def model_reasoning_configs(self, value: dict[str, ModelReasoningConfig]) -> None:
        self._set(Keys.model_reasoning_configs, {key: item.to_dict() for key, item in value.items()})

    @property
    def verified_provider_fingerprints(self) -> dict[str, str]:
        stored = self.defaults.dictionary(Keys.verified_provider_fingerprints) or {}
        return {key: value for key, value in stored.items() if isinstance(value, str)}

    @verified_provider_fingerprints.setter
    def verified_provider_fingerprints(self, value: dict[str, str]) -> None:
        self._set(Keys.verified_provider_fingerprints, dict(value))

    @property
    def verified_private_ai_model_fingerprints(self) -> dict[str, str]:
        """Model id → the fingerprint that was verified for it."""
        stored = self.defaults.dictionary(Keys.verified_private_ai_model_fingerprints) or {}
        return {key: value for key, value in stored.items() if isinstance(value, str)}

    @verified_private_ai_model_fingerprints.setter
    def verified_private_ai_model_fingerprints(self, value: dict[str, str]) -> None:
        self._set(Keys.verified_private_ai_model_fingerprints, dict(value))

    # --- private AI -------------------------------------------------------

    @property
    def private_ai_prefix_kv_cache_enabled(self) -> bool:
        return self._bool_default(Keys.private_ai_prefix_kv_cache_enabled, True)

    @private_ai_prefix_kv_cache_enabled.setter
    def private_ai_prefix_kv_cache_enabled(self, value: bool) -> None:
        self._set(Keys.private_ai_prefix_kv_cache_enabled, value)

    @property
    def private_ai_boost_enabled(self) -> bool:
        return self._bool_default(Keys.private_ai_boost_enabled, True)

    @private_ai_boost_enabled.setter
    def private_ai_boost_enabled(self, value: bool) -> None:
        self._set(Keys.private_ai_boost_enabled, value)

    @property
    def private_ai_backend_preference(self) -> PrivateAIBackendPreference:
        raw = (self.defaults.string(Keys.private_ai_backend_preference) or "").strip()
        preference = PrivateAIBackendPreference.from_raw(raw, None)
        if preference is None or preference is PrivateAIBackendPreference.AUTO:
            return PrivateAIBackendPreference.system_default()
        return preference

    @private_ai_backend_preference.setter
    def private_ai_backend_preference(self, value: PrivateAIBackendPreference) -> None:
        resolved = (
            PrivateAIBackendPreference.system_default()
            if value is PrivateAIBackendPreference.AUTO
            else value
        )
        self._set_enum(Keys.private_ai_backend_preference, resolved)

    @property
    def private_ai_context_token_limit(self) -> int:
        value = self.defaults.integer(Keys.private_ai_context_token_limit)
        if value == 0:
            return DEFAULT_PRIVATE_AI_CONTEXT_TOKEN_LIMIT
        return self.clamp_private_ai_context_token_limit(value)

    @private_ai_context_token_limit.setter
    def private_ai_context_token_limit(self, value: int) -> None:
        self._set(Keys.private_ai_context_token_limit, self.clamp_private_ai_context_token_limit(value))

    @property
    def private_ai_interest_captured(self) -> bool:
        return self.defaults.bool(Keys.private_ai_interest_captured)

    @private_ai_interest_captured.setter
    def private_ai_interest_captured(self, value: bool) -> None:
        self._set(Keys.private_ai_interest_captured, value)

    @staticmethod
    def clamp_private_ai_context_token_limit(value: int) -> int:
        low, high = PRIVATE_AI_CONTEXT_TOKEN_LIMIT_RANGE
        return min(max(value, low), high)

    @staticmethod
    def estimated_private_ai_dictation_words(context_token_limit: int) -> int:
        available = max(
            0,
            SettingsStore.clamp_private_ai_context_token_limit(context_token_limit)
            - PRIVATE_AI_DICTATION_SYSTEM_OVERHEAD_TOKENS,
        )
        input_tokens = available / PRIVATE_AI_DICTATION_ROUND_TRIP_TOKEN_COST
        import math

        return max(100, int(math.ceil(input_tokens * 0.75 / 50)) * 50)

    @staticmethod
    def estimated_private_ai_input_tokens(input_text: str) -> int:
        import math

        segments = input_text.split()
        word_based = int(math.ceil(len(segments) / 0.75))
        dense = 0
        for segment in segments:
            byte_count = len(segment.encode("utf-8"))
            if byte_count > PRIVATE_AI_DENSE_SEGMENT_BYTE_THRESHOLD:
                dense += _ceil_div(byte_count, PRIVATE_AI_DENSE_BYTES_PER_TOKEN)
            else:
                dense += 1
        return max(1, max(word_based, dense))

    @staticmethod
    def private_ai_dictation_token_budget(
        input_text: str, context_token_limit: int
    ) -> PrivateAIDictationTokenBudget:
        import math

        estimated_input = SettingsStore.estimated_private_ai_input_tokens(input_text)
        requested_output = max(
            PRIVATE_AI_DICTATION_MINIMUM_OUTPUT_TOKENS,
            int(math.ceil(estimated_input * 1.15)) + 64,
        )
        available_output = (
            SettingsStore.clamp_private_ai_context_token_limit(context_token_limit)
            - PRIVATE_AI_DICTATION_SYSTEM_OVERHEAD_TOKENS
            - estimated_input
        )
        return PrivateAIDictationTokenBudget(
            max_output_tokens=min(
                requested_output,
                max(PRIVATE_AI_DICTATION_MINIMUM_OUTPUT_TOKENS, available_output),
            ),
            has_sufficient_headroom=available_output >= requested_output,
        )

    @staticmethod
    def private_ai_max_output_tokens(input_text: str, context_token_limit: int) -> int:
        return SettingsStore.private_ai_dictation_token_budget(
            input_text, context_token_limit
        ).max_output_tokens

    def migrate_private_ai_context_default_to_4k_if_needed(self) -> None:
        if self.defaults.bool(Keys.private_ai_context_default_migrated_to_4k):
            return
        stored = self.defaults.object(Keys.private_ai_context_token_limit)
        if stored is None or stored == 2048:
            self.defaults.set(
                Keys.private_ai_context_token_limit, DEFAULT_PRIVATE_AI_CONTEXT_TOKEN_LIMIT
            )
        self.defaults.set(Keys.private_ai_context_default_migrated_to_4k, True)

    # --- model capability checks -----------------------------------------

    @staticmethod
    def is_reasoning_model(model: str) -> bool:
        """Whether the model reasons internally and rejects `temperature`.

        A provider namespace (OpenRouter's `openai/`) is stripped first, so
        `openai/o3` matches while `openai/gpt-4o` — which still wants
        temperature control — does not.
        """
        lowered = model.lower()
        _, separator, remainder = lowered.partition("/")
        family = remainder if separator else lowered
        return (
            family.startswith("gpt-5")
            or "gpt-5." in family
            or family.startswith("o1")
            or family.startswith("o3")
            or family.startswith("o4")
            or "gpt-oss" in family
            or ("deepseek" in family and "reasoner" in family)
        )

    @staticmethod
    def is_temperature_unsupported(model: str) -> bool:
        """Reasoning models plus the Anthropic models that deprecated temperature.

        Opus 4.7+, Sonnet 5 and Fable/Mythos 5 answer HTTP 400 when sent
        `temperature`; Sonnet 4.6 and older still accept it.
        """
        if SettingsStore.is_reasoning_model(model):
            return True
        # Normalise dotted OpenRouter IDs (claude-opus-4.8) to the hyphenated form.
        lowered = model.lower().replace(".", "-")
        return (
            "claude-opus-4-7" in lowered
            or "claude-opus-4-8" in lowered
            or "claude-sonnet-5" in lowered
            or "claude-fable" in lowered
            or "claude-mythos" in lowered
        )

    # =====================================================================
    # Hotkeys
    # =====================================================================

    @staticmethod
    def default_primary_dictation_shortcut() -> HotkeyShortcut:
        """Right Alt: reachable with one thumb and rarely bound elsewhere."""
        return HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE)

    @property
    def _legacy_hotkey_shortcut(self) -> HotkeyShortcut:
        return self._shortcut(Keys.hotkey_shortcut_key, None) or self.default_primary_dictation_shortcut()

    @property
    def primary_dictation_shortcuts(self) -> list[HotkeyShortcut]:
        fallback = self._legacy_hotkey_shortcut
        payload = self.defaults.json(Keys.primary_dictation_shortcuts_key)
        if isinstance(payload, list):
            shortcuts: list[HotkeyShortcut] = []
            for item in payload:
                if isinstance(item, dict):
                    try:
                        shortcuts.append(HotkeyShortcut.from_dict(item))
                    except (KeyError, ValueError, TypeError):
                        continue
            return self.normalized_primary_dictation_shortcuts(shortcuts, fallback)
        return [fallback]

    @primary_dictation_shortcuts.setter
    def primary_dictation_shortcuts(self, value: list[HotkeyShortcut]) -> None:
        self.object_will_change()
        shortcuts = self.normalized_primary_dictation_shortcuts(value, self._legacy_hotkey_shortcut)
        self.defaults.set(
            Keys.primary_dictation_shortcuts_key, [shortcut.to_dict() for shortcut in shortcuts]
        )
        self.defaults.set(Keys.hotkey_shortcut_key, shortcuts[0].to_dict())

    @property
    def hotkey_shortcut(self) -> HotkeyShortcut:
        shortcuts = self.primary_dictation_shortcuts
        return shortcuts[0] if shortcuts else self.default_primary_dictation_shortcut()

    @hotkey_shortcut.setter
    def hotkey_shortcut(self, value: HotkeyShortcut) -> None:
        self.object_will_change()
        shortcuts = self.normalized_primary_dictation_shortcuts(
            [value], self.default_primary_dictation_shortcut()
        )
        self.defaults.set(
            Keys.primary_dictation_shortcuts_key, [shortcut.to_dict() for shortcut in shortcuts]
        )
        self.defaults.set(Keys.hotkey_shortcut_key, shortcuts[0].to_dict())

    @property
    def primary_dictation_shortcut_display_string(self) -> str:
        displays = [
            shortcut.display_string.strip()
            for shortcut in self.primary_dictation_shortcuts
            if shortcut.display_string.strip()
        ]
        if not displays:
            return self.default_primary_dictation_shortcut().display_string
        return " / ".join(displays)

    @staticmethod
    def normalized_primary_dictation_shortcuts(
        shortcuts: Iterable[HotkeyShortcut], fallback: HotkeyShortcut
    ) -> list[HotkeyShortcut]:
        unique: list[HotkeyShortcut] = []
        for shortcut in shortcuts:
            if shortcut not in unique:
                unique.append(shortcut)
        if not unique:
            unique.append(fallback)
        return unique

    @property
    def hotkey_mode(self) -> str:
        raw = self.defaults.string(Keys.hotkey_mode)
        if raw in HotkeyActivationMode.ALL:
            return raw
        return (
            HotkeyActivationMode.HOLD
            if self.defaults.bool(Keys.press_and_hold_mode)
            else HotkeyActivationMode.TOGGLE
        )

    @hotkey_mode.setter
    def hotkey_mode(self, value: str) -> None:
        self.object_will_change()
        self.defaults.set(Keys.hotkey_mode, value)
        self.defaults.set(Keys.press_and_hold_mode, value == HotkeyActivationMode.HOLD)

    @property
    def press_and_hold_mode(self) -> bool:
        if self.defaults.has(Keys.hotkey_mode):
            return self.hotkey_mode == HotkeyActivationMode.HOLD
        return self.defaults.bool(Keys.press_and_hold_mode)

    @press_and_hold_mode.setter
    def press_and_hold_mode(self, value: bool) -> None:
        self.hotkey_mode = HotkeyActivationMode.HOLD if value else HotkeyActivationMode.TOGGLE

    @property
    def cancel_recording_hotkey_shortcut(self) -> HotkeyShortcut:
        return self._shortcut(
            Keys.cancel_recording_hotkey_shortcut, HotkeyShortcut.keyboard(KEY_ESC, ModifierFlags.NONE)
        )

    @cancel_recording_hotkey_shortcut.setter
    def cancel_recording_hotkey_shortcut(self, value: HotkeyShortcut) -> None:
        self._set_shortcut(Keys.cancel_recording_hotkey_shortcut, value)

    @property
    def paste_last_transcription_hotkey_shortcut(self) -> HotkeyShortcut | None:
        """Unbound by default — there is no safe universal default chord."""
        return self._shortcut(Keys.paste_last_transcription_hotkey_shortcut, None)

    @paste_last_transcription_hotkey_shortcut.setter
    def paste_last_transcription_hotkey_shortcut(self, value: HotkeyShortcut | None) -> None:
        self._set_shortcut(Keys.paste_last_transcription_hotkey_shortcut, value)

    @property
    def paste_last_transcription_shortcut_enabled(self) -> bool:
        return self._bool_default(Keys.paste_last_transcription_shortcut_enabled, False)

    @paste_last_transcription_shortcut_enabled.setter
    def paste_last_transcription_shortcut_enabled(self, value: bool) -> None:
        self._set(Keys.paste_last_transcription_shortcut_enabled, value)

    @property
    def prompt_mode_hotkey_shortcut(self) -> HotkeyShortcut:
        return self._shortcut(
            Keys.prompt_mode_hotkey_shortcut, HotkeyShortcut.keyboard(KEY_RIGHTMETA, ModifierFlags.NONE)
        )

    @prompt_mode_hotkey_shortcut.setter
    def prompt_mode_hotkey_shortcut(self, value: HotkeyShortcut) -> None:
        self._set_shortcut(Keys.prompt_mode_hotkey_shortcut, value)

    @property
    def prompt_mode_shortcut_enabled(self) -> bool:
        return self._bool_default(Keys.prompt_mode_shortcut_enabled, False)

    @prompt_mode_shortcut_enabled.setter
    def prompt_mode_shortcut_enabled(self, value: bool) -> None:
        self._set(Keys.prompt_mode_shortcut_enabled, value)

    @property
    def prompt_mode_selected_prompt_id(self) -> str | None:
        return self.defaults.string(Keys.prompt_mode_selected_prompt_id)

    @prompt_mode_selected_prompt_id.setter
    def prompt_mode_selected_prompt_id(self, value: str | None) -> None:
        self._set(Keys.prompt_mode_selected_prompt_id, value)

    @property
    def is_secondary_dictation_prompt_off(self) -> bool:
        return self.defaults.bool(Keys.secondary_dictation_prompt_off)

    @is_secondary_dictation_prompt_off.setter
    def is_secondary_dictation_prompt_off(self, value: bool) -> None:
        self._set(Keys.secondary_dictation_prompt_off, value)

    @property
    def command_mode_hotkey_shortcut(self) -> HotkeyShortcut | None:
        return self._shortcut(Keys.command_mode_hotkey_shortcut, None)

    @command_mode_hotkey_shortcut.setter
    def command_mode_hotkey_shortcut(self, value: HotkeyShortcut | None) -> None:
        self._set_shortcut(Keys.command_mode_hotkey_shortcut, value)

    @property
    def command_mode_shortcut_enabled(self) -> bool:
        return self._bool_default(Keys.command_mode_shortcut_enabled, False)

    @command_mode_shortcut_enabled.setter
    def command_mode_shortcut_enabled(self, value: bool) -> None:
        self._set(Keys.command_mode_shortcut_enabled, value)

    @property
    def command_mode_selected_model(self) -> str | None:
        return self.defaults.string(Keys.command_mode_selected_model)

    @command_mode_selected_model.setter
    def command_mode_selected_model(self, value: str | None) -> None:
        self._set(Keys.command_mode_selected_model, value)

    @property
    def command_mode_selected_provider_id(self) -> str:
        return self.defaults.string(Keys.command_mode_selected_provider_id) or ""

    @command_mode_selected_provider_id.setter
    def command_mode_selected_provider_id(self, value: str) -> None:
        self._set(Keys.command_mode_selected_provider_id, value)

    @property
    def command_mode_confirm_before_execute(self) -> bool:
        return self._bool_default(Keys.command_mode_confirm_before_execute, True)

    @command_mode_confirm_before_execute.setter
    def command_mode_confirm_before_execute(self, value: bool) -> None:
        self._set(Keys.command_mode_confirm_before_execute, value)

    @property
    def command_mode_linked_to_global(self) -> bool:
        return self._bool_default(Keys.command_mode_linked_to_global, True)

    @command_mode_linked_to_global.setter
    def command_mode_linked_to_global(self, value: bool) -> None:
        self._set(Keys.command_mode_linked_to_global, value)

    @property
    def rewrite_mode_hotkey_shortcut(self) -> HotkeyShortcut:
        from ..models.keycodes import KEY_R

        return self._shortcut(
            Keys.rewrite_mode_hotkey_shortcut, HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)
        )

    @rewrite_mode_hotkey_shortcut.setter
    def rewrite_mode_hotkey_shortcut(self, value: HotkeyShortcut) -> None:
        self._set_shortcut(Keys.rewrite_mode_hotkey_shortcut, value)

    @property
    def rewrite_mode_shortcut_enabled(self) -> bool:
        return self._bool_default(Keys.rewrite_mode_shortcut_enabled, False)

    @rewrite_mode_shortcut_enabled.setter
    def rewrite_mode_shortcut_enabled(self, value: bool) -> None:
        self._set(Keys.rewrite_mode_shortcut_enabled, value)

    @property
    def rewrite_mode_selected_model(self) -> str | None:
        return self.defaults.string(Keys.rewrite_mode_selected_model)

    @rewrite_mode_selected_model.setter
    def rewrite_mode_selected_model(self, value: str | None) -> None:
        self._set(Keys.rewrite_mode_selected_model, value)

    @property
    def rewrite_mode_selected_provider_id(self) -> str:
        return self.defaults.string(Keys.rewrite_mode_selected_provider_id) or ""

    @rewrite_mode_selected_provider_id.setter
    def rewrite_mode_selected_provider_id(self, value: str) -> None:
        self._set(Keys.rewrite_mode_selected_provider_id, value)

    @property
    def rewrite_mode_linked_to_global(self) -> bool:
        return self._bool_default(Keys.rewrite_mode_linked_to_global, True)

    @rewrite_mode_linked_to_global.setter
    def rewrite_mode_linked_to_global(self, value: bool) -> None:
        self._set(Keys.rewrite_mode_linked_to_global, value)

    # =====================================================================
    # Audio devices
    # =====================================================================

    @property
    def experimental_direct_audio_capture_enabled(self) -> bool:
        """Always on.

        Direct PipeWire/ALSA capture is the required backend; a stored
        "disabled" preference from an older build is deliberately ignored,
        so the newer setting always wins.
        """
        return True

    @property
    def preferred_input_device_uid(self) -> str | None:
        return self.defaults.string(Keys.preferred_input_device_uid)

    @preferred_input_device_uid.setter
    def preferred_input_device_uid(self, value: str | None) -> None:
        self.defaults.set(Keys.preferred_input_device_uid, value)

    @property
    def preferred_output_device_uid(self) -> str | None:
        return self.defaults.string(Keys.preferred_output_device_uid)

    @preferred_output_device_uid.setter
    def preferred_output_device_uid(self, value: str | None) -> None:
        self.defaults.set(Keys.preferred_output_device_uid, value)

    @property
    def microphone_priority(self) -> list[MicrophonePriorityEntry]:
        payload = self.defaults.json(Keys.microphone_priority)
        if not isinstance(payload, list):
            return []
        entries = [
            MicrophonePriorityEntry.from_dict(item)
            for item in payload
            if isinstance(item, dict) and item.get("uid")
        ]
        return self.normalized_microphone_priority(entries)

    @microphone_priority.setter
    def microphone_priority(self, value: list[MicrophonePriorityEntry]) -> None:
        entries = self.normalized_microphone_priority(value)
        if entries == self.microphone_priority:
            return
        self.object_will_change()
        if entries:
            self.defaults.set(Keys.microphone_priority, [entry.to_dict() for entry in entries])
        else:
            self.defaults.set(Keys.microphone_priority, [])
        if entries:
            self.preferred_input_device_uid = entries[0].uid

    @staticmethod
    def normalized_microphone_priority(
        entries: Iterable[MicrophonePriorityEntry],
    ) -> list[MicrophonePriorityEntry]:
        seen: set[str] = set()
        result: list[MicrophonePriorityEntry] = []
        for entry in entries:
            if not entry.uid or entry.uid in seen:
                continue
            seen.add(entry.uid)
            result.append(entry)
        return result

    @property
    def suppressed_microphone_uids(self) -> set[str]:
        return set(self.defaults.string_array(Keys.suppressed_microphone_uids) or [])

    @suppressed_microphone_uids.setter
    def suppressed_microphone_uids(self, value: Iterable[str]) -> None:
        uids = set(value)
        if not uids:
            self.defaults.remove(Keys.suppressed_microphone_uids)
        else:
            self.defaults.set(Keys.suppressed_microphone_uids, sorted(uids))

    @property
    def stored_mic_selection_mode_for_migration(self) -> MicrophoneSelectionMode:
        return MicrophoneSelectionMode.from_raw(
            self.defaults.string(Keys.microphone_selection_mode), MicrophoneSelectionMode.SYSTEM
        )

    @property
    def has_stored_mic_selection_mode_for_migration(self) -> bool:
        return self.defaults.has(Keys.microphone_selection_mode)

    @property
    def microphone_selection_mode(self) -> MicrophoneSelectionMode:
        return MicrophoneSelectionMode.from_raw(
            self.defaults.string(Keys.microphone_selection_mode), MicrophoneSelectionMode.MANUAL
        )

    @microphone_selection_mode.setter
    def microphone_selection_mode(self, value: MicrophoneSelectionMode) -> None:
        self._set_enum(Keys.microphone_selection_mode, value)

    @property
    def microphone_selection_migration_version(self) -> int:
        return self.defaults.integer(Keys.microphone_selection_migration_version)

    @microphone_selection_migration_version.setter
    def microphone_selection_migration_version(self, value: int) -> None:
        self.defaults.set(Keys.microphone_selection_migration_version, value)

    @property
    def show_microphone_change_alerts(self) -> bool:
        return self._bool_default(Keys.show_microphone_change_alerts, True)

    @show_microphone_change_alerts.setter
    def show_microphone_change_alerts(self, value: bool) -> None:
        self._set(Keys.show_microphone_change_alerts, value)

    def record_input_device_selection(self, uid: str, name: str | None = None) -> None:
        if not uid:
            return
        suppressed = self.suppressed_microphone_uids
        suppressed.discard(uid)
        self.suppressed_microphone_uids = suppressed

        existing_name = next((entry.name for entry in self.microphone_priority if entry.uid == uid), None)
        entries = [entry for entry in self.microphone_priority if entry.uid != uid]
        entries.insert(0, MicrophonePriorityEntry(uid=uid, name=name or existing_name or "Microphone"))
        self.microphone_priority = entries
        self.microphone_selection_mode = MicrophoneSelectionMode.MANUAL

    def reconcile_microphone_priority(self, devices: list) -> None:
        entries = list(self.microphone_priority)
        preferred_uid = self.preferred_input_device_uid
        suppressed = self.suppressed_microphone_uids

        if not entries and preferred_uid:
            name = next((d.name for d in devices if d.uid == preferred_uid), "Previously selected microphone")
            entries.append(MicrophonePriorityEntry(uid=preferred_uid, name=name))

        known = {entry.uid for entry in entries}
        new_entries: list[MicrophonePriorityEntry] = []
        for device in devices:
            if device.uid in suppressed or device.uid in known:
                continue
            known.add(device.uid)
            new_entries.append(MicrophonePriorityEntry(uid=device.uid, name=device.name))
        if new_entries:
            # Keep the user's first choice stable while making a newly connected
            # microphone the immediate fallback.
            index = min(1, len(entries))
            entries[index:index] = new_entries

        names_by_uid: dict[str, str] = {}
        for device in devices:
            names_by_uid.setdefault(device.uid, device.name)
        entries = [
            MicrophonePriorityEntry(uid=entry.uid, name=names_by_uid.get(entry.uid, entry.name))
            for entry in entries
        ]
        self.microphone_priority = entries

    def remove_microphone_from_priority(self, uid: str, is_connected: bool) -> None:
        if not uid:
            return
        suppressed = self.suppressed_microphone_uids
        if is_connected:
            suppressed.add(uid)
        else:
            suppressed.discard(uid)
        self.suppressed_microphone_uids = suppressed

        entries = [entry for entry in self.microphone_priority if entry.uid != uid]
        self.microphone_priority = entries
        if not entries:
            self.preferred_input_device_uid = None

    def restore_removed_microphones(self, devices: list) -> None:
        self.suppressed_microphone_uids = set()
        self.reconcile_microphone_priority(devices)

    def move_microphone_priority_before(self, uid: str, target_uid: str) -> None:
        if uid == target_uid:
            return
        entries = list(self.microphone_priority)
        source_index = next((i for i, e in enumerate(entries) if e.uid == uid), None)
        target_index = next((i for i, e in enumerate(entries) if e.uid == target_uid), None)
        if source_index is None or target_index is None:
            return
        entry = entries.pop(source_index)
        adjusted = target_index - 1 if source_index < target_index else target_index
        entries.insert(adjusted, entry)
        self.microphone_priority = entries

    def move_microphone_priority_by(self, uid: str, offset: int) -> None:
        entries = list(self.microphone_priority)
        source_index = next((i for i, e in enumerate(entries) if e.uid == uid), None)
        if source_index is None:
            return
        destination = min(max(source_index + offset, 0), len(entries) - 1)
        if destination == source_index:
            return
        entries[source_index], entries[destination] = entries[destination], entries[source_index]
        self.microphone_priority = entries

    @property
    def visualizer_noise_threshold(self) -> float:
        value = self.defaults.double(Keys.visualizer_noise_threshold)
        return 0.4 if value == 0.0 else value

    @visualizer_noise_threshold.setter
    def visualizer_noise_threshold(self, value: float) -> None:
        self.defaults.set(Keys.visualizer_noise_threshold, max(min(value, 0.95), 0.0))

    # =====================================================================
    # Capture behaviour
    # =====================================================================

    @property
    def enable_streaming_preview(self) -> bool:
        return self._bool_default(Keys.enable_streaming_preview, True)

    @enable_streaming_preview.setter
    def enable_streaming_preview(self, value: bool) -> None:
        self._set(Keys.enable_streaming_preview, value)

    @property
    def experimental_parakeet_unified_final_enabled(self) -> bool:
        return self._bool_default(Keys.experimental_parakeet_unified_final_enabled, True)

    @experimental_parakeet_unified_final_enabled.setter
    def experimental_parakeet_unified_final_enabled(self, value: bool) -> None:
        self._set(Keys.experimental_parakeet_unified_final_enabled, value)

    @property
    def show_history_performance_metrics(self) -> bool:
        return self._bool_default(Keys.show_history_performance_metrics, True)

    @show_history_performance_metrics.setter
    def show_history_performance_metrics(self, value: bool) -> None:
        self._set(Keys.show_history_performance_metrics, value)

    @property
    def skip_silent_recordings_enabled(self) -> bool:
        return self._bool_default(Keys.skip_silent_recordings_enabled, False)

    @skip_silent_recordings_enabled.setter
    def skip_silent_recordings_enabled(self, value: bool) -> None:
        self._set(Keys.skip_silent_recordings_enabled, value)

    @property
    def copy_transcription_to_clipboard(self) -> bool:
        return self.defaults.bool(Keys.copy_transcription_to_clipboard)

    @copy_transcription_to_clipboard.setter
    def copy_transcription_to_clipboard(self, value: bool) -> None:
        self.defaults.set(Keys.copy_transcription_to_clipboard, value)

    @property
    def text_insertion_mode(self) -> TextInsertionMode:
        return self._enum(Keys.text_insertion_mode, TextInsertionMode, TextInsertionMode.STANDARD)

    @text_insertion_mode.setter
    def text_insertion_mode(self, value: TextInsertionMode) -> None:
        self._set_enum(Keys.text_insertion_mode, value)

    @property
    def pause_media_during_transcription(self) -> bool:
        return self._bool_default(Keys.pause_media_during_transcription, False)

    @pause_media_during_transcription.setter
    def pause_media_during_transcription(self, value: bool) -> None:
        self._set(Keys.pause_media_during_transcription, value)

    # =====================================================================
    # Spoken send
    # =====================================================================

    @property
    def spoken_send_enabled(self) -> bool:
        return self._bool_default(Keys.spoken_send_enabled, False)

    @spoken_send_enabled.setter
    def spoken_send_enabled(self, value: bool) -> None:
        self._set(Keys.spoken_send_enabled, value)

    @property
    def spoken_send_immediately_enabled(self) -> bool:
        return self._bool_default(Keys.spoken_send_immediately_enabled, True)

    @spoken_send_immediately_enabled.setter
    def spoken_send_immediately_enabled(self, value: bool) -> None:
        self._set(Keys.spoken_send_immediately_enabled, value)

    @property
    def spoken_send_phrase(self) -> str:
        return self.defaults.string(Keys.spoken_send_phrase) or "send it"

    @spoken_send_phrase.setter
    def spoken_send_phrase(self, value: str) -> None:
        self._set(Keys.spoken_send_phrase, value)

    @property
    def spoken_send_key(self) -> SpokenSendKey:
        return self._enum(Keys.spoken_send_key, SpokenSendKey, SpokenSendKey.ENTER)

    @spoken_send_key.setter
    def spoken_send_key(self, value: SpokenSendKey) -> None:
        self._set_enum(Keys.spoken_send_key, value)

    # =====================================================================
    # Appearance
    # =====================================================================

    @property
    def accent_color_option(self) -> AccentColorOption:
        return self._enum(Keys.accent_color_option, AccentColorOption, AccentColorOption.ORANGE)

    @property
    def accent_color_was_chosen(self) -> bool:
        """Whether the user picked an accent, or is still following the desktop."""
        return self.defaults.object(Keys.accent_color_option) is not None

    @accent_color_option.setter
    def accent_color_option(self, value: AccentColorOption) -> None:
        self._set_enum(Keys.accent_color_option, value)

    @property
    def theme_preference(self) -> ThemePreference:
        return self._enum(Keys.theme_preference, ThemePreference, ThemePreference.SYSTEM)

    @theme_preference.setter
    def theme_preference(self, value: ThemePreference) -> None:
        self._set_enum(Keys.theme_preference, value)

    @property
    def overlay_position(self) -> OverlayPosition:
        return self._enum(Keys.overlay_position, OverlayPosition, OverlayPosition.BOTTOM)

    @overlay_position.setter
    def overlay_position(self, value: OverlayPosition) -> None:
        self._set_enum(Keys.overlay_position, value)

    @property
    def notch_presentation_mode(self) -> NotchPresentationMode:
        return self._enum(
            Keys.notch_presentation_mode, NotchPresentationMode, NotchPresentationMode.STANDARD
        )

    @notch_presentation_mode.setter
    def notch_presentation_mode(self, value: NotchPresentationMode) -> None:
        self._set_enum(Keys.notch_presentation_mode, value)

    @property
    def overlay_bottom_offset(self) -> float:
        value = self.defaults.double(Keys.overlay_bottom_offset)
        return 50.0 if value == 0.0 else value

    @overlay_bottom_offset.setter
    def overlay_bottom_offset(self, value: float) -> None:
        self._set(Keys.overlay_bottom_offset, max(min(value, 1000.0), 10.0))

    def migrate_overlay_bottom_offset_to_50_if_needed(self) -> None:
        if self.defaults.bool(Keys.overlay_bottom_offset_migrated_to_50):
            return
        self.defaults.set(Keys.overlay_bottom_offset_migrated_to_50, True)
        if not self.defaults.has(Keys.overlay_bottom_offset):
            self.defaults.set(Keys.overlay_bottom_offset, 50.0)

    @property
    def overlay_size(self) -> OverlaySize:
        return self._enum(Keys.overlay_size, OverlaySize, OverlaySize.MEDIUM)

    @overlay_size.setter
    def overlay_size(self, value: OverlaySize) -> None:
        self._set_enum(Keys.overlay_size, value)

    @property
    def transcription_preview_char_limit(self) -> int:
        stored = self.defaults.object(Keys.transcription_preview_char_limit)
        value = int(stored) if isinstance(stored, (int, float)) else DEFAULT_TRANSCRIPTION_PREVIEW_CHAR_LIMIT
        return self.normalized_transcription_preview_char_limit(value)

    @transcription_preview_char_limit.setter
    def transcription_preview_char_limit(self, value: int) -> None:
        clamped = self.normalized_transcription_preview_char_limit(value)
        if clamped == self.transcription_preview_char_limit:
            return
        self._set(Keys.transcription_preview_char_limit, clamped)

    @staticmethod
    def normalized_transcription_preview_char_limit(value: int) -> int:
        low, high = TRANSCRIPTION_PREVIEW_CHAR_LIMIT_RANGE
        clamped = max(low, min(high, value))
        offset = clamped - low
        snapped = (
            _round_half_up(offset / TRANSCRIPTION_PREVIEW_CHAR_LIMIT_STEP)
            * TRANSCRIPTION_PREVIEW_CHAR_LIMIT_STEP
        )
        return max(low, min(high, low + snapped))

    # =====================================================================
    # Sounds
    # =====================================================================

    @property
    def transcription_start_sound(self) -> TranscriptionStartSound:
        return self._enum(
            Keys.transcription_start_sound, TranscriptionStartSound, TranscriptionStartSound.FLUID_SFX_0
        )

    @transcription_start_sound.setter
    def transcription_start_sound(self, value: TranscriptionStartSound) -> None:
        self._set_enum(Keys.transcription_start_sound, value)

    def migrate_transcription_start_sound_if_needed(self) -> None:
        """Fold the legacy on/off toggle into the sound selection.

        Disabled maps to `none`; enabled keeps whichever sound is selected.
        """
        if self.defaults.has(Keys.transcription_start_sound):
            return
        if not self.defaults.has(Keys.enable_transcription_sounds):
            return
        enabled = self.defaults.bool(Keys.enable_transcription_sounds)
        self.defaults.set(
            Keys.transcription_start_sound,
            TranscriptionStartSound.FLUID_SFX_0.value if enabled else TranscriptionStartSound.NONE.value,
        )

    @property
    def transcription_sound_volume(self) -> float:
        value = self.defaults.double(Keys.transcription_sound_volume)
        return 0.5 if value == 0.0 and not self.defaults.has(Keys.transcription_sound_volume) else value

    @transcription_sound_volume.setter
    def transcription_sound_volume(self, value: float) -> None:
        self._set(Keys.transcription_sound_volume, max(0.0, min(1.0, value)))

    @property
    def transcription_sound_independent_volume(self) -> bool:
        return self._bool_default(Keys.transcription_sound_independent_volume, False)

    @transcription_sound_independent_volume.setter
    def transcription_sound_independent_volume(self, value: bool) -> None:
        self._set(Keys.transcription_sound_independent_volume, value)

    # =====================================================================
    # Formatting
    # =====================================================================

    @property
    def filler_words(self) -> list[str]:
        stored = self.defaults.string_array(Keys.filler_words)
        return list(stored) if stored is not None else list(DEFAULT_FILLER_WORDS)

    @filler_words.setter
    def filler_words(self, value: list[str]) -> None:
        self._set(Keys.filler_words, list(value))

    @property
    def remove_filler_words_enabled(self) -> bool:
        return self._bool_default(Keys.remove_filler_words_enabled, False)

    @remove_filler_words_enabled.setter
    def remove_filler_words_enabled(self, value: bool) -> None:
        self._set(Keys.remove_filler_words_enabled, value)

    @property
    def auto_convert_punctuation_enabled(self) -> bool:
        return self._bool_default(Keys.auto_convert_punctuation_enabled, True)

    @auto_convert_punctuation_enabled.setter
    def auto_convert_punctuation_enabled(self, value: bool) -> None:
        self._set(Keys.auto_convert_punctuation_enabled, value)

    @property
    def literal_dictation_formatting_enabled(self) -> bool:
        """Opt-in: slash-command and mention rewriting is off until enabled."""
        return self._bool_default(Keys.literal_dictation_formatting_enabled, False)

    @literal_dictation_formatting_enabled.setter
    def literal_dictation_formatting_enabled(self, value: bool) -> None:
        self._set(Keys.literal_dictation_formatting_enabled, value)

    @staticmethod
    def normalized_punctuation_dictionary_prefix(value: str) -> str | None:
        return normalized_alias(value)

    @property
    def punctuation_dictionary_prefix(self) -> str:
        stored = self.defaults.string(Keys.punctuation_dictionary_prefix)
        normalized = normalized_alias(stored or "")
        return normalized or DEFAULT_PUNCTUATION_DICTIONARY_PREFIX

    @punctuation_dictionary_prefix.setter
    def punctuation_dictionary_prefix(self, value: str) -> None:
        self._set(
            Keys.punctuation_dictionary_prefix,
            normalized_alias(value) or DEFAULT_PUNCTUATION_DICTIONARY_PREFIX,
        )

    @property
    def punctuation_dictionary_rules(self) -> list[PunctuationDictionaryRule]:
        payload = self.defaults.json(Keys.punctuation_dictionary_rules)
        if not isinstance(payload, list):
            return [
                PunctuationDictionaryRule(list(rule.aliases), rule.symbol)
                for rule in DEFAULT_PUNCTUATION_DICTIONARY_RULES
            ]
        return self._normalize_punctuation_rules(
            PunctuationDictionaryRule.from_dict(item) for item in payload if isinstance(item, dict)
        )

    @punctuation_dictionary_rules.setter
    def punctuation_dictionary_rules(self, value: list[PunctuationDictionaryRule]) -> None:
        self.object_will_change()
        rules = self._normalize_punctuation_rules(value)
        self.defaults.set(Keys.punctuation_dictionary_rules, [rule.to_dict() for rule in rules])

    @staticmethod
    def _normalize_punctuation_rules(
        rules: Iterable[PunctuationDictionaryRule],
    ) -> list[PunctuationDictionaryRule]:
        normalized: list[PunctuationDictionaryRule] = []
        for rule in rules:
            aliases = normalized_aliases(rule.aliases)
            symbol = normalized_symbol(rule.symbol)
            if not aliases or symbol is None:
                continue
            normalized.append(PunctuationDictionaryRule(aliases, symbol, id=rule.id))
        return normalized

    @property
    def spoken_formatting_action_rules(self) -> list[SpokenFormattingActionRule]:
        payload = self.defaults.json(Keys.spoken_formatting_action_rules)
        if not isinstance(payload, list):
            return [
                SpokenFormattingActionRule(rule.action, list(rule.aliases), rule.is_enabled)
                for rule in DEFAULT_SPOKEN_FORMATTING_ACTION_RULES
            ]
        decoded = [
            SpokenFormattingActionRule.from_dict(item) for item in payload if isinstance(item, dict)
        ]
        return self._removing_duplicate_spoken_formatting_aliases(
            self._ordered_spoken_formatting_rules(decoded, use_defaults_for_missing=True)
        )

    @spoken_formatting_action_rules.setter
    def spoken_formatting_action_rules(self, value: list[SpokenFormattingActionRule]) -> None:
        self.object_will_change()
        ordered = self._ordered_spoken_formatting_rules(value, use_defaults_for_missing=False)
        rules = self._removing_duplicate_spoken_formatting_aliases(ordered)
        self.defaults.set(Keys.spoken_formatting_action_rules, [rule.to_dict() for rule in rules])

    @staticmethod
    def _ordered_spoken_formatting_rules(
        rules: Iterable[SpokenFormattingActionRule], use_defaults_for_missing: bool
    ) -> list[SpokenFormattingActionRule]:
        by_action: dict[SpokenFormattingAction, SpokenFormattingActionRule] = {}
        for rule in rules:
            by_action.setdefault(rule.action, rule)
        ordered: list[SpokenFormattingActionRule] = []
        for action in SpokenFormattingAction:
            rule = by_action.get(action)
            if rule is None:
                if use_defaults_for_missing:
                    fallback = next(
                        (r for r in DEFAULT_SPOKEN_FORMATTING_ACTION_RULES if r.action is action), None
                    )
                    ordered.append(
                        SpokenFormattingActionRule(action, list(fallback.aliases), fallback.is_enabled)
                        if fallback
                        else SpokenFormattingActionRule(action, [], False)
                    )
                else:
                    ordered.append(SpokenFormattingActionRule(action, [], False))
                continue
            ordered.append(SpokenFormattingActionRule(action, list(rule.aliases), rule.is_enabled))
        return ordered

    def _removing_duplicate_spoken_formatting_aliases(
        self, rules: Iterable[SpokenFormattingActionRule]
    ) -> list[SpokenFormattingActionRule]:
        """Punctuation aliases win; an action cannot steal a claimed alias."""
        claimed: set[str] = set()
        for rule in self.punctuation_dictionary_rules:
            claimed.update(rule.aliases)
        result: list[SpokenFormattingActionRule] = []
        for rule in rules:
            unique: list[str] = []
            for alias in rule.aliases:
                if alias in claimed:
                    continue
                claimed.add(alias)
                unique.append(alias)
            result.append(SpokenFormattingActionRule(rule.action, unique, rule.is_enabled))
        return result

    # --- GAAV -------------------------------------------------------------

    @property
    def gaav_mode_enabled(self) -> bool:
        return self._bool_default(Keys.gaav_mode_enabled, False)

    @gaav_mode_enabled.setter
    def gaav_mode_enabled(self, value: bool) -> None:
        self._set(Keys.gaav_mode_enabled, value)

    @property
    def gaav_lowercase_first_letter_enabled(self) -> bool:
        return self._bool_default(Keys.gaav_lowercase_first_letter_enabled, self.gaav_mode_enabled)

    @gaav_lowercase_first_letter_enabled.setter
    def gaav_lowercase_first_letter_enabled(self, value: bool) -> None:
        self._set(Keys.gaav_lowercase_first_letter_enabled, value)

    @property
    def gaav_remove_trailing_period_enabled(self) -> bool:
        return self._bool_default(Keys.gaav_remove_trailing_period_enabled, self.gaav_mode_enabled)

    @gaav_remove_trailing_period_enabled.setter
    def gaav_remove_trailing_period_enabled(self, value: bool) -> None:
        self._set(Keys.gaav_remove_trailing_period_enabled, value)

    # --- continuous dictation ---------------------------------------------

    @property
    def continuous_dictation_mode_enabled(self) -> bool:
        return self._bool_default(Keys.continuous_dictation_mode_enabled, False)

    @continuous_dictation_mode_enabled.setter
    def continuous_dictation_mode_enabled(self, value: bool) -> None:
        self._set(Keys.continuous_dictation_mode_enabled, value)

    @property
    def continuous_dictation_spacing_enabled(self) -> bool:
        return self._bool_default(
            Keys.continuous_dictation_spacing_enabled, self.continuous_dictation_mode_enabled
        )

    @continuous_dictation_spacing_enabled.setter
    def continuous_dictation_spacing_enabled(self, value: bool) -> None:
        self._set(Keys.continuous_dictation_spacing_enabled, value)

    @property
    def context_aware_capitalization_enabled(self) -> bool:
        return self._bool_default(
            Keys.context_aware_capitalization_enabled, self.continuous_dictation_mode_enabled
        )

    @context_aware_capitalization_enabled.setter
    def context_aware_capitalization_enabled(self, value: bool) -> None:
        self._set(Keys.context_aware_capitalization_enabled, value)

    @property
    def needs_dictation_formatting_context(self) -> bool:
        return self.continuous_dictation_spacing_enabled or self.context_aware_capitalization_enabled

    # =====================================================================
    # Dictionary
    # =====================================================================

    @property
    def custom_dictionary_entries(self) -> list[CustomDictionaryEntry]:
        payload = self.defaults.json(Keys.custom_dictionary_entries)
        if not isinstance(payload, list):
            return []
        return [CustomDictionaryEntry.from_dict(item) for item in payload if isinstance(item, dict)]

    @custom_dictionary_entries.setter
    def custom_dictionary_entries(self, value: list[CustomDictionaryEntry]) -> None:
        self._set(Keys.custom_dictionary_entries, [entry.to_dict() for entry in value])

    @property
    def automatic_dictionary_learning_enabled(self) -> bool:
        return self._bool_default(Keys.automatic_dictionary_learning_enabled, True)

    @automatic_dictionary_learning_enabled.setter
    def automatic_dictionary_learning_enabled(self, value: bool) -> None:
        self._set(Keys.automatic_dictionary_learning_enabled, value)

    @property
    def automatic_dictionary_suggestion_frequency(self) -> AutomaticDictionarySuggestionFrequency:
        return AutomaticDictionarySuggestionFrequency.from_raw(
            self.defaults.integer(Keys.automatic_dictionary_suggestion_frequency)
        )

    @automatic_dictionary_suggestion_frequency.setter
    def automatic_dictionary_suggestion_frequency(
        self, value: AutomaticDictionarySuggestionFrequency
    ) -> None:
        self._set(Keys.automatic_dictionary_suggestion_frequency, value.value)

    @property
    def vocabulary_boosting_enabled(self) -> bool:
        return self._bool_default(Keys.vocabulary_boosting_enabled, False)

    @vocabulary_boosting_enabled.setter
    def vocabulary_boosting_enabled(self, value: bool) -> None:
        self._set(Keys.vocabulary_boosting_enabled, value)

    @property
    def pronunciation_matching_enabled(self) -> bool:
        return self._bool_default(Keys.pronunciation_matching_enabled, False)

    @pronunciation_matching_enabled.setter
    def pronunciation_matching_enabled(self, value: bool) -> None:
        self._set(Keys.pronunciation_matching_enabled, value)

    # =====================================================================
    # Speech model
    # =====================================================================

    @property
    def selected_speech_model(self) -> SpeechModel:
        raw = self.defaults.string(Keys.selected_speech_model)
        model = SpeechModel.from_raw(raw, None)
        return SpeechModel.supported_or_default(model)

    @selected_speech_model.setter
    def selected_speech_model(self, value: SpeechModel) -> None:
        self._set_enum(Keys.selected_speech_model, value)

    @property
    def selected_whisper_language_code(self) -> str | None:
        """`None` means automatic detection."""
        stored = self.defaults.string(Keys.selected_whisper_language_code)
        if stored is None or stored == AUTOMATIC_WHISPER_LANGUAGE_CODE:
            return None
        return stored

    @selected_whisper_language_code.setter
    def selected_whisper_language_code(self, value: str | None) -> None:
        self._set(
            Keys.selected_whisper_language_code,
            value if value is not None else AUTOMATIC_WHISPER_LANGUAGE_CODE,
        )

    @staticmethod
    def whisper_language_backup_value(code: str | None) -> str:
        return code if code is not None else AUTOMATIC_WHISPER_LANGUAGE_CODE

    @staticmethod
    def whisper_language_code_from_backup_value(value: str | None) -> str | None:
        if value is None or value == AUTOMATIC_WHISPER_LANGUAGE_CODE:
            return None
        return value

    @property
    def selected_cohere_language(self) -> CohereLanguage:
        return self._enum(Keys.selected_cohere_language, CohereLanguage, CohereLanguage.ENGLISH)

    @selected_cohere_language.setter
    def selected_cohere_language(self, value: CohereLanguage) -> None:
        self._set_enum(Keys.selected_cohere_language, value)

    @property
    def selected_nemotron_language(self) -> str:
        """The stored Nemotron language, normalized.

        Unknown values fall back to English, and the four codes that were
        renamed when regional variants arrived map forward.
        """
        stored = self.defaults.string(Keys.selected_nemotron_language)
        if stored:
            language = NemotronLanguage.supported_language(stored)
            if language is not None:
                return language.raw_value
        return "en"

    @selected_nemotron_language.setter
    def selected_nemotron_language(self, value: str) -> None:
        self._set(Keys.selected_nemotron_language, value)

    @property
    def external_model_artifacts_directories(self) -> list[str]:
        return self.defaults.string_array(Keys.external_model_artifacts_directories) or []

    @external_model_artifacts_directories.setter
    def external_model_artifacts_directories(self, value: list[str]) -> None:
        self._set(Keys.external_model_artifacts_directories, list(value))

    # =====================================================================
    # History and stats
    # =====================================================================

    @property
    def save_transcription_history(self) -> bool:
        return self._bool_default(Keys.save_transcription_history, True)

    @save_transcription_history.setter
    def save_transcription_history(self, value: bool) -> None:
        self._set(Keys.save_transcription_history, value)

    @property
    def history_auto_clear_interval(self) -> HistoryAutoClearInterval:
        return self._enum(
            Keys.history_auto_clear_interval,
            HistoryAutoClearInterval,
            HistoryAutoClearInterval.NEVER,
        )

    @history_auto_clear_interval.setter
    def history_auto_clear_interval(self, value: HistoryAutoClearInterval) -> None:
        self._set_enum(Keys.history_auto_clear_interval, value)

    @property
    def save_audio_with_transcription_history(self) -> bool:
        return self._bool_default(Keys.save_audio_with_transcription_history, False)

    @save_audio_with_transcription_history.setter
    def save_audio_with_transcription_history(self, value: bool) -> None:
        self._set(Keys.save_audio_with_transcription_history, value)

    @property
    def audio_history_budget_gb(self) -> float:
        value = self.defaults.double(Keys.audio_history_budget_gb)
        return 1.0 if value == 0.0 else value

    @audio_history_budget_gb.setter
    def audio_history_budget_gb(self, value: float) -> None:
        self._set(Keys.audio_history_budget_gb, max(0.1, value))

    @property
    def user_typing_wpm(self) -> int:
        value = self.defaults.integer(Keys.user_typing_wpm)
        return 40 if value == 0 else value

    @user_typing_wpm.setter
    def user_typing_wpm(self, value: int) -> None:
        self._set(Keys.user_typing_wpm, value)

    @property
    def weekends_dont_break_streak(self) -> bool:
        return self._bool_default(Keys.weekends_dont_break_streak, False)

    @weekends_dont_break_streak.setter
    def weekends_dont_break_streak(self, value: bool) -> None:
        self._set(Keys.weekends_dont_break_streak, value)

    @property
    def file_transcription_speaker_labels_enabled(self) -> bool:
        return self.defaults.bool(Keys.file_transcription_speaker_labels_enabled)

    @file_transcription_speaker_labels_enabled.setter
    def file_transcription_speaker_labels_enabled(self, value: bool) -> None:
        self._set(Keys.file_transcription_speaker_labels_enabled, value)

    @property
    def file_transcription_expected_speaker_count(self) -> int:
        return self.defaults.integer(Keys.file_transcription_expected_speaker_count)

    @file_transcription_expected_speaker_count.setter
    def file_transcription_expected_speaker_count(self, value: int) -> None:
        self._set(Keys.file_transcription_expected_speaker_count, value)

    # =====================================================================
    # Application behaviour
    # =====================================================================

    @property
    def enable_debug_logs(self) -> bool:
        return self._bool_default(Keys.enable_debug_logs, False)

    @enable_debug_logs.setter
    def enable_debug_logs(self, value: bool) -> None:
        self._set(Keys.enable_debug_logs, value)

    @property
    def share_detailed_analytics(self) -> bool:
        return self._bool_default(Keys.share_anonymous_analytics, True)

    @share_detailed_analytics.setter
    def share_detailed_analytics(self, value: bool) -> None:
        self._set(Keys.share_anonymous_analytics, value)

    @property
    def hide_from_dock_and_app_switcher(self) -> bool:
        """Inverse of `ShowInDock`; on Linux this hides the taskbar entry."""
        return not self._bool_default(Keys.show_in_dock, True)

    @hide_from_dock_and_app_switcher.setter
    def hide_from_dock_and_app_switcher(self, value: bool) -> None:
        self._set(Keys.show_in_dock, not value)

    @property
    def show_main_window_at_login_launch(self) -> bool:
        return self._bool_default(Keys.show_main_window_at_login_launch, True)

    @show_main_window_at_login_launch.setter
    def show_main_window_at_login_launch(self, value: bool) -> None:
        self._set(Keys.show_main_window_at_login_launch, value)

    @property
    def auto_update_check_enabled(self) -> bool:
        return self._bool_default(Keys.auto_update_check_enabled, True)

    @auto_update_check_enabled.setter
    def auto_update_check_enabled(self, value: bool) -> None:
        self._set(Keys.auto_update_check_enabled, value)

    @property
    def beta_releases_enabled(self) -> bool:
        return self._bool_default(Keys.beta_releases_enabled, False)

    @beta_releases_enabled.setter
    def beta_releases_enabled(self, value: bool) -> None:
        self._set(Keys.beta_releases_enabled, value)
        self.last_update_check_date = None
        self.clear_update_snooze()

    @property
    def last_update_check_date(self) -> datetime | None:
        from .settings_types import parse_iso

        raw = self.defaults.string(Keys.last_update_check_date)
        return parse_iso(raw) if raw else None

    @last_update_check_date.setter
    def last_update_check_date(self, value: datetime | None) -> None:
        from .settings_types import iso

        self.defaults.set(Keys.last_update_check_date, iso(value) if value else None)

    def clear_update_snooze(self) -> None:
        self.defaults.remove(Keys.update_prompt_snoozed_until)
        self.defaults.remove(Keys.snoozed_update_version)

    @property
    def onboarding_completed(self) -> bool:
        """Absent means completed.

        An upgrade from a build that predates onboarding must not drop the
        user into a setup flow, so the unset state reads as done.
        `bootstrap_onboarding_state` is what writes the real value on a
        genuinely new install.
        """
        if self.defaults.object(Keys.onboarding_completed) is None:
            return True
        return self.defaults.bool(Keys.onboarding_completed)

    @onboarding_completed.setter
    def onboarding_completed(self, value: bool) -> None:
        self._set(Keys.onboarding_completed, value)
        if value:
            # Finishing the flow also clears a pending manual restart.
            self.defaults.set(Keys.manual_onboarding_reset_requested, False)
            self.defaults.remove(Keys.manual_onboarding_reset_requested_at)

    @property
    def onboarding_current_step(self) -> int:
        return max(0, min(LAST_ONBOARDING_STEP, self.defaults.integer(Keys.onboarding_current_step)))

    @onboarding_current_step.setter
    def onboarding_current_step(self, value: int) -> None:
        self._set(Keys.onboarding_current_step, max(0, min(LAST_ONBOARDING_STEP, value)))

    @property
    def onboarding_ai_skipped(self) -> bool:
        return self.defaults.bool(Keys.onboarding_ai_skipped)

    @onboarding_ai_skipped.setter
    def onboarding_ai_skipped(self, value: bool) -> None:
        self._set(Keys.onboarding_ai_skipped, value)

    @property
    def onboarding_playground_validated(self) -> bool:
        return self.defaults.bool(Keys.onboarding_playground_validated)

    @onboarding_playground_validated.setter
    def onboarding_playground_validated(self, value: bool) -> None:
        self._set(Keys.onboarding_playground_validated, value)

    @property
    def onboarding_playground_skipped(self) -> bool:
        return self.defaults.bool(Keys.onboarding_playground_skipped)

    @onboarding_playground_skipped.setter
    def onboarding_playground_skipped(self, value: bool) -> None:
        self._set(Keys.onboarding_playground_skipped, value)

    @property
    def playground_used(self) -> bool:
        return self.defaults.bool(Keys.playground_used)

    @playground_used.setter
    def playground_used(self, value: bool) -> None:
        self._set(Keys.playground_used, value)

    @property
    def onboarding_selected_language_id(self) -> str:
        stored = (self.defaults.string(Keys.onboarding_selected_language_id) or "").strip()
        return stored or "en"

    @onboarding_selected_language_id.setter
    def onboarding_selected_language_id(self, value: str | None) -> None:
        normalized = (value or "").strip()
        self._set(Keys.onboarding_selected_language_id, normalized or "en")

    @property
    def should_show_onboarding(self) -> bool:
        return not self.onboarding_completed

    @property
    def analytics_onboarding_origin(self) -> OnboardingOrigin:
        return (
            OnboardingOrigin.MANUAL_RESTART
            if self.defaults.bool(Keys.manual_onboarding_reset_requested)
            else OnboardingOrigin.FIRST_RUN
        )

    def bootstrap_onboarding_state(self, is_true_first_open: bool) -> None:
        """Decide once, on the first launch, whether to show onboarding."""
        if self.defaults.object(Keys.onboarding_completed) is not None:
            return

        self.object_will_change()
        should_show = is_true_first_open and not self.has_legacy_usage_signals()
        self.defaults.set(Keys.onboarding_completed, not should_show)
        self._reset_onboarding_step_state()

    def reset_onboarding_progress(self) -> None:
        """Run the flow again, at the user's request."""
        self.object_will_change()
        self.defaults.set(Keys.onboarding_completed, False)
        self.defaults.set(Keys.manual_onboarding_reset_requested, True)
        self.defaults.set(
            Keys.manual_onboarding_reset_requested_at, datetime.now().timestamp()
        )
        self._reset_onboarding_step_state()
        self.defaults.set(Keys.playground_used, False)

    def _reset_onboarding_step_state(self) -> None:
        self.defaults.set(Keys.onboarding_current_step, 0)
        self.defaults.set(Keys.onboarding_ai_skipped, False)
        self.defaults.set(Keys.onboarding_playground_validated, False)
        self.defaults.set(Keys.onboarding_playground_skipped, False)
        self.defaults.set(Keys.onboarding_selected_language_id, "en")

    def repair_forced_onboarding_reset_if_needed(self, first_open_at: float | None) -> None:
        """Undo the 1.6.2 migration that re-onboarded everybody.

        That build wrote `OnboardingGeneration` and cleared the completion
        flag for every install. Anyone with signs of prior use — or a
        recorded first open from before that build — is put back.
        """
        had_opened_before = first_open_at is not None and (
            first_open_at < FORCED_ONBOARDING_RESET_INTRODUCED_AT
        )
        has_existing_install = self.has_legacy_usage_signals() or had_opened_before
        has_current_manual_reset = (
            self.defaults.bool(Keys.manual_onboarding_reset_requested)
            and self.defaults.object(Keys.manual_onboarding_reset_requested_at) is not None
        )
        if (
            self.defaults.object(Keys.onboarding_generation) is None
            or self.defaults.bool(Keys.onboarding_completed) is not False
            or has_current_manual_reset
            or not has_existing_install
        ):
            return

        self.object_will_change()
        self.defaults.set(Keys.onboarding_completed, True)
        self.defaults.set(Keys.manual_onboarding_reset_requested, False)
        self.defaults.remove(Keys.manual_onboarding_reset_requested_at)
        self.defaults.set(Keys.onboarding_current_step, 0)
        self.defaults.set(Keys.onboarding_ai_skipped, False)
        self.defaults.set(Keys.onboarding_playground_validated, False)
        self.defaults.set(Keys.onboarding_playground_skipped, False)

    def has_legacy_usage_signals(self) -> bool:
        """Whether this install has clearly been used before."""
        for key in (
            Keys.playground_used,
            Keys.hotkey_shortcut_key,
            Keys.primary_dictation_shortcuts_key,
            Keys.selected_provider_id,
            Keys.custom_dictionary_entries,
        ):
            if self.defaults.object(key) is not None:
                return True
        raw_model = self.defaults.string(Keys.selected_speech_model)
        if raw_model and raw_model != SpeechModel.default_model().value:
            return True
        return bool(self.saved_providers)

    @property
    def launch_at_startup(self) -> bool:
        return self.defaults.bool(Keys.launch_at_startup)

    @launch_at_startup.setter
    def launch_at_startup(self, value: bool) -> None:
        self._set(Keys.launch_at_startup, value)

    # =====================================================================
    # Prompts
    # =====================================================================

    @property
    def dictation_prompt_profiles(self) -> list[DictationPromptProfile]:
        payload = self.defaults.json(Keys.dictation_prompt_profiles)
        if not isinstance(payload, list):
            return []
        profiles: list[DictationPromptProfile] = []
        for item in payload:
            if isinstance(item, dict) and item.get("id"):
                try:
                    profiles.append(DictationPromptProfile.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
        return profiles

    @dictation_prompt_profiles.setter
    def dictation_prompt_profiles(self, value: list[DictationPromptProfile]) -> None:
        self._set(Keys.dictation_prompt_profiles, [profile.to_dict() for profile in value])

    @property
    def app_prompt_bindings(self) -> list[AppPromptBinding]:
        payload = self.defaults.json(Keys.app_prompt_bindings)
        if not isinstance(payload, list):
            return []
        bindings: list[AppPromptBinding] = []
        for item in payload:
            if isinstance(item, dict) and item.get("id"):
                try:
                    bindings.append(AppPromptBinding.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
        return bindings

    @app_prompt_bindings.setter
    def app_prompt_bindings(self, value: list[AppPromptBinding]) -> None:
        self._set(Keys.app_prompt_bindings, [binding.to_dict() for binding in value])

    @property
    def dictation_prompt_configurations(self) -> dict[str, DictationPromptConfiguration]:
        payload = self.defaults.json(Keys.dictation_prompt_configurations)
        if not isinstance(payload, dict):
            return {}
        return {
            key: DictationPromptConfiguration.from_dict(value)
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    @dictation_prompt_configurations.setter
    def dictation_prompt_configurations(self, value: dict[str, DictationPromptConfiguration]) -> None:
        self._set(
            Keys.dictation_prompt_configurations,
            {key: item.to_dict() for key, item in value.items()},
        )

    @property
    def selected_dictation_prompt_id(self) -> str | None:
        value = self.defaults.string(Keys.selected_dictation_prompt_id)
        return value or None

    @selected_dictation_prompt_id.setter
    def selected_dictation_prompt_id(self, value: str | None) -> None:
        self._set(Keys.selected_dictation_prompt_id, value)

    @property
    def is_dictation_prompt_off(self) -> bool:
        return self.defaults.bool(Keys.dictation_prompt_off)

    @is_dictation_prompt_off.setter
    def is_dictation_prompt_off(self, value: bool) -> None:
        self._set(Keys.dictation_prompt_off, value)

    @property
    def is_edit_prompt_off(self) -> bool:
        return self.defaults.bool(Keys.edit_prompt_off)

    @is_edit_prompt_off.setter
    def is_edit_prompt_off(self, value: bool) -> None:
        self._set(Keys.edit_prompt_off, value)

    @property
    def send_custom_prompt_only(self) -> bool:
        return self.defaults.bool(Keys.send_custom_prompt_only)

    @send_custom_prompt_only.setter
    def send_custom_prompt_only(self, value: bool) -> None:
        self._set(Keys.send_custom_prompt_only, value)

    @property
    def selected_edit_prompt_id(self) -> str | None:
        for key in (
            Keys.selected_edit_prompt_id,
            Keys.selected_rewrite_prompt_id,
            Keys.selected_write_prompt_id,
        ):
            value = self.defaults.string(key)
            if value:
                return value
        return None

    @selected_edit_prompt_id.setter
    def selected_edit_prompt_id(self, value: str | None) -> None:
        self._set(Keys.selected_edit_prompt_id, value)

    @property
    def default_dictation_prompt_override(self) -> str | None:
        """`None` = built-in prompt, `""` = deliberately empty system prompt."""
        if not self.defaults.has(Keys.default_dictation_prompt_override):
            return None
        return self.defaults.string(Keys.default_dictation_prompt_override) or ""

    @default_dictation_prompt_override.setter
    def default_dictation_prompt_override(self, value: str | None) -> None:
        self._set(Keys.default_dictation_prompt_override, value)

    @property
    def default_edit_prompt_override(self) -> str | None:
        for key in (
            Keys.default_edit_prompt_override,
            Keys.default_rewrite_prompt_override,
            Keys.default_write_prompt_override,
        ):
            if self.defaults.has(key):
                return self.defaults.string(key) or ""
        return None

    @default_edit_prompt_override.setter
    def default_edit_prompt_override(self, value: str | None) -> None:
        self._set(Keys.default_edit_prompt_override, value)

    @property
    def dictation_prompt_routing_scope(self) -> PromptRoutingScope:
        return self._enum(
            Keys.dictation_prompt_routing_scope, PromptRoutingScope, PromptRoutingScope.ALL_APPS
        )

    @dictation_prompt_routing_scope.setter
    def dictation_prompt_routing_scope(self, value: PromptRoutingScope) -> None:
        self._set_enum(Keys.dictation_prompt_routing_scope, value)

    @property
    def edit_prompt_routing_scope(self) -> PromptRoutingScope:
        return self._enum(Keys.edit_prompt_routing_scope, PromptRoutingScope, PromptRoutingScope.ALL_APPS)

    @edit_prompt_routing_scope.setter
    def edit_prompt_routing_scope(self, value: PromptRoutingScope) -> None:
        self._set_enum(Keys.edit_prompt_routing_scope, value)

    @property
    def dictation_prompt_selection(self) -> DictationPromptSelection:
        if self.is_dictation_prompt_off:
            return DictationPromptSelection.OFF
        identifier = self.selected_dictation_prompt_id
        if identifier is None:
            return DictationPromptSelection.DEFAULT
        if identifier == PRIVATE_AI_PROMPT_CONFIGURATION_KEY:
            return DictationPromptSelection.PRIVATE_AI
        return DictationPromptSelection.profile(identifier)

    @dictation_prompt_selection.setter
    def dictation_prompt_selection(self, value: DictationPromptSelection) -> None:
        if value.kind == "off":
            self.is_dictation_prompt_off = True
            return
        self.is_dictation_prompt_off = False
        if value.kind == "default":
            self.selected_dictation_prompt_id = None
        elif value.kind == "privateAI":
            self.selected_dictation_prompt_id = PRIVATE_AI_PROMPT_CONFIGURATION_KEY
        else:
            self.selected_dictation_prompt_id = value.profile_id

    def dictation_prompt_configuration_key(self, selection: DictationPromptSelection) -> str | None:
        if selection.kind == "off":
            return None
        if selection.kind == "default":
            return DEFAULT_PROMPT_CONFIGURATION_KEY
        if selection.kind == "privateAI":
            return PRIVATE_AI_PROMPT_CONFIGURATION_KEY
        trimmed = (selection.profile_id or "").strip()
        # Namespaced so a profile whose id is "__default__" cannot collide.
        return f"{PROFILE_CONFIGURATION_KEY_PREFIX}{trimmed}" if trimmed else None

    def dictation_prompt_selection_for_configuration_key(
        self, key: str
    ) -> DictationPromptSelection | None:
        if key == PRIVATE_AI_PROMPT_CONFIGURATION_KEY:
            return DictationPromptSelection.PRIVATE_AI
        if key == DEFAULT_PROMPT_CONFIGURATION_KEY:
            return DictationPromptSelection.DEFAULT
        if key.startswith(PROFILE_CONFIGURATION_KEY_PREFIX):
            identifier = key[len(PROFILE_CONFIGURATION_KEY_PREFIX) :]
            if self._has_dictate_profile(identifier):
                return DictationPromptSelection.profile(identifier)
        return None

    def dictation_prompt_configuration(
        self, selection: DictationPromptSelection
    ) -> DictationPromptConfiguration:
        key = self.dictation_prompt_configuration_key(selection)
        if key is None:
            return DictationPromptConfiguration()
        return self.dictation_prompt_configurations.get(key) or DictationPromptConfiguration()

    def set_dictation_prompt_configuration(
        self, configuration: DictationPromptConfiguration, selection: DictationPromptSelection
    ) -> None:
        key = self.dictation_prompt_configuration_key(selection)
        if key is None:
            return
        provider_id = configuration.provider_id.strip()
        model_name = configuration.model_name.strip()
        configurations = self.dictation_prompt_configurations
        if configuration.shortcut is None and not provider_id and not model_name:
            # An empty configuration is stored as no configuration at all.
            configurations.pop(key, None)
        else:
            configurations[key] = DictationPromptConfiguration(
                shortcut=configuration.shortcut, provider_id=provider_id, model_name=model_name
            )
        self.dictation_prompt_configurations = configurations

    def remove_dictation_prompt_configuration(self, selection: DictationPromptSelection) -> None:
        key = self.dictation_prompt_configuration_key(selection)
        if key is None:
            return
        configurations = self.dictation_prompt_configurations
        if configurations.pop(key, None) is not None:
            self.dictation_prompt_configurations = configurations

    def dictation_prompt_shortcut_assignments(
        self,
    ) -> list[tuple[DictationPromptSelection, HotkeyShortcut]]:
        assignments = []
        for key, configuration in self.dictation_prompt_configurations.items():
            if configuration.shortcut is None:
                continue
            selection = self.dictation_prompt_selection_for_configuration_key(key)
            if selection is not None:
                assignments.append((selection, configuration.shortcut))
        return assignments

    def _has_dictate_profile(self, identifier: str) -> bool:
        return any(
            profile.id == identifier and profile.mode.normalized is PromptMode.DICTATE
            for profile in self.dictation_prompt_profiles
        )

    # --- per-app prompt bindings -----------------------------------------

    def prompt_routing_scope(self, mode: PromptMode) -> PromptRoutingScope:
        return (
            self.edit_prompt_routing_scope
            if mode.normalized is PromptMode.EDIT
            else self.dictation_prompt_routing_scope
        )

    def app_prompt_binding(self, mode: PromptMode, app_bundle_id: str | None):
        """The binding for this app, if there is one.

        Bundle ids are matched case-insensitively, because the desktop
        reports app ids with inconsistent casing.
        """
        if not app_bundle_id:
            return None
        wanted = app_bundle_id.strip().casefold()
        if not wanted:
            return None
        normalized_mode = mode.normalized
        for binding in self.app_prompt_bindings:
            if (
                binding.mode.normalized is normalized_mode
                and binding.app_bundle_id.strip().casefold() == wanted
            ):
                return binding
        return None

    def has_app_prompt_binding(self, mode: PromptMode, app_bundle_id: str | None) -> bool:
        return self.app_prompt_binding(mode, app_bundle_id) is not None

    # --- resolving which prompt to send ----------------------------------

    #: A prompt may fold the transcript in wherever it likes.
    TRANSCRIPT_PLACEHOLDER = "${transcript}"

    @staticmethod
    def combine_base_prompt(base: str, body: str) -> str:
        """Put the hidden base prompt in front of a custom one, once."""
        base = base.strip()
        body = body.strip()
        if body.lower().startswith(base.lower()):
            return body  # Already carries it; do not say it twice.
        return body and f"{base}\n\n{body}" or base

    @staticmethod
    def strip_base_prompt(base: str, text: str) -> str:
        """Remove a base prompt an older build folded into a saved profile."""
        base = base.strip()
        trimmed = text.strip()
        if base and trimmed.lower().startswith(base.lower()):
            return trimmed[len(base) :].strip()
        return trimmed

    def system_prompt_for_profile_body(self, base: str, body: str) -> str:
        """A custom prompt, with or without the base in front of it.

        "Send custom prompt only" means exactly that: the built-in
        instructions are left out rather than silently prepended.
        """
        body = body.strip()
        if self.send_custom_prompt_only:
            return body
        return self.combine_base_prompt(base, body)

    def selected_dictation_prompt_profile(self, app_bundle_id: str | None = None):
        """The prompt profile in force, taking per-app bindings into account."""
        from ..services.provider_routing import effective_prompt_selection

        selection = effective_prompt_selection(
            self, DictationShortcutSlot.PRIMARY, app_bundle_id
        )
        if selection.kind != "profile":
            return None
        return next(
            (
                profile
                for profile in self.dictation_prompt_profiles
                if profile.id == selection.profile_id
                and profile.mode.normalized is PromptMode.DICTATE
            ),
            None,
        )

    def effective_dictation_system_prompt(
        self, base: str, app_bundle_id: str | None = None
    ) -> str:
        """The system prompt this dictation should actually carry.

        Order: an explicit Off means none at all; a selected profile wins;
        otherwise the user's default override, else the built-in base.
        """
        from ..services.provider_routing import effective_prompt_selection

        selection = effective_prompt_selection(
            self, DictationShortcutSlot.PRIMARY, app_bundle_id
        )
        if selection.kind == "off":
            return ""

        profile = self.selected_dictation_prompt_profile(app_bundle_id)
        if profile is not None:
            body = self.strip_base_prompt(base, profile.prompt)
            if body:
                return self.system_prompt_for_profile_body(base, body)

        override = self.default_dictation_prompt_override
        if override is not None:
            trimmed = override.strip()
            if trimmed:
                return self.system_prompt_for_profile_body(base, trimmed)
            return ""
        return base

    @classmethod
    def render_dictation_user_message(cls, prompt_text: str, transcript: str) -> str:
        """Fold the transcript into the prompt, or append it."""
        if cls.TRANSCRIPT_PLACEHOLDER in prompt_text:
            return prompt_text.replace(cls.TRANSCRIPT_PLACEHOLDER, transcript)
        if not prompt_text.strip():
            return transcript
        return f"{prompt_text}\n\n{transcript}"

    def ai_enhancement_enabled_for_app(self, app_bundle_id: str | None) -> bool:
        """Whether AI cleanup should run for the app now focused.

        A per-app override wins over the global switch, so dictating into a
        coding agent can stay raw while a chat client still gets cleaned up.
        """
        binding = self.app_prompt_binding(PromptMode.DICTATE, app_bundle_id)
        if binding is None:
            return self.enable_ai_processing
        return binding.ai_enhancement.resolve(self.enable_ai_processing)

    @staticmethod
    def dictation_selection_supports_app_override(selection: DictationPromptSelection) -> bool:
        """Only Default and profile selections can be overridden per app.

        Off means off everywhere, and Private AI is a deliberate choice of
        engine rather than of wording.
        """
        return selection.kind in ("default", "profile")

    def normalize_prompt_selections_if_needed(self) -> None:
        """Drop selections pointing at profiles that no longer exist."""
        profile_ids = {profile.id for profile in self.dictation_prompt_profiles}

        selected = self.selected_dictation_prompt_id
        if selected and selected != PRIVATE_AI_PROMPT_CONFIGURATION_KEY and selected not in profile_ids:
            self.selected_dictation_prompt_id = None

        edit_selected = self.defaults.string(Keys.selected_edit_prompt_id)
        if edit_selected and edit_selected not in profile_ids:
            self.defaults.remove(Keys.selected_edit_prompt_id)

        prompt_mode_selected = self.prompt_mode_selected_prompt_id
        if prompt_mode_selected and prompt_mode_selected not in profile_ids:
            self.prompt_mode_selected_prompt_id = None

        bindings = self.app_prompt_bindings
        reconciled: list[AppPromptBinding] = []
        changed = False
        for binding in bindings:
            if binding.prompt_id and binding.prompt_id not in profile_ids:
                changed = True
                binding = AppPromptBinding(
                    id=binding.id,
                    mode=binding.mode,
                    app_bundle_id=binding.app_bundle_id,
                    app_name=binding.app_name,
                    prompt_id=None,
                    created_at=binding.created_at,
                    updated_at=binding.updated_at,
                )
            reconciled.append(binding)
        if changed:
            self.app_prompt_bindings = reconciled

    # =====================================================================
    # Backup
    # =====================================================================

    def make_backup_payload(self) -> SettingsBackupPayload:
        return SettingsBackupPayload(
            {
                "selectedProviderID": self.selected_provider_id,
                "selectedModelByProvider": self.selected_model_by_provider,
                "savedProviders": [provider.to_dict() for provider in self.saved_providers],
                "modelReasoningConfigs": {
                    key: value.to_dict() for key, value in self.model_reasoning_configs.items()
                },
                "privateAIPrefixKVCacheEnabled": self.private_ai_prefix_kv_cache_enabled,
                "privateAIBoostEnabled": self.private_ai_boost_enabled,
                "privateAIBackendPreference": self.private_ai_backend_preference.value,
                "privateAIContextTokenLimit": self.private_ai_context_token_limit,
                "selectedSpeechModel": self.selected_speech_model.value,
                "selectedWhisperLanguageCode": self.whisper_language_backup_value(
                    self.selected_whisper_language_code
                ),
                "selectedCohereLanguage": self.selected_cohere_language.value,
                "selectedNemotronLanguage": self.selected_nemotron_language,
                "hotkeyShortcut": self.hotkey_shortcut.to_dict(),
                "primaryDictationShortcuts": [s.to_dict() for s in self.primary_dictation_shortcuts],
                "promptModeHotkeyShortcut": self.prompt_mode_hotkey_shortcut.to_dict(),
                "promptModeShortcutEnabled": self.prompt_mode_shortcut_enabled,
                "promptModeSelectedPromptID": self.prompt_mode_selected_prompt_id,
                "secondaryDictationPromptOff": self.is_secondary_dictation_prompt_off,
                "commandModeHotkeyShortcut": (
                    self.command_mode_hotkey_shortcut.to_dict()
                    if self.command_mode_hotkey_shortcut
                    else None
                ),
                "commandModeShortcutEnabled": self.command_mode_shortcut_enabled,
                "commandModeSelectedModel": self.command_mode_selected_model,
                "commandModeSelectedProviderID": self.command_mode_selected_provider_id,
                "commandModeConfirmBeforeExecute": self.command_mode_confirm_before_execute,
                "commandModeLinkedToGlobal": self.command_mode_linked_to_global,
                "rewriteModeHotkeyShortcut": self.rewrite_mode_hotkey_shortcut.to_dict(),
                "rewriteModeShortcutEnabled": self.rewrite_mode_shortcut_enabled,
                "rewriteModeSelectedModel": self.rewrite_mode_selected_model,
                "rewriteModeSelectedProviderID": self.rewrite_mode_selected_provider_id,
                "rewriteModeLinkedToGlobal": self.rewrite_mode_linked_to_global,
                "cancelRecordingHotkeyShortcut": self.cancel_recording_hotkey_shortcut.to_dict(),
                "pasteLastTranscriptionHotkeyShortcut": (
                    self.paste_last_transcription_hotkey_shortcut.to_dict()
                    if self.paste_last_transcription_hotkey_shortcut
                    else None
                ),
                "pasteLastTranscriptionShortcutEnabled": self.paste_last_transcription_shortcut_enabled,
                "showThinkingTokens": self.show_thinking_tokens,
                "hideFromDockAndAppSwitcher": self.hide_from_dock_and_app_switcher,
                "showMainWindowAtLoginLaunch": self.show_main_window_at_login_launch,
                "accentColorOption": self.accent_color_option.value,
                "transcriptionStartSound": self.transcription_start_sound.value,
                "transcriptionSoundVolume": self.transcription_sound_volume,
                "transcriptionSoundIndependentVolume": self.transcription_sound_independent_volume,
                "autoUpdateCheckEnabled": self.auto_update_check_enabled,
                "betaReleasesEnabled": self.beta_releases_enabled,
                "enableDebugLogs": self.enable_debug_logs,
                "shareAnonymousAnalytics": self.share_detailed_analytics,
                "pressAndHoldMode": self.press_and_hold_mode,
                "hotkeyMode": self.hotkey_mode,
                "enableStreamingPreview": self.enable_streaming_preview,
                "experimentalParakeetUnifiedFinalEnabled": self.experimental_parakeet_unified_final_enabled,
                "showHistoryPerformanceMetrics": self.show_history_performance_metrics,
                "skipSilentRecordingsEnabled": self.skip_silent_recordings_enabled,
                "enableAIStreaming": self.enable_ai_streaming,
                "copyTranscriptionToClipboard": self.copy_transcription_to_clipboard,
                "textInsertionMode": self.text_insertion_mode.value,
                "spokenSendEnabled": self.spoken_send_enabled,
                "spokenSendImmediatelyEnabled": self.spoken_send_immediately_enabled,
                "spokenSendPhrase": self.spoken_send_phrase,
                "spokenSendKey": self.spoken_send_key.value,
                "preferredInputDeviceUID": self.preferred_input_device_uid,
                "microphonePriority": [entry.to_dict() for entry in self.microphone_priority],
                "suppressedMicrophoneUIDs": sorted(self.suppressed_microphone_uids),
                "preferredOutputDeviceUID": self.preferred_output_device_uid,
                # Kept in the schema for compatibility with older builds; current
                # builds always resolve microphones from the priority list.
                "microphoneSelectionMode": MicrophoneSelectionMode.MANUAL.value,
                "visualizerNoiseThreshold": self.visualizer_noise_threshold,
                "overlayPosition": self.overlay_position.value,
                "overlayBottomOffset": self.overlay_bottom_offset,
                "overlaySize": self.overlay_size.value,
                "transcriptionPreviewCharLimit": self.transcription_preview_char_limit,
                "userTypingWPM": self.user_typing_wpm,
                "saveTranscriptionHistory": self.save_transcription_history,
                "saveAudioWithTranscriptionHistory": self.save_audio_with_transcription_history,
                "historyAutoClearInterval": self.history_auto_clear_interval.value,
                "audioHistoryBudgetGB": self.audio_history_budget_gb,
                "notifyAIProcessingFailures": self.notify_ai_processing_failures,
                "showMicrophoneChangeAlerts": self.show_microphone_change_alerts,
                "weekendsDontBreakStreak": self.weekends_dont_break_streak,
                "fillerWords": self.filler_words,
                "removeFillerWordsEnabled": self.remove_filler_words_enabled,
                "autoConvertPunctuationEnabled": self.auto_convert_punctuation_enabled,
                "literalDictationFormattingEnabled": self.literal_dictation_formatting_enabled,
                "punctuationDictionaryPrefix": self.punctuation_dictionary_prefix,
                "punctuationDictionaryRules": [
                    rule.to_dict() for rule in self.punctuation_dictionary_rules
                ],
                "spokenFormattingActionRules": [
                    rule.to_dict() for rule in self.spoken_formatting_action_rules
                ],
                "gaavModeEnabled": self.gaav_mode_enabled,
                "gaavLowercaseFirstLetterEnabled": self.gaav_lowercase_first_letter_enabled,
                "gaavRemoveTrailingPeriodEnabled": self.gaav_remove_trailing_period_enabled,
                "continuousDictationModeEnabled": self.continuous_dictation_mode_enabled,
                "continuousDictationSpacingEnabled": self.continuous_dictation_spacing_enabled,
                "contextAwareCapitalizationEnabled": self.context_aware_capitalization_enabled,
                "pauseMediaDuringTranscription": self.pause_media_during_transcription,
                "automaticDictionaryLearningEnabled": self.automatic_dictionary_learning_enabled,
                "automaticDictionarySuggestionFrequency": self.automatic_dictionary_suggestion_frequency.value,
                "pronunciationMatchingEnabled": self.pronunciation_matching_enabled,
                "vocabularyBoostingEnabled": self.vocabulary_boosting_enabled,
                "customDictionaryEntries": [entry.to_dict() for entry in self.custom_dictionary_entries],
                "selectedDictationPromptID": self.selected_dictation_prompt_id,
                "dictationPromptOff": self.is_dictation_prompt_off,
                "dictationPromptRoutingScope": self.dictation_prompt_routing_scope.value,
                "editPromptOff": self.is_edit_prompt_off,
                "selectedEditPromptID": self.selected_edit_prompt_id,
                "editPromptRoutingScope": self.edit_prompt_routing_scope.value,
                "defaultDictationPromptOverride": self.default_dictation_prompt_override,
                "defaultEditPromptOverride": self.default_edit_prompt_override,
                "fileTranscriptionSpeakerLabelsEnabled": self.file_transcription_speaker_labels_enabled,
                "fileTranscriptionExpectedSpeakerCount": self.file_transcription_expected_speaker_count,
            }
        )

    def restore(
        self,
        payload: SettingsBackupPayload,
        prompt_profiles: list[DictationPromptProfile] | None = None,
        app_prompt_bindings: list[AppPromptBinding] | None = None,
    ) -> None:
        values = payload.values
        if prompt_profiles is None:
            prompt_profiles = self.dictation_prompt_profiles
        if app_prompt_bindings is None:
            app_prompt_bindings = self.app_prompt_bindings

        def present(key: str) -> bool:
            return key in values and values[key] is not None

        self.saved_providers = [SavedProvider.from_dict(item) for item in values.get("savedProviders") or []]
        self.selected_provider_id = values.get("selectedProviderID") or ""
        self.selected_model_by_provider = dict(values.get("selectedModelByProvider") or {})
        self.model_reasoning_configs = {
            key: ModelReasoningConfig.from_dict(value)
            for key, value in (values.get("modelReasoningConfigs") or {}).items()
        }
        if present("privateAIPrefixKVCacheEnabled"):
            self.private_ai_prefix_kv_cache_enabled = bool(values["privateAIPrefixKVCacheEnabled"])
        if present("privateAIBoostEnabled"):
            self.private_ai_boost_enabled = bool(values["privateAIBoostEnabled"])
        if present("privateAIBackendPreference"):
            self.private_ai_backend_preference = PrivateAIBackendPreference.from_raw(
                values["privateAIBackendPreference"], PrivateAIBackendPreference.AUTO
            )
        if present("privateAIContextTokenLimit"):
            self.private_ai_context_token_limit = int(values["privateAIContextTokenLimit"])

        self.selected_speech_model = SpeechModel.supported_or_default(
            SpeechModel.from_raw(values.get("selectedSpeechModel"), None)
        )
        if present("selectedWhisperLanguageCode"):
            self.selected_whisper_language_code = self.whisper_language_code_from_backup_value(
                values["selectedWhisperLanguageCode"]
            )
        if present("selectedCohereLanguage"):
            self.selected_cohere_language = CohereLanguage.from_raw(
                values["selectedCohereLanguage"], CohereLanguage.ENGLISH
            )
        if present("selectedNemotronLanguage"):
            self.selected_nemotron_language = values["selectedNemotronLanguage"]

        primary = values.get("primaryDictationShortcuts")
        if primary is None:
            legacy = values.get("hotkeyShortcut")
            primary = [legacy] if legacy else []
        self.primary_dictation_shortcuts = [HotkeyShortcut.from_dict(item) for item in primary]

        if present("promptModeHotkeyShortcut"):
            self.prompt_mode_hotkey_shortcut = HotkeyShortcut.from_dict(values["promptModeHotkeyShortcut"])
        self.prompt_mode_shortcut_enabled = bool(values.get("promptModeShortcutEnabled") or False)
        self.command_mode_hotkey_shortcut = (
            HotkeyShortcut.from_dict(values["commandModeHotkeyShortcut"])
            if present("commandModeHotkeyShortcut")
            else None
        )
        self.command_mode_shortcut_enabled = bool(values.get("commandModeShortcutEnabled") or False)
        self.command_mode_selected_model = values.get("commandModeSelectedModel")
        self.command_mode_selected_provider_id = values.get("commandModeSelectedProviderID") or ""
        self.command_mode_confirm_before_execute = bool(
            values.get("commandModeConfirmBeforeExecute", True)
        )
        self.command_mode_linked_to_global = bool(values.get("commandModeLinkedToGlobal", True))
        if present("rewriteModeHotkeyShortcut"):
            self.rewrite_mode_hotkey_shortcut = HotkeyShortcut.from_dict(
                values["rewriteModeHotkeyShortcut"]
            )
        self.rewrite_mode_shortcut_enabled = bool(values.get("rewriteModeShortcutEnabled") or False)
        self.rewrite_mode_selected_model = values.get("rewriteModeSelectedModel")
        self.rewrite_mode_selected_provider_id = values.get("rewriteModeSelectedProviderID") or ""
        self.rewrite_mode_linked_to_global = bool(values.get("rewriteModeLinkedToGlobal", True))
        if present("cancelRecordingHotkeyShortcut"):
            self.cancel_recording_hotkey_shortcut = HotkeyShortcut.from_dict(
                values["cancelRecordingHotkeyShortcut"]
            )

        # Both guarded so restoring an older backup doesn't wipe a configured
        # shortcut or leave the feature enabled with nothing bound.
        if present("pasteLastTranscriptionHotkeyShortcut"):
            self.paste_last_transcription_hotkey_shortcut = HotkeyShortcut.from_dict(
                values["pasteLastTranscriptionHotkeyShortcut"]
            )
        if present("pasteLastTranscriptionShortcutEnabled"):
            self.paste_last_transcription_shortcut_enabled = bool(
                values["pasteLastTranscriptionShortcutEnabled"]
            )

        self.show_thinking_tokens = bool(values.get("showThinkingTokens") or False)
        self.hide_from_dock_and_app_switcher = bool(values.get("hideFromDockAndAppSwitcher") or False)
        self.show_main_window_at_login_launch = (
            bool(values["showMainWindowAtLoginLaunch"])
            if present("showMainWindowAtLoginLaunch")
            else True
        )
        self.accent_color_option = AccentColorOption.from_raw(
            values.get("accentColorOption"), AccentColorOption.CYAN
        )
        self.transcription_start_sound = TranscriptionStartSound.from_raw(
            values.get("transcriptionStartSound"), TranscriptionStartSound.FLUID_SFX_0
        )
        self.transcription_sound_volume = float(values.get("transcriptionSoundVolume") or 0.0)
        self.transcription_sound_independent_volume = bool(
            values.get("transcriptionSoundIndependentVolume") or False
        )
        self.auto_update_check_enabled = bool(values.get("autoUpdateCheckEnabled", True))
        self.beta_releases_enabled = bool(values.get("betaReleasesEnabled") or False)
        self.enable_debug_logs = bool(values.get("enableDebugLogs") or False)
        self.share_detailed_analytics = bool(values.get("shareAnonymousAnalytics", True))
        self.hotkey_mode = values.get("hotkeyMode") or (
            HotkeyActivationMode.HOLD if values.get("pressAndHoldMode") else HotkeyActivationMode.TOGGLE
        )
        self.enable_streaming_preview = bool(values.get("enableStreamingPreview", True))
        if present("experimentalParakeetUnifiedFinalEnabled"):
            self.experimental_parakeet_unified_final_enabled = bool(
                values["experimentalParakeetUnifiedFinalEnabled"]
            )
        if present("showHistoryPerformanceMetrics"):
            self.show_history_performance_metrics = bool(values["showHistoryPerformanceMetrics"])
        if present("skipSilentRecordingsEnabled"):
            self.skip_silent_recordings_enabled = bool(values["skipSilentRecordingsEnabled"])
        self.enable_ai_streaming = bool(values.get("enableAIStreaming", True))
        self.copy_transcription_to_clipboard = bool(values.get("copyTranscriptionToClipboard") or False)
        self.text_insertion_mode = TextInsertionMode.from_raw(
            values.get("textInsertionMode"), TextInsertionMode.STANDARD
        )
        if present("spokenSendEnabled"):
            self.spoken_send_enabled = bool(values["spokenSendEnabled"])
        if present("spokenSendImmediatelyEnabled"):
            self.spoken_send_immediately_enabled = bool(values["spokenSendImmediatelyEnabled"])
        if present("spokenSendPhrase"):
            self.spoken_send_phrase = values["spokenSendPhrase"]
        if present("spokenSendKey"):
            self.spoken_send_key = SpokenSendKey.from_raw(values["spokenSendKey"], SpokenSendKey.ENTER)

        self.preferred_input_device_uid = values.get("preferredInputDeviceUID")
        self.suppressed_microphone_uids = set(values.get("suppressedMicrophoneUIDs") or [])
        microphone_priority = values.get("microphonePriority")
        if microphone_priority is not None:
            self.microphone_priority = [
                MicrophonePriorityEntry.from_dict(item) for item in microphone_priority
            ]
        else:
            self.microphone_priority = []
        self.preferred_output_device_uid = values.get("preferredOutputDeviceUID")
        if microphone_priority is not None:
            self.microphone_selection_mode = MicrophoneSelectionMode.MANUAL
            self.microphone_selection_migration_version = MICROPHONE_PRIORITY_MIGRATION_VERSION
        elif values.get("microphoneSelectionMode") == MicrophoneSelectionMode.SYSTEM.value:
            self.microphone_selection_mode = MicrophoneSelectionMode.SYSTEM
            self.microphone_selection_migration_version = 0
        else:
            self.microphone_selection_mode = MicrophoneSelectionMode.MANUAL

        self.visualizer_noise_threshold = float(values.get("visualizerNoiseThreshold") or 0.0)
        self.overlay_position = OverlayPosition.from_raw(
            values.get("overlayPosition"), OverlayPosition.BOTTOM
        )
        self.overlay_bottom_offset = float(values.get("overlayBottomOffset") or 50.0)
        self.overlay_size = OverlaySize.from_raw(values.get("overlaySize"), OverlaySize.MEDIUM)
        self.transcription_preview_char_limit = int(
            values.get("transcriptionPreviewCharLimit") or DEFAULT_TRANSCRIPTION_PREVIEW_CHAR_LIMIT
        )
        self.user_typing_wpm = int(values.get("userTypingWPM") or 0)
        self.save_transcription_history = bool(values.get("saveTranscriptionHistory", True))
        if present("saveAudioWithTranscriptionHistory"):
            self.save_audio_with_transcription_history = bool(values["saveAudioWithTranscriptionHistory"])
        if present("audioHistoryBudgetGB"):
            self.audio_history_budget_gb = float(values["audioHistoryBudgetGB"])
        if present("historyAutoClearInterval"):
            self.history_auto_clear_interval = HistoryAutoClearInterval.from_raw(
                values["historyAutoClearInterval"], HistoryAutoClearInterval.NEVER
            )
        if present("notifyAIProcessingFailures"):
            self.notify_ai_processing_failures = bool(values["notifyAIProcessingFailures"])
        if present("showMicrophoneChangeAlerts"):
            self.show_microphone_change_alerts = bool(values["showMicrophoneChangeAlerts"])
        self.weekends_dont_break_streak = bool(values.get("weekendsDontBreakStreak") or False)
        self.filler_words = list(values.get("fillerWords") or [])
        self.remove_filler_words_enabled = bool(values.get("removeFillerWordsEnabled") or False)
        if present("autoConvertPunctuationEnabled"):
            self.auto_convert_punctuation_enabled = bool(values["autoConvertPunctuationEnabled"])
        if present("literalDictationFormattingEnabled"):
            self.literal_dictation_formatting_enabled = bool(values["literalDictationFormattingEnabled"])
        if present("punctuationDictionaryPrefix"):
            self.punctuation_dictionary_prefix = values["punctuationDictionaryPrefix"]
        if present("punctuationDictionaryRules"):
            self.punctuation_dictionary_rules = [
                PunctuationDictionaryRule.from_dict(item) for item in values["punctuationDictionaryRules"]
            ]
        if present("spokenFormattingActionRules"):
            self.spoken_formatting_action_rules = [
                SpokenFormattingActionRule.from_dict(item)
                for item in values["spokenFormattingActionRules"]
            ]

        restored_gaav = bool(values.get("gaavModeEnabled") or False)
        restored_continuous = bool(values.get("continuousDictationModeEnabled") or False)
        self.gaav_mode_enabled = restored_gaav
        self.gaav_lowercase_first_letter_enabled = (
            bool(values["gaavLowercaseFirstLetterEnabled"])
            if present("gaavLowercaseFirstLetterEnabled")
            else restored_gaav
        )
        self.gaav_remove_trailing_period_enabled = (
            bool(values["gaavRemoveTrailingPeriodEnabled"])
            if present("gaavRemoveTrailingPeriodEnabled")
            else restored_gaav
        )
        self.continuous_dictation_mode_enabled = restored_continuous
        self.continuous_dictation_spacing_enabled = (
            bool(values["continuousDictationSpacingEnabled"])
            if present("continuousDictationSpacingEnabled")
            else restored_continuous
        )
        self.context_aware_capitalization_enabled = (
            bool(values["contextAwareCapitalizationEnabled"])
            if present("contextAwareCapitalizationEnabled")
            else restored_continuous
        )
        self.pause_media_during_transcription = bool(values.get("pauseMediaDuringTranscription") or False)
        if present("automaticDictionaryLearningEnabled"):
            self.automatic_dictionary_learning_enabled = bool(values["automaticDictionaryLearningEnabled"])
        if present("automaticDictionarySuggestionFrequency"):
            self.automatic_dictionary_suggestion_frequency = (
                AutomaticDictionarySuggestionFrequency.from_raw(
                    values["automaticDictionarySuggestionFrequency"]
                )
            )
        if present("pronunciationMatchingEnabled"):
            self.pronunciation_matching_enabled = bool(values["pronunciationMatchingEnabled"])
        self.vocabulary_boosting_enabled = bool(values.get("vocabularyBoostingEnabled") or False)
        self.custom_dictionary_entries = [
            CustomDictionaryEntry.from_dict(item) for item in values.get("customDictionaryEntries") or []
        ]

        self.dictation_prompt_profiles = prompt_profiles
        self.app_prompt_bindings = app_prompt_bindings
        self.selected_dictation_prompt_id = values.get("selectedDictationPromptID")
        self.is_dictation_prompt_off = (
            bool(values["dictationPromptOff"])
            if present("dictationPromptOff")
            else self.is_dictation_prompt_off
        )
        self.dictation_prompt_routing_scope = PromptRoutingScope.from_raw(
            values.get("dictationPromptRoutingScope"), PromptRoutingScope.ALL_APPS
        )
        self.is_edit_prompt_off = (
            bool(values["editPromptOff"]) if present("editPromptOff") else self.is_edit_prompt_off
        )
        self.edit_prompt_routing_scope = PromptRoutingScope.from_raw(
            values.get("editPromptRoutingScope"), PromptRoutingScope.ALL_APPS
        )
        self.selected_edit_prompt_id = values.get("selectedEditPromptID")
        self.default_dictation_prompt_override = values.get("defaultDictationPromptOverride")
        self.default_edit_prompt_override = values.get("defaultEditPromptOverride")
        if present("fileTranscriptionSpeakerLabelsEnabled"):
            self.file_transcription_speaker_labels_enabled = bool(
                values["fileTranscriptionSpeakerLabelsEnabled"]
            )
        if present("fileTranscriptionExpectedSpeakerCount"):
            self.file_transcription_expected_speaker_count = int(
                values["fileTranscriptionExpectedSpeakerCount"]
            )
        self.prompt_mode_selected_prompt_id = values.get("promptModeSelectedPromptID")
        self.is_secondary_dictation_prompt_off = bool(values.get("secondaryDictationPromptOff") or False)
        self.normalize_prompt_selections_if_needed()


#: Providers built into the app; a stored selection naming one always resolves.
BUILT_IN_PROVIDER_IDS = frozenset(
    {
        "openai",
        "groq",
        "anthropic",
        "gemini",
        "openrouter",
        "cerebras",
        "xai",
        "ollama",
        "lmstudio",
        "cohere",
        "nvidia",
        "compatible",
    }
)
