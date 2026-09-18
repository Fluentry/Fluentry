"""Which engine gets used for which language."""

from __future__ import annotations

import pytest

from fluentry.persistence import voice_engine_languages as catalog
from fluentry.persistence.nemotron_language import NemotronLanguage
from fluentry.persistence.settings_types import CohereLanguage
from fluentry.persistence.speech_model import SpeechModel

ALL_LINUX_MODELS = [model for model in SpeechModel if model.is_supported]


def route_ids(language_id: str, models=None):
    return [
        (route.model, route.binding.kind, route.binding.value)
        for route in catalog.routes_for_language_id(language_id, models or ALL_LINUX_MODELS)
    ]


# --- the catalog ------------------------------------------------------------


def test_every_popular_language_is_a_real_language():
    known = {language.id for language in catalog.LANGUAGE_DEFINITIONS}
    assert catalog.POPULAR_LANGUAGE_IDS <= known


def test_the_popular_list_is_the_eleven_the_picker_shows():
    popular = [language.id for language in catalog.popular_languages(ALL_LINUX_MODELS)]
    assert popular == ["ar", "de", "en", "es", "fr", "hi", "it", "ja", "ko", "pt", "zh"]


def test_mandarin_gets_a_shorter_name_in_the_picker():
    mandarin = catalog.language("zh", ALL_LINUX_MODELS)
    assert mandarin.display_name == "Mandarin Chinese"
    assert mandarin.popular_display_name == "Mandarin"
    assert catalog.language("fr", ALL_LINUX_MODELS).popular_display_name == "French"


def test_a_language_with_no_engine_is_not_listed():
    # Nothing routes anywhere when no model is installed.
    assert catalog.all_languages([]) == []


def test_search_matches_names_codes_and_aliases():
    def found(query):
        return {language.id for language in catalog.searchable_languages(query, ALL_LINUX_MODELS)}

    assert "de" in found("deutsch")
    assert "zh" in found("mandarin")
    assert "tl" in found("filipino")
    assert found("") == {language.id for language in catalog.all_languages(ALL_LINUX_MODELS)}
    assert found("kryptonian") == set()


def test_an_unknown_language_id_has_no_routes():
    assert catalog.language("kr-KR", ALL_LINUX_MODELS) is None
    assert catalog.routes_for_language_id("kr-KR", ALL_LINUX_MODELS) == []


# --- routing ----------------------------------------------------------------


def test_english_prefers_parakeet_then_falls_back_through_whisper():
    routes = route_ids("en")
    assert routes[0] == (SpeechModel.PARAKEET_TDT_V2, catalog.AUTOMATIC, None)
    assert routes[1] == (SpeechModel.PARAKEET_REALTIME, catalog.AUTOMATIC, None)
    assert (SpeechModel.WHISPER_SMALL, catalog.WHISPER, "en") in routes
    assert (SpeechModel.WHISPER_LARGE_TURBO, catalog.WHISPER, "en") in routes


def test_only_english_gets_the_english_only_parakeet_models():
    for language_id in ("fr", "de", "pt"):
        models = {model for model, _kind, _value in route_ids(language_id)}
        assert SpeechModel.PARAKEET_TDT_V2 not in models
        assert SpeechModel.PARAKEET_REALTIME not in models
        assert SpeechModel.PARAKEET_TDT in models


def test_a_language_parakeet_v3_does_not_cover_skips_it():
    models = {model for model, _kind, _value in route_ids("ja")}
    assert SpeechModel.PARAKEET_TDT not in models
    assert SpeechModel.COHERE_TRANSCRIBE_SIX_BIT in models


def test_cohere_routes_carry_their_own_language_enum():
    routes = dict(
        ((model, kind), value) for model, kind, value in route_ids("pt")
    )
    assert routes[(SpeechModel.COHERE_TRANSCRIBE_SIX_BIT, catalog.COHERE)] == (
        CohereLanguage.PORTUGUESE.value
    )


def test_nemotron_routes_use_the_regional_code_where_there_is_one():
    assert catalog.NEMOTRON_LANGUAGE_MAP["de"] == NemotronLanguage("de-DE")
    assert catalog.NEMOTRON_LANGUAGE_MAP["fr"] == NemotronLanguage("fr")
    # nb-NO is Norwegian, which the catalog calls "no".
    assert catalog.NEMOTRON_LANGUAGE_MAP["no"] == NemotronLanguage("nb-NO")


def test_the_streaming_nemotron_comes_before_the_offline_one():
    routes = [model for model, _kind, _value in route_ids("uk")]
    assert routes.index(SpeechModel.NEMOTRON_STREAMING) < routes.index(
        SpeechModel.NEMOTRON_OFFLINE
    )


def test_apple_models_never_appear_on_linux():
    for language in catalog.all_languages(SpeechModel.available_models()):
        for route in catalog.routes(language, SpeechModel.available_models()):
            assert route.model.is_supported


def test_a_route_is_filtered_out_when_its_model_is_not_installed():
    only_whisper = [SpeechModel.WHISPER_SMALL]
    assert route_ids("en", only_whisper) == [(SpeechModel.WHISPER_SMALL, catalog.WHISPER, "en")]


def test_parakeet_routes_are_badged_and_others_are_not():
    for route in catalog.routes_for_language_id("en", ALL_LINUX_MODELS):
        if route.model in (SpeechModel.PARAKEET_TDT, SpeechModel.PARAKEET_TDT_V2):
            assert route.badge_text == "Optimized for Fluentry"
        else:
            assert route.badge_text is None


def test_route_ids_are_unique_within_a_language():
    identifiers = [route.id for route in catalog.routes_for_language_id("en", ALL_LINUX_MODELS)]
    assert len(identifiers) == len(set(identifiers))


# --- whisper ----------------------------------------------------------------


def test_whisper_language_codes_only_exist_for_languages_whisper_knows():
    assert catalog.whisper_language_code("en") == "en"
    assert catalog.whisper_language_code("kryptonian") is None
    assert catalog.whisper_language_for_code("pt").display_name == "Portuguese"
    assert catalog.whisper_language_for_code("zz") is None


def test_every_listed_language_has_a_whisper_route():
    """Whisper is the fallback that makes the catalog complete.

    Every language in the definition list is one of Whisper's 99, so no
    language can end up with no engine at all once Whisper is installed.
    """
    assert [
        language.id
        for language in catalog.LANGUAGE_DEFINITIONS
        if catalog.whisper_language_code(language.id) is None
    ] == []


# --- applying a route -------------------------------------------------------


def test_applying_a_whisper_route_sets_the_model_and_the_code(settings):
    route = next(
        route
        for route in catalog.routes_for_language_id("pt", ALL_LINUX_MODELS)
        if route.binding.kind == catalog.WHISPER
    )
    catalog.apply(route, settings)
    assert settings.selected_speech_model == route.model
    assert settings.selected_whisper_language_code == "pt"
    assert settings.onboarding_selected_language_id == "pt"


def test_applying_a_cohere_route_sets_the_cohere_language(settings):
    route = next(
        route
        for route in catalog.routes_for_language_id("ja", ALL_LINUX_MODELS)
        if route.binding.kind == catalog.COHERE
    )
    catalog.apply(route, settings)
    assert settings.selected_cohere_language == CohereLanguage.JAPANESE


def test_applying_a_nemotron_route_sets_the_nemotron_language(settings):
    route = next(
        route
        for route in catalog.routes_for_language_id("de", ALL_LINUX_MODELS)
        if route.binding.kind == catalog.NEMOTRON
    )
    catalog.apply(route, settings)
    assert settings.selected_nemotron_language == "de-DE"


def test_applying_an_automatic_route_leaves_the_language_settings_alone(settings):
    settings.selected_whisper_language_code = "fr"
    route = next(
        route
        for route in catalog.routes_for_language_id("en", ALL_LINUX_MODELS)
        if route.binding.kind == catalog.AUTOMATIC
    )
    catalog.apply(route, settings)
    assert settings.selected_whisper_language_code == "fr"
    assert settings.selected_speech_model == route.model


# --- the Nemotron language type ---------------------------------------------


def test_nemotron_legacy_codes_map_forward():
    assert NemotronLanguage.supported_language("el") == NemotronLanguage("el-GR")
    assert NemotronLanguage.supported_language("lv") == NemotronLanguage("lv-LV")
    assert NemotronLanguage.supported_language("el-GR") == NemotronLanguage("el-GR")
    assert NemotronLanguage.supported_language("kryptonian") is None


def test_nemotron_display_names_shorten_for_the_picker():
    assert NemotronLanguage("auto").compact_display_name == "Auto"
    assert NemotronLanguage("en").compact_display_name == "English"
    assert NemotronLanguage("pl").display_name == "Polish (pl-PL) - Alpha"
    assert NemotronLanguage("pl").compact_display_name == "Polish"


def test_an_unknown_nemotron_code_falls_back_to_english(settings):
    settings.selected_nemotron_language = "kryptonian"
    assert settings.selected_nemotron_language == "en"
