"""Which AI provider a dictation actually reaches, and whether it may run."""

from __future__ import annotations

import pytest

from fluentry.persistence.settings_store import PRIVATE_AI_PROVIDER_ID, SettingsStore
from fluentry.persistence.settings_types import (
    DictationPromptConfiguration,
    DictationPromptProfile,
    DictationPromptSelection,
    PromptMode,
    PromptRoutingScope,
    SavedProvider,
    AppPromptBinding,
)
from fluentry.services.provider_routing import (
    CUSTOM_PROVIDER_PREFIX,
    ProviderModelVerificationStore,
    ProviderRoute,
    ai_post_processing_is_configured,
    dictation_provider_id,
    is_local_endpoint,
    private_ai_route,
    provider_fingerprint,
    provider_is_configured,
    provider_key,
    resolve_route,
    resolve_route_for_post_processing,
    resolve_route_for_provider,
    route_is_configured,
    setup_issue,
)

OPENAI_BASE = "https://api.openai.com/v1"


def verify(settings: SettingsStore, key: str, base_url: str, api_key: str) -> None:
    fingerprints = settings.verified_provider_fingerprints
    fingerprints[key] = provider_fingerprint(base_url, api_key)
    settings.verified_provider_fingerprints = fingerprints


def configured_openai(settings: SettingsStore) -> dict[str, str]:
    settings.selected_provider_id = "openai"
    settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    verify(settings, "openai", OPENAI_BASE, "sk-test")
    return {"openai": "sk-test"}


# --- provider keys ----------------------------------------------------------


def test_a_built_in_provider_keeps_its_bare_id():
    assert provider_key("openai") == "openai"
    assert provider_key("  ollama  ") == "ollama"


def test_a_user_added_provider_is_namespaced():
    assert provider_key("my-server") == "custom:my-server"
    assert provider_key("custom:my-server") == "custom:my-server"


def test_an_empty_provider_has_no_key():
    assert provider_key("   ") == ""


# --- endpoints --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:11434/v1",
        "http://10.0.0.5:8080",
        "http://192.168.1.20:1234/v1",
        "http://172.16.0.9/v1",
        "http://172.31.255.1/v1",
    ],
)
def test_local_endpoints_are_recognised(url):
    assert is_local_endpoint(url) is True


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "http://172.32.0.1/v1", "http://example.com", "", "not a url"],
)
def test_remote_endpoints_are_not_mistaken_for_local(url):
    assert is_local_endpoint(url) is False


def test_the_fingerprint_covers_both_endpoint_and_key():
    first = provider_fingerprint(OPENAI_BASE, "sk-one")
    assert first is not None
    assert first != provider_fingerprint(OPENAI_BASE, "sk-two")
    assert first != provider_fingerprint("https://other.example/v1", "sk-one")
    assert provider_fingerprint("", "sk-one") is None


def test_the_fingerprint_does_not_contain_the_key():
    assert "sk-secret" not in provider_fingerprint(OPENAI_BASE, "sk-secret")


# --- resolving --------------------------------------------------------------


def test_the_global_provider_resolves_without_a_slot(settings):
    keys = configured_openai(settings)
    route = resolve_route(settings, api_keys=keys)
    assert route.provider_id == "openai"
    assert route.provider_key == "openai"
    assert route.base_url == OPENAI_BASE
    assert route.model == "gpt-4o-mini"
    assert route.api_key == "sk-test"


def test_a_saved_provider_resolves_through_its_namespaced_key(settings):
    settings.saved_providers = [
        SavedProvider(id="ABC", name="My Server", base_url="http://localhost:8000/v1", models=["m1"])
    ]
    settings.selected_provider_id = "ABC"
    route = resolve_route(settings, api_keys={f"{CUSTOM_PROVIDER_PREFIX}ABC": "key"})
    assert route.provider_key == "custom:ABC"
    assert route.base_url == "http://localhost:8000/v1"
    assert route.model == "m1"
    assert route.api_key == "key"


def test_an_unknown_provider_gets_no_base_url(settings):
    settings.defaults.set("SelectedProviderID", "mystery")
    route = resolve_route(settings)
    # available_selected_provider_id blanks a provider that no longer exists.
    assert route.base_url == ""


def test_a_prompt_configuration_overrides_the_global_provider(settings):
    keys = configured_openai(settings)
    settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="groq", model_name="llama-3.1-8b"),
        DictationPromptSelection.DEFAULT,
    )
    route = resolve_route(settings, dictation_slot="primary", api_keys=keys)
    assert route.provider_id == "groq"
    assert route.model == "llama-3.1-8b"


def test_a_half_filled_configuration_falls_back_to_the_global_provider(settings):
    keys = configured_openai(settings)
    settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="groq", model_name=""),
        DictationPromptSelection.DEFAULT,
    )
    route = resolve_route(settings, dictation_slot="primary", api_keys=keys)
    assert route.provider_id == "openai"


def test_a_prompt_selection_of_off_resolves_to_nothing(settings):
    configured_openai(settings)
    settings.dictation_prompt_selection = DictationPromptSelection.OFF
    assert resolve_route(settings, dictation_slot="primary").is_empty


def test_private_ai_resolves_to_nothing_without_a_local_runtime(settings):
    settings.dictation_prompt_selection = DictationPromptSelection.PRIVATE_AI
    assert private_ai_route(settings) == ProviderRoute()
    assert resolve_route(settings, dictation_slot="primary").is_empty


def test_private_ai_is_never_an_accidental_fallback(settings):
    """A stored Private AI selection must not silently route to a cloud provider."""
    settings.defaults.set("SelectedProviderID", PRIVATE_AI_PROVIDER_ID)
    settings.dictation_prompt_selection = DictationPromptSelection.profile("style")
    route = resolve_route(settings, dictation_slot="primary")
    assert route.is_empty


def test_resolving_for_a_named_provider_ignores_the_selection(settings):
    settings.saved_providers = [
        SavedProvider(id="ABC", name="Mine", base_url="http://localhost:9000/v1")
    ]
    route = resolve_route_for_provider(
        settings, "ABC", "some-model", api_keys={"custom:ABC": "key"}
    )
    assert route.model == "some-model"
    assert route.base_url == "http://localhost:9000/v1"


# --- per-app bindings -------------------------------------------------------


def dictate_profile(identifier: str = "style") -> DictationPromptProfile:
    return DictationPromptProfile(id=identifier, name="Style", prompt="Be terse.")


def test_selected_apps_only_means_off_in_an_unbound_app(settings):
    keys = configured_openai(settings)
    settings.dictation_prompt_routing_scope = PromptRoutingScope.SELECTED_APPS_ONLY
    route = resolve_route(settings, dictation_slot="primary", app_bundle_id="org.gnome.Text", api_keys=keys)
    assert route.is_empty


def test_a_bound_app_uses_its_profile(settings):
    keys = configured_openai(settings)
    profile = dictate_profile()
    settings.dictation_prompt_profiles = [profile]
    settings.dictation_prompt_routing_scope = PromptRoutingScope.SELECTED_APPS_ONLY
    settings.app_prompt_bindings = [
        AppPromptBinding(
            mode=PromptMode.DICTATE,
            app_bundle_id="org.gnome.text",
            app_name="Text",
            prompt_id=profile.id,
        )
    ]
    settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="groq", model_name="llama-3.1-8b"),
        DictationPromptSelection.profile(profile.id),
    )
    route = resolve_route(
        settings, dictation_slot="primary", app_bundle_id="org.gnome.Text", api_keys=keys
    )
    assert route.provider_id == "groq"


def test_a_binding_pointing_at_a_deleted_profile_falls_back_to_default(settings):
    keys = configured_openai(settings)
    settings.dictation_prompt_routing_scope = PromptRoutingScope.SELECTED_APPS_ONLY
    settings.app_prompt_bindings = [
        AppPromptBinding(
            mode=PromptMode.DICTATE,
            app_bundle_id="org.gnome.text",
            app_name="Text",
            prompt_id="deleted",
        )
    ]
    route = resolve_route(
        settings, dictation_slot="primary", app_bundle_id="org.gnome.Text", api_keys=keys
    )
    assert route.provider_id == "openai"


def test_bindings_match_app_ids_case_insensitively(settings):
    settings.app_prompt_bindings = [
        AppPromptBinding(
            mode=PromptMode.DICTATE, app_bundle_id="org.gnome.text", app_name="Text", prompt_id=None
        )
    ]
    assert settings.has_app_prompt_binding(PromptMode.DICTATE, "ORG.GNOME.TEXT") is True
    assert settings.has_app_prompt_binding(PromptMode.DICTATE, "org.kde.kate") is False
    assert settings.has_app_prompt_binding(PromptMode.DICTATE, None) is False


def test_an_edit_binding_does_not_answer_for_dictation(settings):
    settings.app_prompt_bindings = [
        AppPromptBinding(
            mode=PromptMode.EDIT, app_bundle_id="org.gnome.text", app_name="Text", prompt_id=None
        )
    ]
    assert settings.has_app_prompt_binding(PromptMode.DICTATE, "org.gnome.text") is False


def test_post_processing_uses_the_global_provider_when_routing_is_app_scoped(settings):
    """Post-processing has no focused app, so it must not resolve to "off"."""
    keys = configured_openai(settings)
    settings.dictation_prompt_routing_scope = PromptRoutingScope.SELECTED_APPS_ONLY
    route = resolve_route_for_post_processing(settings, api_keys=keys)
    assert route.provider_id == "openai"


# --- the gate ---------------------------------------------------------------


def test_a_verified_provider_with_a_key_is_configured(settings):
    keys = configured_openai(settings)
    assert provider_is_configured(settings, api_keys=keys) is True
    assert ai_post_processing_is_configured(settings, api_keys=keys) is True


def test_an_unverified_provider_is_not_configured(settings):
    settings.selected_provider_id = "openai"
    settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    assert provider_is_configured(settings, api_keys={"openai": "sk-test"}) is False


def test_a_changed_api_key_invalidates_the_verification(settings):
    configured_openai(settings)
    assert provider_is_configured(settings, api_keys={"openai": "sk-different"}) is False


def test_a_remote_provider_without_a_key_is_not_configured(settings):
    settings.selected_provider_id = "openai"
    settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    verify(settings, "openai", OPENAI_BASE, "")
    assert provider_is_configured(settings, api_keys={}) is False


def test_a_local_provider_needs_no_key(settings):
    settings.selected_provider_id = "ollama"
    settings.selected_model_by_provider = {"ollama": "llama3"}
    verify(settings, "ollama", "http://localhost:11434/v1", "")
    assert provider_is_configured(settings, api_keys={}) is True


def test_a_provider_with_no_model_is_not_configured(settings):
    settings.selected_provider_id = "openai"
    verify(settings, "openai", OPENAI_BASE, "sk-test")
    assert provider_is_configured(settings, api_keys={"openai": "sk-test"}) is False


def test_the_gate_is_closed_when_the_prompt_selection_is_off(settings):
    keys = configured_openai(settings)
    settings.dictation_prompt_selection = DictationPromptSelection.OFF
    assert ai_post_processing_is_configured(settings, api_keys=keys) is False
    # The provider itself is still configured, which is what prompt mode asks.
    assert provider_is_configured(settings, api_keys=keys) is True


def test_the_gate_is_closed_in_an_app_with_no_binding(settings):
    keys = configured_openai(settings)
    settings.dictation_prompt_routing_scope = PromptRoutingScope.SELECTED_APPS_ONLY
    assert (
        ai_post_processing_is_configured(settings, app_bundle_id="org.kde.kate", api_keys=keys)
        is False
    )


def test_an_empty_route_is_never_configured(settings):
    assert route_is_configured(ProviderRoute(), settings) is False


# --- the main shortcut's provider ------------------------------------------


def resolve_id(selection, global_provider, provider="", model=""):
    return dictation_provider_id(
        selection,
        DictationPromptConfiguration(provider_id=provider, model_name=model),
        global_provider,
        private_provider_id="fluid-1",
    )


def test_off_names_no_provider():
    assert resolve_id(DictationPromptSelection.OFF, "openai", "openrouter", "model") == ""


def test_private_ai_names_the_private_provider():
    assert resolve_id(DictationPromptSelection.PRIVATE_AI, "openai") == "fluid-1"


def test_default_falls_through_to_the_global_provider():
    assert resolve_id(DictationPromptSelection.DEFAULT, "openai") == "openai"


def test_a_complete_configuration_wins_and_is_trimmed():
    assert resolve_id(DictationPromptSelection.DEFAULT, "openai", " openrouter ", " model ") == (
        "openrouter"
    )


def test_a_profile_uses_its_own_provider():
    assert resolve_id(DictationPromptSelection.profile("style"), "openai", "custom", "model") == (
        "custom"
    )


def test_default_with_a_private_global_provider_stays_private():
    assert resolve_id(DictationPromptSelection.DEFAULT, "fluid-1") == "fluid-1"


def test_a_profile_never_inherits_the_private_provider():
    assert resolve_id(DictationPromptSelection.profile("style"), "fluid-1") == ""


def test_a_half_configured_default_over_private_names_nothing():
    assert resolve_id(DictationPromptSelection.DEFAULT, "fluid-1", "openai", "") == ""


def test_a_configuration_missing_its_model_falls_back():
    assert resolve_id(DictationPromptSelection.DEFAULT, "openai", "openrouter", "") == "openai"
    assert resolve_id(DictationPromptSelection.DEFAULT, "openai", "", "model") == "openai"


# --- what is still missing --------------------------------------------------


def test_a_missing_key_is_reported_first():
    assert setup_issue(True, False, True, True, False) == "API key missing"


def test_a_keyless_provider_needs_no_key():
    assert setup_issue(False, False, True, True, False) is None


def test_a_missing_model_is_reported():
    assert setup_issue(True, True, False, False, False) == "Choose a model"


def test_never_having_tested_is_not_a_problem():
    assert setup_issue(True, True, True, False, False) is None


def test_a_failed_test_is_a_problem():
    assert setup_issue(True, True, True, False, True) == "Verification failed"


# --- remembered verifications ----------------------------------------------


def test_an_identity_covers_every_field(defaults):
    identity = ProviderModelVerificationStore.identity
    base = identity("openai", OPENAI_BASE, "sk-a", "gpt-4o")
    assert len(base) == 64
    assert base != identity("groq", OPENAI_BASE, "sk-a", "gpt-4o")
    assert base != identity("openai", "https://other/v1", "sk-a", "gpt-4o")
    assert base != identity("openai", OPENAI_BASE, "sk-b", "gpt-4o")
    assert base != identity("openai", OPENAI_BASE, "sk-a", "gpt-4o-mini")


def test_an_identity_ignores_surrounding_whitespace():
    identity = ProviderModelVerificationStore.identity
    assert identity(" openai ", OPENAI_BASE, "sk-a", " gpt-4o ") == identity(
        "openai", OPENAI_BASE, "sk-a", "gpt-4o"
    )


def test_the_identity_does_not_contain_the_key():
    assert "sk-secret" not in ProviderModelVerificationStore.identity(
        "openai", OPENAI_BASE, "sk-secret", "gpt-4o"
    )


def test_a_recorded_success_survives_a_reload(defaults):
    store = ProviderModelVerificationStore(defaults=defaults)
    identity = ProviderModelVerificationStore.identity("openai", OPENAI_BASE, "sk-a", "gpt-4o")
    assert store.contains(identity) is False
    store.record_success(identity, now=1000.0)
    assert ProviderModelVerificationStore(defaults=defaults).contains(identity) is True


def test_a_removed_success_is_forgotten(defaults):
    store = ProviderModelVerificationStore(defaults=defaults)
    identity = ProviderModelVerificationStore.identity("openai", OPENAI_BASE, "sk-a", "gpt-4o")
    store.record_success(identity, now=1000.0)
    store.remove(identity)
    assert ProviderModelVerificationStore(defaults=defaults).contains(identity) is False


def test_the_store_keeps_the_most_recent_entries(defaults):
    store = ProviderModelVerificationStore(defaults=defaults)
    identities = [
        ProviderModelVerificationStore.identity("openai", OPENAI_BASE, "sk", f"model-{index}")
        for index in range(ProviderModelVerificationStore.MAXIMUM_ENTRIES + 10)
    ]
    for index, identity in enumerate(identities):
        store.record_success(identity, now=float(index))

    reloaded = ProviderModelVerificationStore(defaults=defaults)
    assert reloaded.contains(identities[-1]) is True
    assert reloaded.contains(identities[0]) is False


def test_malformed_stored_entries_are_dropped(defaults):
    defaults.set(
        ProviderModelVerificationStore.DEFAULTS_KEY,
        {"too-short": 1.0, "x" * 64: 5.0},
    )
    store = ProviderModelVerificationStore(defaults=defaults)
    assert store.contains("too-short") is False
    assert store.contains("x" * 64) is True
