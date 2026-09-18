"""Adding, editing and removing AI providers."""

from __future__ import annotations

import pytest

from fluentry.persistence.settings_store import PRIVATE_AI_PROVIDER_ID, SettingsStore
from fluentry.persistence.settings_types import (
    DictationPromptConfiguration,
    DictationPromptSelection,
    SavedProvider,
)
from fluentry.services.provider_routing import provider_fingerprint
from fluentry.services.provider_setup import (
    ADDED_PROVIDER_IDS_KEY,
    ProviderItem,
    ProviderSetupController,
    ProviderSetupDraft,
)


class FakeSecretStore:
    """Stands in for the desktop secret service."""

    def __init__(self) -> None:
        self.persisted: dict[str, str] = {}
        self.writes = 0
        self.fails = False

    def __call__(self, keys: dict[str, str]) -> bool:
        self.writes += 1
        if self.fails:
            return False
        self.persisted = dict(keys)
        return True


@pytest.fixture
def secrets() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def controller(settings: SettingsStore, secrets: FakeSecretStore) -> ProviderSetupController:
    return ProviderSetupController(
        settings=settings,
        persist_api_keys=secrets,
        catalog=[
            ProviderItem(id="openai", name="OpenAI", is_built_in=True),
            ProviderItem(id="ollama", name="Ollama", is_built_in=True),
        ],
    )


def local_draft(**overrides) -> ProviderSetupDraft:
    values = {"name": "Local", "base_url": "http://localhost:1234/v1", "model": "tiny"}
    values.update(overrides)
    return ProviderSetupDraft(**values)


# --- the draft --------------------------------------------------------------


def test_a_complete_draft_is_valid():
    assert local_draft().is_valid is True


def test_a_draft_needs_a_name():
    assert local_draft(name="   ").is_valid is False


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///tmp/model",
        "ftp://example.com",
        "",
        "not a url",
        "https://",
    ],
)
def test_only_http_endpoints_are_accepted(base_url):
    assert local_draft(base_url=base_url).is_valid is False


def test_credentials_embedded_in_the_url_are_rejected():
    """A URL-embedded secret would end up in logs and error messages."""
    assert local_draft(base_url="https://user:secret@example.com").is_valid is False
    assert local_draft(base_url="https://user@example.com").is_valid is False


def test_a_built_in_provider_needs_a_key():
    assert ProviderSetupDraft(
        provider_id="openai", name="OpenAI", base_url="https://api.openai.com/v1"
    ).is_valid is False
    assert ProviderSetupDraft(
        provider_id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    ).is_valid is True


def test_a_local_provider_needs_no_key():
    assert ProviderSetupDraft(
        provider_id="ollama", name="Ollama", base_url="http://localhost:11434/v1"
    ).is_valid is True


# --- the visible list -------------------------------------------------------


def test_a_fresh_catalog_shows_nothing(controller):
    assert controller.cached_added_provider_items == []


def test_editing_a_draft_persists_nothing(controller, secrets):
    draft = local_draft()
    assert draft.is_valid
    assert secrets.writes == 0
    assert controller.settings.saved_providers == []


def test_a_saved_provider_appears_without_verification(controller, secrets):
    assert controller.add_provider(local_draft()) is True
    assert len(controller.cached_added_provider_items) == 1


def test_the_selected_provider_stays_visible(controller):
    controller.settings.selected_provider_id = "openai"
    controller.refresh_provider_items()
    assert any(item.id == "openai" for item in controller.cached_added_provider_items)


def test_an_expired_credential_stays_discoverable(controller):
    """Losing verification must not make a provider vanish from the list."""
    controller.provider_api_keys["openai"] = "expired-key"
    controller.refresh_provider_items()
    assert any(item.id == "openai" for item in controller.cached_added_provider_items)


def test_a_referenced_provider_stays_visible_after_its_key_disappears(controller):
    controller.settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="openai", model_name="gpt-4o"),
        DictationPromptSelection.DEFAULT,
    )
    controller.refresh_provider_items()
    assert any(item.id == "openai" for item in controller.cached_added_provider_items)


def test_a_verified_provider_stays_visible(controller):
    controller.settings.verified_provider_fingerprints = {
        "openai": provider_fingerprint("https://api.openai.com/v1", "sk-test")
    }
    controller.refresh_provider_items()
    assert any(item.id == "openai" for item in controller.cached_added_provider_items)


def test_the_private_provider_is_never_listed(controller):
    controller.catalog = [
        *controller.catalog,
        ProviderItem(id=PRIVATE_AI_PROVIDER_ID, name="Private AI", is_built_in=True),
    ]
    controller.settings.defaults.set(ADDED_PROVIDER_IDS_KEY, [PRIVATE_AI_PROVIDER_ID])
    controller.refresh_provider_items()
    assert all(item.id != PRIVATE_AI_PROVIDER_ID for item in controller.cached_added_provider_items)


def test_the_list_is_sorted_by_name(controller):
    controller.settings.defaults.set(ADDED_PROVIDER_IDS_KEY, ["openai", "ollama"])
    controller.refresh_provider_items()
    assert [item.name for item in controller.cached_added_provider_items] == ["Ollama", "OpenAI"]


# --- adding -----------------------------------------------------------------


def test_adding_saves_a_custom_provider(controller, secrets):
    assert controller.add_provider(local_draft(api_key="test-key")) is True
    assert len(controller.settings.saved_providers) == 1
    assert secrets.persisted != {}


def test_adding_never_changes_the_current_route(controller):
    controller.settings.selected_provider_id = "openai"
    controller.settings.selected_model_by_provider = {"openai": "gpt-4o-mini"}
    controller.add_provider(local_draft())
    assert controller.settings.selected_provider_id == "openai"
    assert controller.settings.selected_model_by_provider["openai"] == "gpt-4o-mini"


def test_a_failed_secret_write_leaves_nothing_behind(controller, secrets):
    secrets.fails = True
    assert controller.add_provider(local_draft(api_key="test-key")) is False
    assert controller.settings.saved_providers == []
    assert controller.provider_api_keys == {}
    assert secrets.persisted == {}


def test_two_providers_may_share_a_display_name(controller):
    assert controller.add_provider(local_draft()) is True
    assert controller.add_provider(local_draft()) is True
    assert len(controller.settings.saved_providers) == 2


def test_a_built_in_provider_can_be_added_once(controller):
    built_in = ProviderSetupDraft(
        provider_id="ollama", name="Ollama", base_url="http://localhost:11434/v1"
    )
    assert controller.add_provider(built_in) is True
    assert any(item.id == "ollama" for item in controller.cached_added_provider_items)
    assert controller.add_provider(built_in) is False


def test_an_unknown_provider_id_cannot_be_added(controller):
    draft = local_draft(provider_id="mystery", api_key="k")
    assert controller.add_provider(draft) is False


def test_an_in_flight_request_blocks_adding(controller):
    controller.is_fetching_models = True
    assert controller.add_provider(local_draft()) is False
    controller.is_fetching_models = False
    controller.is_testing_connection = True
    assert controller.add_provider(local_draft()) is False


def test_an_invalid_draft_is_not_added(controller):
    assert controller.add_provider(local_draft(base_url="file:///tmp/x")) is False
    assert controller.settings.saved_providers == []


# --- removing ---------------------------------------------------------------


@pytest.fixture
def populated(controller, secrets):
    controller.provider_api_keys = {"openai": "remove-key", "other": "keep-key"}
    controller.settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="openai", model_name="gpt-4o"),
        DictationPromptSelection.DEFAULT,
    )
    controller.settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(provider_id="other", model_name="keep"),
        DictationPromptSelection.profile("unrelated"),
    )
    controller.settings.rewrite_mode_selected_provider_id = "openai"
    controller.settings.rewrite_mode_selected_model = "old"
    controller.settings.command_mode_selected_provider_id = "other"
    controller.settings.command_mode_selected_model = "keep-model"
    controller.settings.available_models_by_provider = {"openai": ["old"], "other": ["keep"]}
    controller.settings.selected_model_by_provider = {"openai": "old"}
    controller.settings.defaults.set(ADDED_PROVIDER_IDS_KEY, ["openai", "other"])
    controller.selected_provider_id = "openai"
    return controller


def test_a_failed_credential_removal_fails_the_whole_operation(populated, secrets):
    before = dict(populated.settings.dictation_prompt_configurations)
    secrets.fails = True

    assert populated.delete_current_provider() is False
    assert populated.provider_api_keys["openai"] == "remove-key"
    assert populated.settings.dictation_prompt_configurations == before
    assert populated.settings.defaults.string_array(ADDED_PROVIDER_IDS_KEY) == ["openai", "other"]


def test_a_busy_editor_cannot_remove_a_provider(populated):
    populated.is_fetching_models = True
    assert populated.delete_current_provider() is False


def test_removal_disconnects_only_the_affected_routes(populated):
    assert populated.delete_current_provider() is True
    configurations = populated.settings.dictation_prompt_configurations
    assert configurations["__default__"].provider_id == ""
    assert configurations["profile:unrelated"].provider_id == "other"


def test_removal_preserves_hotkeys_on_the_disconnected_prompt(populated):
    from fluentry.models.hotkey import HotkeyShortcut
    from fluentry.models.keycodes import KEY_F12, ModifierFlags

    shortcut = HotkeyShortcut.keyboard(KEY_F12, ModifierFlags.NONE)
    populated.settings.set_dictation_prompt_configuration(
        DictationPromptConfiguration(
            shortcut=shortcut, provider_id="openai", model_name="gpt-4o"
        ),
        DictationPromptSelection.DEFAULT,
    )
    assert populated.delete_current_provider() is True
    kept = populated.settings.dictation_prompt_configurations["__default__"]
    assert kept.provider_id == ""
    assert kept.shortcut == shortcut


def test_removal_clears_the_affected_mode_routes(populated):
    populated.delete_current_provider()
    assert populated.settings.rewrite_mode_selected_provider_id == ""
    assert populated.settings.rewrite_mode_selected_model is None
    assert populated.settings.command_mode_selected_provider_id == "other"
    assert populated.settings.command_mode_selected_model == "keep-model"


def test_removal_keeps_unrelated_credentials_and_models(populated):
    populated.delete_current_provider()
    assert populated.provider_api_keys == {"other": "keep-key"}
    assert populated.settings.available_models_by_provider["other"] == ["keep"]


def test_a_removed_provider_disappears_from_the_list(populated):
    populated.delete_current_provider()
    assert all(item.id != "openai" for item in populated.cached_added_provider_items)
    assert populated.settings.defaults.string_array(ADDED_PROVIDER_IDS_KEY) == ["other"]


def test_removing_the_default_leaves_no_provider_selected(populated):
    populated.settings.selected_provider_id = "openai"
    populated.settings.selected_model = "old"
    assert populated.delete_current_provider() is True
    assert populated.settings.selected_provider_id == ""
    assert populated.settings.selected_model is None


def test_removing_a_keyless_provider_needs_no_secret_write(controller, secrets):
    controller.selected_provider_id = "ollama"
    before = secrets.writes
    secrets.fails = True  # Would fail if it were called at all.
    assert controller.delete_current_provider() is True
    assert secrets.writes == before


def test_the_private_provider_cannot_be_removed(controller):
    controller.selected_provider_id = PRIVATE_AI_PROVIDER_ID
    assert controller.delete_current_provider() is False


def test_removing_a_verified_provider_drops_its_verification(populated):
    populated.settings.verified_provider_fingerprints = {"openai": "fingerprint"}
    populated.delete_current_provider()
    assert "openai" not in populated.settings.verified_provider_fingerprints


# --- closing the editor -----------------------------------------------------


def test_an_unchanged_key_closes_without_a_secret_write(controller, secrets):
    controller.selected_provider_id = "openai"
    controller.provider_api_keys["openai"] = "edited-key"
    controller.managed_original_key = "edited-key"
    secrets.fails = True  # Would fail if it were called at all.
    assert controller.save_managed_provider_before_closing("openai") is True
    assert secrets.writes == 0


def test_a_failed_write_keeps_the_editor_open_with_the_draft(controller, secrets):
    controller.selected_provider_id = "openai"
    controller.provider_api_keys["openai"] = "edited-key"
    controller.managed_original_key = "old-key"
    secrets.fails = True
    assert controller.save_managed_provider_before_closing("openai") is False
    assert controller.provider_api_keys["openai"] == "edited-key"


def test_an_edited_key_is_persisted_on_close(controller, secrets):
    controller.selected_provider_id = "openai"
    controller.provider_api_keys["openai"] = "edited-key"
    controller.managed_original_key = "old-key"
    assert controller.save_managed_provider_before_closing("openai") is True
    assert secrets.persisted["openai"] == "edited-key"
    assert controller.managed_original_key == "edited-key"


def test_closing_a_provider_that_is_not_the_open_one_writes_nothing(controller, secrets):
    controller.selected_provider_id = "ollama"
    controller.managed_original_key = "old-key"
    assert controller.save_managed_provider_before_closing("openai") is True
    assert secrets.writes == 0


def test_a_busy_editor_cannot_be_dismissed(controller):
    controller.is_testing_connection = True
    assert controller.save_managed_provider_before_closing("openai") is False
