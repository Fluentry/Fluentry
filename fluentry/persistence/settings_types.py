"""Value types stored by `SettingsStore`.

Raw values are preserved exactly from the macOS build so a backup exported
there restores here. Where a case only made sense on macOS (Apple Speech, the
MacBook notch) the case is kept for decoding and marked unsupported, rather
than dropped, so restoring an old backup never fails.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable

from ..models.hotkey import HotkeyShortcut


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return fallback or now()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return fallback or now()


class StrEnum(str, Enum):
    @classmethod
    def from_raw(cls, raw: Any, fallback=None):
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            for case in cls:
                if case.value == raw:
                    return case
        return fallback

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.value


# --- prompts ----------------------------------------------------------------


class PromptMode(StrEnum):
    DICTATE = "dictate"
    EDIT = "edit"
    WRITE = "write"  # legacy persisted value, decoded as EDIT
    REWRITE = "rewrite"  # legacy persisted value, decoded as EDIT

    @property
    def normalized(self) -> "PromptMode":
        if self in (PromptMode.WRITE, PromptMode.REWRITE):
            return PromptMode.EDIT
        return self

    @staticmethod
    def visible_prompt_modes() -> list["PromptMode"]:
        return [PromptMode.DICTATE, PromptMode.EDIT]

    @property
    def display_name(self) -> str:
        return "Dictate" if self.normalized is PromptMode.DICTATE else "Edit"


class DictationShortcutSlot(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"

    @property
    def display_name(self) -> str:
        return "Primary Dictation Shortcut" if self is DictationShortcutSlot.PRIMARY else "Secondary Dictation Shortcut"


class PromptRoutingScope(StrEnum):
    """Whether a prompt applies everywhere or only to bound apps.

    The raw values are the macOS ones, so a backup moves between platforms.
    """

    ALL_APPS = "allApps"
    SELECTED_APPS_ONLY = "selectedAppsOnly"


DEFAULT_PROMPT_CONFIGURATION_KEY = "__default__"
PRIVATE_AI_PROMPT_CONFIGURATION_KEY = "__privateAI__"


@dataclass(frozen=True)
class DictationPromptSelection:
    """`off` / `default` / `privateAI` / `profile(id)`."""

    kind: str
    profile_id: str | None = None

    @staticmethod
    def profile(profile_id: str) -> "DictationPromptSelection":
        return DictationPromptSelection("profile", profile_id)


DictationPromptSelection.OFF = DictationPromptSelection("off")
DictationPromptSelection.DEFAULT = DictationPromptSelection("default")
DictationPromptSelection.PRIVATE_AI = DictationPromptSelection("privateAI")


@dataclass
class DictationPromptProfile:
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    name: str = ""
    prompt: str = ""
    mode: PromptMode = PromptMode.DICTATE
    include_context: bool = False
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)

    def __post_init__(self) -> None:
        self.mode = self.mode.normalized

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "mode": self.mode.value,
            "includeContext": self.include_context,
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "DictationPromptProfile":
        return DictationPromptProfile(
            id=str(payload["id"]),
            name=str(payload["name"]),
            prompt=str(payload["prompt"]),
            mode=(PromptMode.from_raw(payload.get("mode"), PromptMode.DICTATE)).normalized,
            include_context=bool(payload.get("includeContext") or False),
            created_at=parse_iso(payload.get("createdAt")),
            updated_at=parse_iso(payload.get("updatedAt")),
        )


class AppAIEnhancement(StrEnum):
    """Whether AI cleanup runs in one particular app.

    Dictating into a chat client and into a coding agent want opposite
    answers, and changing the global switch each time is the thing this
    exists to avoid.
    """

    INHERIT = "inherit"
    ON = "on"
    OFF = "off"

    @property
    def display_name(self) -> str:
        return {
            AppAIEnhancement.INHERIT: "Use the global setting",
            AppAIEnhancement.ON: "Always on",
            AppAIEnhancement.OFF: "Always off",
        }[self]

    def resolve(self, global_enabled: bool) -> bool:
        if self is AppAIEnhancement.ON:
            return True
        if self is AppAIEnhancement.OFF:
            return False
        return global_enabled


@dataclass
class AppPromptBinding:
    """Binds a prompt to an application.

    macOS identified apps by bundle id; on Linux the stable identifier is the
    desktop-entry / WM_CLASS app id, stored in the same lowercase-normalised
    field so existing bindings keep working.
    """

    mode: PromptMode
    app_bundle_id: str
    app_name: str
    prompt_id: str | None
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    ai_enhancement: AppAIEnhancement = AppAIEnhancement.INHERIT
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)

    def __post_init__(self) -> None:
        self.mode = self.mode.normalized
        self.app_bundle_id = normalize_app_id(self.app_bundle_id)
        trimmed_name = (self.app_name or "").strip()
        self.app_name = trimmed_name or self.app_bundle_id
        trimmed_prompt = (self.prompt_id or "").strip()
        self.prompt_id = trimmed_prompt or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode.value,
            "appBundleID": self.app_bundle_id,
            "appName": self.app_name,
            "promptID": self.prompt_id,
            "aiEnhancement": self.ai_enhancement.value,
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "AppPromptBinding":
        return AppPromptBinding(
            id=str(payload["id"]),
            mode=PromptMode.from_raw(payload.get("mode"), PromptMode.DICTATE),
            app_bundle_id=str(payload.get("appBundleID") or ""),
            app_name=str(payload.get("appName") or ""),
            prompt_id=payload.get("promptID"),
            # Absent in bindings written before this existed, which is
            # exactly what "inherit" means.
            ai_enhancement=AppAIEnhancement.from_raw(
                payload.get("aiEnhancement"), AppAIEnhancement.INHERIT
            ),
            created_at=parse_iso(payload.get("createdAt")),
            updated_at=parse_iso(payload.get("updatedAt")),
        )


def normalize_app_id(value: str) -> str:
    return (value or "").strip().lower()


@dataclass
class DictationPromptConfiguration:
    shortcut: HotkeyShortcut | None = None
    provider_id: str = ""
    model_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"providerID": self.provider_id, "modelName": self.model_name}
        if self.shortcut is not None:
            payload["shortcut"] = self.shortcut.to_dict()
        return payload

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "DictationPromptConfiguration":
        raw_shortcut = payload.get("shortcut")
        return DictationPromptConfiguration(
            shortcut=HotkeyShortcut.from_dict(raw_shortcut) if isinstance(raw_shortcut, dict) else None,
            provider_id=str(payload.get("providerID") or ""),
            model_name=str(payload.get("modelName") or ""),
        )


class PromptResolutionSource(StrEnum):
    APP_BINDING_PROFILE = "appBindingProfile"
    APP_BINDING_DEFAULT = "appBindingDefault"
    SELECTED_PROFILE = "selectedProfile"
    DEFAULT_OVERRIDE = "defaultOverride"
    BUILT_IN_DEFAULT = "builtInDefault"


@dataclass
class PromptResolution:
    source: PromptResolutionSource
    profile: DictationPromptProfile | None
    app_binding: AppPromptBinding | None
    prompt_body: str
    system_prompt: str


# --- audio devices ----------------------------------------------------------


class MicrophoneSelectionMode(StrEnum):
    SYSTEM = "system"
    MANUAL = "manual"

    @property
    def display_name(self) -> str:
        return "Use System Default" if self is MicrophoneSelectionMode.SYSTEM else "Use Preferred Microphone"


@dataclass(frozen=True)
class MicrophonePriorityEntry:
    uid: str
    name: str

    @property
    def id(self) -> str:
        return self.uid

    def to_dict(self) -> dict[str, Any]:
        return {"uid": self.uid, "name": self.name}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "MicrophonePriorityEntry":
        return MicrophonePriorityEntry(uid=str(payload["uid"]), name=str(payload.get("name") or ""))


# --- providers --------------------------------------------------------------


@dataclass
class SavedProvider:
    """A user-added AI endpoint.

    `api_key` is a legacy field: keys live in the secret store now, and this
    stays only so a backup written by an older build still decodes.
    """

    id: str
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "baseURL": self.base_url,
            "apiKey": self.api_key,
            "models": list(self.models),
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SavedProvider":
        raw_models = payload.get("models")
        return SavedProvider(
            id=str(payload["id"]),
            name=str(payload.get("name") or ""),
            base_url=str(payload.get("baseURL") or ""),
            api_key=str(payload.get("apiKey") or ""),
            models=[str(model) for model in raw_models] if isinstance(raw_models, list) else [],
        )


@dataclass
class ModelReasoningConfig:
    parameter_name: str = ""
    parameter_value: str = ""
    is_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameterName": self.parameter_name,
            "parameterValue": self.parameter_value,
            "isEnabled": self.is_enabled,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "ModelReasoningConfig":
        return ModelReasoningConfig(
            parameter_name=str(payload.get("parameterName") or ""),
            parameter_value=str(payload.get("parameterValue") or ""),
            is_enabled=bool(payload.get("isEnabled") or False),
        )


class PrivateAIBackendPreference(StrEnum):
    AUTO = "auto"
    LLAMA = "llama"
    ONNX = "onnx"

    @staticmethod
    def system_default() -> "PrivateAIBackendPreference":
        """llama.cpp is the portable CPU/GPU path on Linux."""
        return PrivateAIBackendPreference.LLAMA

    @property
    def display_name(self) -> str:
        if self is PrivateAIBackendPreference.AUTO:
            return PrivateAIBackendPreference.system_default().display_name
        if self is PrivateAIBackendPreference.LLAMA:
            return "llama.cpp (Recommended)"
        return "ONNX Runtime (Experimental)"


# --- dictionary -------------------------------------------------------------


@dataclass
class CustomDictionaryEntry:
    triggers: list[str]
    replacement: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    def __post_init__(self) -> None:
        self.triggers = [trigger.strip(" \t").lower() for trigger in self.triggers]

    @staticmethod
    def sanitized_replacement(text: str) -> str:
        """Trim padding but preserve an intentional all-whitespace payload."""
        trimmed = text.strip()
        return text if trimmed == "" else trimmed

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "triggers": list(self.triggers), "replacement": self.replacement}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "CustomDictionaryEntry":
        return CustomDictionaryEntry(
            id=str(payload.get("id") or str(uuid.uuid4()).upper()),
            triggers=[str(trigger) for trigger in (payload.get("triggers") or [])],
            replacement=str(payload.get("replacement") or ""),
        )


class AutomaticDictionarySuggestionFrequency(Enum):
    FIRST = 1
    SECOND = 2
    THIRD = 3

    @staticmethod
    def from_raw(raw: Any, fallback: "AutomaticDictionarySuggestionFrequency" = None):
        fallback = fallback or AutomaticDictionarySuggestionFrequency.FIRST
        try:
            return AutomaticDictionarySuggestionFrequency(int(raw))
        except (TypeError, ValueError):
            return fallback

    @property
    def display_name(self) -> str:
        return f"{self.value} correction" + ("s" if self.value != 1 else "")


def normalized_alias(value: str) -> str | None:
    alias = (value or "").strip().lower()
    return alias or None


def normalized_aliases(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        alias = normalized_alias(value)
        if alias is None or alias in seen:
            continue
        seen.add(alias)
        aliases.append(alias)
    return aliases


def normalized_symbol(value: str) -> str | None:
    symbol = (value or "").strip()
    return symbol or None


@dataclass
class PunctuationDictionaryRule:
    aliases: list[str]
    symbol: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    def __post_init__(self) -> None:
        self.aliases = normalized_aliases(self.aliases)
        self.symbol = normalized_symbol(self.symbol) or self.symbol

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "aliases": list(self.aliases), "symbol": self.symbol}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "PunctuationDictionaryRule":
        return PunctuationDictionaryRule(
            id=str(payload.get("id") or str(uuid.uuid4()).upper()),
            aliases=[str(alias) for alias in (payload.get("aliases") or [])],
            symbol=str(payload.get("symbol") or ""),
        )


class SpokenFormattingAction(StrEnum):
    NEW_LINE = "newLine"
    NEW_PARAGRAPH = "newParagraph"
    TAB = "tab"
    SPACE = "space"

    @property
    def title(self) -> str:
        return {
            SpokenFormattingAction.NEW_LINE: "New Line",
            SpokenFormattingAction.NEW_PARAGRAPH: "New Paragraph",
            SpokenFormattingAction.TAB: "Tab",
            SpokenFormattingAction.SPACE: "Space",
        }[self]

    @property
    def display_symbol(self) -> str:
        return {
            SpokenFormattingAction.NEW_LINE: "⏎",
            SpokenFormattingAction.NEW_PARAGRAPH: "¶",
            SpokenFormattingAction.TAB: "⇥",
            SpokenFormattingAction.SPACE: "␣",
        }[self]

    @property
    def output(self) -> str:
        return {
            SpokenFormattingAction.NEW_LINE: "\n",
            SpokenFormattingAction.NEW_PARAGRAPH: "\n\n",
            SpokenFormattingAction.TAB: "\t",
            SpokenFormattingAction.SPACE: " ",
        }[self]


@dataclass
class SpokenFormattingActionRule:
    action: SpokenFormattingAction
    aliases: list[str]
    is_enabled: bool = True

    def __post_init__(self) -> None:
        self.aliases = normalized_aliases(self.aliases)
        self.is_enabled = self.is_enabled and bool(self.aliases)

    @property
    def id(self) -> SpokenFormattingAction:
        return self.action

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.value, "aliases": list(self.aliases), "isEnabled": self.is_enabled}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SpokenFormattingActionRule":
        return SpokenFormattingActionRule(
            action=SpokenFormattingAction.from_raw(payload.get("action"), SpokenFormattingAction.NEW_LINE),
            aliases=[str(alias) for alias in (payload.get("aliases") or [])],
            is_enabled=bool(payload.get("isEnabled", True)),
        )


DEFAULT_SPOKEN_FORMATTING_ACTION_RULES = [
    SpokenFormattingActionRule(SpokenFormattingAction.NEW_LINE, ["new line", "next line"]),
    SpokenFormattingActionRule(SpokenFormattingAction.NEW_PARAGRAPH, ["new paragraph", "next paragraph"]),
    SpokenFormattingActionRule(SpokenFormattingAction.TAB, ["tab"]),
    SpokenFormattingActionRule(SpokenFormattingAction.SPACE, ["space"]),
]

DEFAULT_PUNCTUATION_DICTIONARY_RULES = [
    PunctuationDictionaryRule(["comma"], ","),
    PunctuationDictionaryRule(["period", "full stop"], "."),
    PunctuationDictionaryRule(["dot"], "."),
    PunctuationDictionaryRule(["question mark", "questionmark"], "?"),
    PunctuationDictionaryRule(["exclamation mark", "exclamation point", "bang"], "!"),
    PunctuationDictionaryRule(["colon"], ":"),
    PunctuationDictionaryRule(["semicolon", "semi colon"], ";"),
    PunctuationDictionaryRule(["ellipsis", "dot dot dot", "three dots"], "..."),
    PunctuationDictionaryRule(["slash", "forward slash", "forwardslash"], "/"),
    PunctuationDictionaryRule(["backslash", "back slash"], "\\"),
    PunctuationDictionaryRule(["hyphen"], "-"),
    PunctuationDictionaryRule(["dash", "minus sign"], "-"),
    PunctuationDictionaryRule(["em dash", "long dash"], "—"),
    PunctuationDictionaryRule(["en dash"], "–"),
    PunctuationDictionaryRule(
        [
            "open parenthesis",
            "open parentheses",
            "left parenthesis",
            "left parentheses",
            "open paren",
            "left paren",
        ],
        "(",
    ),
    PunctuationDictionaryRule(
        [
            "close parenthesis",
            "close parentheses",
            "right parenthesis",
            "right parentheses",
            "close paren",
            "right paren",
        ],
        ")",
    ),
    PunctuationDictionaryRule(
        ["open bracket", "left bracket", "open square bracket", "left square bracket"], "["
    ),
    PunctuationDictionaryRule(
        ["close bracket", "right bracket", "close square bracket", "right square bracket"], "]"
    ),
    PunctuationDictionaryRule(
        [
            "open brace",
            "left brace",
            "open curly brace",
            "left curly brace",
            "open curly bracket",
            "left curly bracket",
        ],
        "{",
    ),
    PunctuationDictionaryRule(
        [
            "close brace",
            "right brace",
            "close curly brace",
            "right curly brace",
            "close curly bracket",
            "right curly bracket",
        ],
        "}",
    ),
    PunctuationDictionaryRule(["open angle bracket", "left angle bracket", "less than sign"], "<"),
    PunctuationDictionaryRule(["close angle bracket", "right angle bracket", "greater than sign"], ">"),
    PunctuationDictionaryRule(["quote", "quotes", "quotation mark", "double quote"], '"'),
    PunctuationDictionaryRule(["open quote", "opening quote", "open double quote", "opening double quote"], '"'),
    PunctuationDictionaryRule(
        ["close quote", "closing quote", "close double quote", "closing double quote"], '"'
    ),
    PunctuationDictionaryRule(["single quote"], "'"),
    PunctuationDictionaryRule(["apostrophe"], "'"),
    PunctuationDictionaryRule(["at the rate", "at sign", "commercial at"], "@"),
    PunctuationDictionaryRule(["ampersand", "and sign"], "&"),
    PunctuationDictionaryRule(["plus sign", "plus"], "+"),
    PunctuationDictionaryRule(["equals sign", "equal sign", "equal", "equals"], "="),
    PunctuationDictionaryRule(["percent sign", "percentage sign", "percent"], "%"),
    PunctuationDictionaryRule(["dollar sign", "dollar"], "$"),
    PunctuationDictionaryRule(["hash", "hash sign", "hashtag", "pound sign", "number sign"], "#"),
    PunctuationDictionaryRule(["asterisk", "star symbol"], "*"),
    PunctuationDictionaryRule(["underscore"], "_"),
    PunctuationDictionaryRule(["pipe", "vertical bar"], "|"),
    PunctuationDictionaryRule(["tilde"], "~"),
    PunctuationDictionaryRule(["caret"], "^"),
    PunctuationDictionaryRule(["backtick", "back tick"], "`"),
]

DEFAULT_PUNCTUATION_DICTIONARY_PREFIX = "literal"

DEFAULT_FILLER_WORDS = [
    "um", "umm", "uh", "uhh", "er", "err", "erm", "urm", "ah", "ahh",
    "eh", "ehh", "hm", "hmm", "mm", "mmm", "oops", "ugh", "actually",
    "please", "sorry", "wait",
]


# --- overlay / appearance ---------------------------------------------------


class OverlaySize(StrEnum):
    PILL = "pill"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

    @property
    def display_name(self) -> str:
        return self.value.capitalize()


class OverlayPosition(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"

    @property
    def display_name(self) -> str:
        return "Top of Screen" if self is OverlayPosition.TOP else "Bottom of Screen"


class NotchPresentationMode(StrEnum):
    """Retained for backup compatibility; Linux renders the compact top overlay."""

    STANDARD = "standard"
    MINIMAL = "minimal"

    @property
    def display_name(self) -> str:
        return "Standard" if self is NotchPresentationMode.STANDARD else "Compact"


class AccentColorOption(StrEnum):
    CYAN = "Cyan"
    GREEN = "Green"
    BLUE = "Blue"
    PURPLE = "Purple"
    ORANGE = "Orange"

    @property
    def hex(self) -> str:
        """Yaru's accent colours, so the app looks at home on Ubuntu.

        The stored raw values are unchanged, so a backup from any build
        still selects the same accent.
        """
        return {
            AccentColorOption.CYAN: "#0073E5",
            AccentColorOption.GREEN: "#0E8420",
            AccentColorOption.BLUE: "#0073E5",
            AccentColorOption.PURPLE: "#762572",
            AccentColorOption.ORANGE: "#E95420",
        }[self]


class ThemePreference(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def display_name(self) -> str:
        return self.value.capitalize()


class TranscriptionStartSound(StrEnum):
    NONE = "none"
    FLUID_SFX_0 = "fluid_sfx_0"
    FLUID_SFX_1 = "fluid_sfx_1"
    FLUID_SFX_2 = "fluid_sfx_2"
    FLUID_SFX_3 = "fluid_sfx_3"
    FLUID_SFX_4 = "fluid_sfx_4"

    @property
    def display_name(self) -> str:
        if self is TranscriptionStartSound.NONE:
            return "None"
        return "Fluid SFX " + self.value.rsplit("_", 1)[1]

    @property
    def start_sound_file_name(self) -> str | None:
        return {
            TranscriptionStartSound.NONE: None,
            TranscriptionStartSound.FLUID_SFX_0: "FV_start_0",
            TranscriptionStartSound.FLUID_SFX_1: "FV_start",
            TranscriptionStartSound.FLUID_SFX_2: "FV_start_2",
            TranscriptionStartSound.FLUID_SFX_3: "sfx_3",
            TranscriptionStartSound.FLUID_SFX_4: "sfx_4",
        }[self]

    @property
    def stop_sound_file_name(self) -> str | None:
        return "FV_end_0" if self is TranscriptionStartSound.FLUID_SFX_0 else None


# --- input / output ---------------------------------------------------------


class SpokenSendKey(StrEnum):
    ENTER = "enter"
    SHIFT_ENTER = "shiftEnter"
    COMMAND_ENTER = "commandEnter"

    @property
    def display_name(self) -> str:
        return {
            SpokenSendKey.ENTER: "Enter",
            SpokenSendKey.SHIFT_ENTER: "Shift + Enter",
            SpokenSendKey.COMMAND_ENTER: "Ctrl + Enter",
        }[self]

    @property
    def modifier_flags(self):
        from ..models.keycodes import ModifierFlags

        return {
            SpokenSendKey.ENTER: ModifierFlags.NONE,
            SpokenSendKey.SHIFT_ENTER: ModifierFlags.SHIFT,
            # macOS sent Command+Enter; Ctrl+Enter is the Linux equivalent chord.
            SpokenSendKey.COMMAND_ENTER: ModifierFlags.CONTROL,
        }[self]


class TextInsertionMode(StrEnum):
    STANDARD = "standard"
    RELIABLE_PASTE = "reliablePaste"
    #: Copy the transcript and stop; the user pastes it themselves. On
    #: Wayland this is the mode that always works, because it never needs
    #: to reach another window.
    CLIPBOARD_ONLY = "clipboardOnly"

    @property
    def display_name(self) -> str:
        return {
            TextInsertionMode.STANDARD: "Clipboard Free Insert",
            TextInsertionMode.RELIABLE_PASTE: "Clipboard Paste",
            TextInsertionMode.CLIPBOARD_ONLY: "Copy to Clipboard Only",
        }[self]

    @property
    def description(self) -> str:
        return {
            TextInsertionMode.STANDARD: (
                "Fastest path. Inserts text without changing the clipboard, "
                "with paste fallback if direct insertion is unavailable."
            ),
            TextInsertionMode.RELIABLE_PASTE: (
                "Compatibility path. Uses a temporary clipboard paste and restores "
                "your previous clipboard after insertion."
            ),
            TextInsertionMode.CLIPBOARD_ONLY: (
                "Copies the transcript and leaves it to you to paste. Works "
                "everywhere, including Wayland apps no typing tool can reach."
            ),
        }[self]

    @property
    def inserts_into_the_focused_app(self) -> bool:
        return self is not TextInsertionMode.CLIPBOARD_ONLY


class HistoryAutoClearInterval(StrEnum):
    """How long transcription history is kept before it clears itself.

    A dictation history is a log of everything the user has said, so being
    able to set it to expire is a privacy control, not housekeeping.
    """

    NEVER = "never"
    END_OF_DAY = "endOfDay"
    AFTER_WEEK = "afterWeek"
    AFTER_MONTH = "afterMonth"
    AFTER_QUARTER = "afterQuarter"

    @property
    def display_name(self) -> str:
        return {
            HistoryAutoClearInterval.NEVER: "Never",
            HistoryAutoClearInterval.END_OF_DAY: "End of Day",
            HistoryAutoClearInterval.AFTER_WEEK: "After 7 Days",
            HistoryAutoClearInterval.AFTER_MONTH: "After 30 Days",
            HistoryAutoClearInterval.AFTER_QUARTER: "After 90 Days",
        }[self]

    @property
    def description(self) -> str:
        return {
            HistoryAutoClearInterval.NEVER: (
                "Keep transcription history until you delete it manually."
            ),
            HistoryAutoClearInterval.END_OF_DAY: (
                "Clears previous days' entries at midnight, keeping only today's history."
            ),
            HistoryAutoClearInterval.AFTER_WEEK: "Deletes history entries older than 7 days.",
            HistoryAutoClearInterval.AFTER_MONTH: "Deletes history entries older than 30 days.",
            HistoryAutoClearInterval.AFTER_QUARTER: "Deletes history entries older than 90 days.",
        }[self]

    @property
    def retained_days(self) -> int | None:
        return {
            HistoryAutoClearInterval.NEVER: None,
            HistoryAutoClearInterval.END_OF_DAY: 0,
            HistoryAutoClearInterval.AFTER_WEEK: 7,
            HistoryAutoClearInterval.AFTER_MONTH: 30,
            HistoryAutoClearInterval.AFTER_QUARTER: 90,
        }[self]

    def cutoff(self, now: datetime | None = None) -> datetime | None:
        """Entries older than this go. None means keep everything.

        Counted from the start of the local day, so "7 days" means seven
        whole days rather than a rolling 168 hours.
        """
        days = self.retained_days
        if days is None:
            return None
        moment = (now or datetime.now().astimezone()).astimezone()
        start_of_day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_of_day - timedelta(days=days)


class WhisperModelSize(StrEnum):
    TINY = "ggml-tiny.bin"
    BASE = "ggml-base.bin"
    SMALL = "ggml-small.bin"
    MEDIUM = "ggml-medium.bin"
    LARGE = "ggml-large-v3.bin"

    @property
    def display_name(self) -> str:
        return {
            WhisperModelSize.TINY: "Tiny (~75 MB)",
            WhisperModelSize.BASE: "Base (~142 MB)",
            WhisperModelSize.SMALL: "Small (~466 MB)",
            WhisperModelSize.MEDIUM: "Medium (~1.5 GB)",
            WhisperModelSize.LARGE: "Large (~2.9 GB)",
        }[self]


class CohereLanguage(StrEnum):
    ENGLISH = "en"
    FRENCH = "fr"
    GERMAN = "de"
    ITALIAN = "it"
    SPANISH = "es"
    PORTUGUESE = "pt"
    GREEK = "el"
    DUTCH = "nl"
    POLISH = "pl"
    MANDARIN = "zh"
    JAPANESE = "ja"
    KOREAN = "ko"
    VIETNAMESE = "vi"
    ARABIC = "ar"

    @property
    def display_name(self) -> str:
        return {
            CohereLanguage.ENGLISH: "English",
            CohereLanguage.FRENCH: "French",
            CohereLanguage.GERMAN: "German",
            CohereLanguage.ITALIAN: "Italian",
            CohereLanguage.SPANISH: "Spanish",
            CohereLanguage.PORTUGUESE: "Portuguese",
            CohereLanguage.GREEK: "Greek",
            CohereLanguage.DUTCH: "Dutch",
            CohereLanguage.POLISH: "Polish",
            CohereLanguage.MANDARIN: "Mandarin",
            CohereLanguage.JAPANESE: "Japanese",
            CohereLanguage.KOREAN: "Korean",
            CohereLanguage.VIETNAMESE: "Vietnamese",
            CohereLanguage.ARABIC: "Arabic",
        }[self]


AUTOMATIC_WHISPER_LANGUAGE_CODE = "auto"
