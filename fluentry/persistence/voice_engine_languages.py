"""Which engine to use for which language.

A port of `VoiceEngineLanguageCatalog`. A *route* pairs a language with a
model and with whatever setting that model needs to be told about the
language — Whisper wants a language code, Cohere and Nemotron want their own
enums, Parakeet detects the language itself.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..i18n import tr
from .nemotron_language import NemotronLanguage
from .settings_types import CohereLanguage
from .speech_model import SpeechModel

AUTOMATIC = "automatic"
COHERE = "cohere"
NEMOTRON = "nemotron"
WHISPER = "whisper"


@dataclass(frozen=True)
class VoiceEngineLanguage:
    id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    is_popular: bool = False

    @property
    def popular_display_name(self) -> str:
        # "Mandarin Chinese" is too long for the onboarding grid.
        return "Mandarin" if self.id == "zh" else self.display_name


@dataclass(frozen=True)
class LanguageBinding:
    kind: str
    value: str | None = None

    @property
    def id(self) -> str:
        if self.kind == AUTOMATIC:
            return "auto"
        return f"{self.kind.replace('_', '')}-{self.value}"


@dataclass(frozen=True)
class VoiceEngineLanguageRoute:
    language: VoiceEngineLanguage
    model: SpeechModel
    binding: LanguageBinding

    @property
    def id(self) -> str:
        return f"{self.language.id}-{self.model.value}-{self.binding.id}"

    @property
    def badge_text(self) -> str | None:
        if self.model in (SpeechModel.PARAKEET_TDT, SpeechModel.PARAKEET_TDT_V2):
            return tr("Optimized for Fluentry")
        return None


POPULAR_LANGUAGE_IDS = {"en", "es", "fr", "de", "pt", "it", "ja", "ko", "zh", "hi", "ar"}

PARAKEET_V3_LANGUAGE_IDS = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}

COHERE_LANGUAGE_MAP = {
    "ar": CohereLanguage.ARABIC,
    "de": CohereLanguage.GERMAN,
    "el": CohereLanguage.GREEK,
    "en": CohereLanguage.ENGLISH,
    "es": CohereLanguage.SPANISH,
    "fr": CohereLanguage.FRENCH,
    "it": CohereLanguage.ITALIAN,
    "ja": CohereLanguage.JAPANESE,
    "ko": CohereLanguage.KOREAN,
    "nl": CohereLanguage.DUTCH,
    "pl": CohereLanguage.POLISH,
    "pt": CohereLanguage.PORTUGUESE,
    "vi": CohereLanguage.VIETNAMESE,
    "zh": CohereLanguage.MANDARIN,
}

WHISPER_MODEL_ORDER = [SpeechModel.WHISPER_SMALL, SpeechModel.WHISPER_LARGE_TURBO]

#: Whisper's own language codes. A language not in here cannot be routed to
#: Whisper even though Whisper is installed.
WHISPER_SUPPORTED_LANGUAGE_CODES = {
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo",
    "br", "bs", "ca", "cs", "cy", "da", "de", "el", "en", "es",
    "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw",
    "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it", "ja",
    "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo",
    "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt",
    "ro", "ru", "sa", "sd", "si", "sk", "sl", "sn", "so", "sq",
    "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl",
    "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "zh",
}

_DEFINITIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("af", "Afrikaans", ()),
    ("am", "Amharic", ()),
    ("ar", "Arabic", ("Arab",)),
    ("as", "Assamese", ()),
    ("az", "Azerbaijani", ()),
    ("ba", "Bashkir", ()),
    ("be", "Belarusian", ()),
    ("bg", "Bulgarian", ()),
    ("bn", "Bengali", ("Bangla",)),
    ("bo", "Tibetan", ()),
    ("br", "Breton", ()),
    ("bs", "Bosnian", ()),
    ("ca", "Catalan", ()),
    ("cs", "Czech", ()),
    ("cy", "Welsh", ()),
    ("da", "Danish", ()),
    ("de", "German", ("Deutsch",)),
    ("el", "Greek", ()),
    ("en", "English", ()),
    ("es", "Spanish", ("Castilian",)),
    ("et", "Estonian", ()),
    ("eu", "Basque", ()),
    ("fa", "Persian", ("Farsi",)),
    ("fi", "Finnish", ()),
    ("fo", "Faroese", ()),
    ("fr", "French", ()),
    ("gl", "Galician", ()),
    ("gu", "Gujarati", ()),
    ("ha", "Hausa", ()),
    ("haw", "Hawaiian", ()),
    ("he", "Hebrew", ()),
    ("hi", "Hindi", ()),
    ("hr", "Croatian", ()),
    ("ht", "Haitian Creole", ()),
    ("hu", "Hungarian", ()),
    ("hy", "Armenian", ()),
    ("id", "Indonesian", ()),
    ("is", "Icelandic", ()),
    ("it", "Italian", ()),
    ("ja", "Japanese", ()),
    ("jw", "Javanese", ()),
    ("ka", "Georgian", ()),
    ("kk", "Kazakh", ()),
    ("km", "Khmer", ()),
    ("kn", "Kannada", ()),
    ("ko", "Korean", ()),
    ("la", "Latin", ()),
    ("lb", "Luxembourgish", ()),
    ("ln", "Lingala", ()),
    ("lo", "Lao", ()),
    ("lt", "Lithuanian", ()),
    ("lv", "Latvian", ()),
    ("mg", "Malagasy", ()),
    ("mi", "Maori", ()),
    ("mk", "Macedonian", ()),
    ("ml", "Malayalam", ()),
    ("mn", "Mongolian", ()),
    ("mr", "Marathi", ()),
    ("ms", "Malay", ()),
    ("mt", "Maltese", ()),
    ("my", "Myanmar", ("Burmese",)),
    ("ne", "Nepali", ()),
    ("nl", "Dutch", ()),
    ("nn", "Norwegian Nynorsk", ()),
    ("no", "Norwegian", ("Norwegian Bokmal",)),
    ("oc", "Occitan", ()),
    ("pa", "Punjabi", ()),
    ("pl", "Polish", ()),
    ("ps", "Pashto", ()),
    ("pt", "Portuguese", ()),
    ("ro", "Romanian", ("Moldavian", "Moldovan")),
    ("ru", "Russian", ()),
    ("sa", "Sanskrit", ()),
    ("sd", "Sindhi", ()),
    ("si", "Sinhala", ("Sinhalese",)),
    ("sk", "Slovak", ()),
    ("sl", "Slovenian", ()),
    ("sn", "Shona", ()),
    ("so", "Somali", ()),
    ("sq", "Albanian", ()),
    ("sr", "Serbian", ()),
    ("su", "Sundanese", ()),
    ("sv", "Swedish", ()),
    ("sw", "Swahili", ()),
    ("ta", "Tamil", ()),
    ("te", "Telugu", ()),
    ("tg", "Tajik", ()),
    ("th", "Thai", ()),
    ("tk", "Turkmen", ()),
    ("tl", "Tagalog", ("Filipino",)),
    ("tr", "Turkish", ()),
    ("tt", "Tatar", ()),
    ("uk", "Ukrainian", ()),
    ("ur", "Urdu", ()),
    ("uz", "Uzbek", ()),
    ("vi", "Vietnamese", ()),
    ("yi", "Yiddish", ()),
    ("yo", "Yoruba", ()),
    ("zh", "Mandarin Chinese", ("Chinese", "Mandarin")),
]

LANGUAGE_DEFINITIONS = [
    VoiceEngineLanguage(
        id=identifier,
        display_name=display_name,
        aliases=aliases,
        is_popular=identifier in POPULAR_LANGUAGE_IDS,
    )
    for identifier, display_name, aliases in _DEFINITIONS
]

WHISPER_LANGUAGE_CODE_MAP = {
    language.id: language.id
    for language in LANGUAGE_DEFINITIONS
    if language.id in WHISPER_SUPPORTED_LANGUAGE_CODES
}


def _nemotron_language_id(language: NemotronLanguage) -> str:
    if language.raw_value == "nb-NO":
        return "no"
    return language.raw_value.split("-", 1)[0]


def _build_nemotron_map() -> dict[str, NemotronLanguage]:
    known = {language.id for language in LANGUAGE_DEFINITIONS}
    mapping: dict[str, NemotronLanguage] = {}
    for language in NemotronLanguage.all_cases():
        if language.raw_value == "auto":
            continue
        identifier = _nemotron_language_id(language)
        if identifier in known:
            mapping[identifier] = language
    return mapping


NEMOTRON_LANGUAGE_MAP = _build_nemotron_map()


def whisper_language_code(language_id: str) -> str | None:
    return WHISPER_LANGUAGE_CODE_MAP.get(language_id)


def _route_candidates(language: VoiceEngineLanguage) -> list[VoiceEngineLanguageRoute]:
    routes: list[VoiceEngineLanguageRoute] = []

    def add(model: SpeechModel, binding: LanguageBinding) -> None:
        routes.append(VoiceEngineLanguageRoute(language=language, model=model, binding=binding))

    automatic = LanguageBinding(AUTOMATIC)

    if language.id == "en":
        add(SpeechModel.PARAKEET_TDT_V2, automatic)
        add(SpeechModel.PARAKEET_REALTIME, automatic)

    if language.id in PARAKEET_V3_LANGUAGE_IDS:
        add(SpeechModel.PARAKEET_TDT, automatic)

    cohere = COHERE_LANGUAGE_MAP.get(language.id)
    if cohere is not None:
        add(SpeechModel.COHERE_TRANSCRIBE_SIX_BIT, LanguageBinding(COHERE, cohere.value))

    nemotron = NEMOTRON_LANGUAGE_MAP.get(language.id)
    if nemotron is not None:
        binding = LanguageBinding(NEMOTRON, nemotron.raw_value)
        add(SpeechModel.NEMOTRON_STREAMING, binding)
        add(SpeechModel.NEMOTRON_OFFLINE, binding)

    code = whisper_language_code(language.id)
    if code is not None:
        for model in WHISPER_MODEL_ORDER:
            add(model, LanguageBinding(WHISPER, code))

    return routes


def routes(
    language: VoiceEngineLanguage, available_models: Sequence[SpeechModel] | None = None
) -> list[VoiceEngineLanguageRoute]:
    models = set(available_models if available_models is not None else SpeechModel.available_models())
    return [route for route in _route_candidates(language) if route.model in models]


def routes_for_language_id(
    language_id: str, available_models: Sequence[SpeechModel] | None = None
) -> list[VoiceEngineLanguageRoute]:
    found = language(language_id, available_models)
    return routes(found, available_models) if found else []


def all_languages(
    available_models: Sequence[SpeechModel] | None = None,
) -> list[VoiceEngineLanguage]:
    models = available_models if available_models is not None else SpeechModel.available_models()
    return [item for item in LANGUAGE_DEFINITIONS if routes(item, models)]


def popular_languages(
    available_models: Sequence[SpeechModel] | None = None,
) -> list[VoiceEngineLanguage]:
    return [item for item in all_languages(available_models) if item.is_popular]


def searchable_languages(
    query: str, available_models: Sequence[SpeechModel] | None = None
) -> list[VoiceEngineLanguage]:
    languages = all_languages(available_models)
    normalized = query.strip().lower()
    if not normalized:
        return languages
    return [
        item
        for item in languages
        if normalized in item.display_name.lower()
        or normalized in item.id.lower()
        or any(normalized in alias.lower() for alias in item.aliases)
    ]


def language(
    language_id: str, available_models: Sequence[SpeechModel] | None = None
) -> VoiceEngineLanguage | None:
    for item in all_languages(available_models):
        if item.id == language_id:
            return item
    return None


def whisper_languages() -> list[VoiceEngineLanguage]:
    return [item for item in LANGUAGE_DEFINITIONS if whisper_language_code(item.id) is not None]


def whisper_language_for_code(language_code: str) -> VoiceEngineLanguage | None:
    for item in whisper_languages():
        if whisper_language_code(item.id) == language_code:
            return item
    return None


def apply(route: VoiceEngineLanguageRoute, settings) -> None:
    """Point every relevant setting at this route."""
    settings.onboarding_selected_language_id = route.language.id
    settings.selected_speech_model = route.model

    if route.binding.kind == WHISPER:
        settings.selected_whisper_language_code = route.binding.value
    elif route.binding.kind == COHERE:
        settings.selected_cohere_language = CohereLanguage(route.binding.value)
    elif route.binding.kind == NEMOTRON:
        settings.selected_nemotron_language = route.binding.value
    # AUTOMATIC: the model detects the language itself.
