"""The languages the Nemotron Speech models accept.

A port of `SettingsStore.NemotronLanguage`. It is deliberately *not* a closed
enum: the raw value is whatever was stored, so a newer build's language
survives a round trip through an older one. `supported_language` is the
validating constructor, and it applies the legacy mapping for the four codes
that were renamed when regional variants were added.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Order matters: it is the order the picker shows.
ALL_RAW_VALUES = [
    "auto", "es", "it", "pt", "hi", "ko", "en", "de-DE", "fr", "ru",
    "tr", "vi-VN", "nl", "ja-JP", "ar", "uk", "pl", "nb-NO", "fi",
    "zh-CN", "cs", "bg", "sk", "sv", "hr", "ro", "et", "da", "hu",
    "el-GR", "he-IL", "lt-LT", "sl-SI", "lv-LV", "mt-MT", "th-TH", "nn-NO",
]

#: Codes that shipped before the regional variants existed.
LEGACY_RAW_VALUES = {
    "el": "el-GR",
    "lt": "lt-LT",
    "lv": "lv-LV",
    "sl": "sl-SI",
}

DISPLAY_NAMES = {
    "auto": "Auto Detect",
    "es": "Spanish (es-US, es-ES)",
    "it": "Italian (it-IT)",
    "pt": "Portuguese (pt-BR, pt-PT)",
    "hi": "Hindi (hi-IN)",
    "ko": "Korean (ko-KR)",
    "en": "English (en-US, en-GB)",
    "de-DE": "German (de-DE)",
    "fr": "French (fr-FR, fr-CA)",
    "ru": "Russian (ru-RU)",
    "tr": "Turkish (tr-TR)",
    "vi-VN": "Vietnamese (vi-VN)",
    "nl": "Dutch (nl-NL)",
    "ja-JP": "Japanese (ja-JP)",
    "ar": "Arabic (ar-AR)",
    "uk": "Ukrainian (uk)",
    "pl": "Polish (pl-PL) - Alpha",
    "nb-NO": "Norwegian Bokmal (nb-NO) - Alpha",
    "fi": "Finnish (fi-FI) - Alpha",
    "zh-CN": "Mandarin (zh-CN) - Alpha",
    "cs": "Czech (cs-CZ) - Alpha",
    "bg": "Bulgarian (bg-BG) - Alpha",
    "sk": "Slovak (sk-SK) - Alpha",
    "sv": "Swedish (sv-SE) - Alpha",
    "hr": "Croatian (hr-HR) - Alpha",
    "ro": "Romanian (ro-RO) - Alpha",
    "et": "Estonian (et-EE) - Alpha",
    "da": "Danish (da-DK) - Alpha",
    "hu": "Hungarian (hu-HU) - Alpha",
    "el-GR": "Greek (el-GR) - Experimental",
    "he-IL": "Hebrew (he-IL) - Experimental",
    "lt-LT": "Lithuanian (lt-LT) - Experimental",
    "sl-SI": "Slovenian (sl-SI) - Experimental",
    "lv-LV": "Latvian (lv-LV) - Experimental",
    "mt-MT": "Maltese (mt-MT) - Experimental",
    "th-TH": "Thai (th-TH) - Experimental",
    "nn-NO": "Norwegian Nynorsk (nn-NO) - Experimental",
}


@dataclass(frozen=True, order=True)
class NemotronLanguage:
    raw_value: str

    @property
    def id(self) -> str:
        return self.raw_value

    @staticmethod
    def all_cases() -> list["NemotronLanguage"]:
        return [NemotronLanguage(raw) for raw in ALL_RAW_VALUES]

    @staticmethod
    def supported_language(raw_value: str) -> "NemotronLanguage | None":
        mapped = LEGACY_RAW_VALUES.get(raw_value, raw_value)
        return NemotronLanguage(mapped) if mapped in ALL_RAW_VALUES else None

    @property
    def display_name(self) -> str:
        return DISPLAY_NAMES.get(self.raw_value, self.raw_value)

    @property
    def compact_display_name(self) -> str:
        if self.raw_value == "auto":
            return "Auto"
        if self.raw_value == "hi":
            return "Hindi"
        if self.raw_value == "en":
            return "English"
        return self.display_name.split(" (")[0]

    def __str__(self) -> str:
        return self.raw_value


AUTO = NemotronLanguage("auto")
ENGLISH = NemotronLanguage("en")
