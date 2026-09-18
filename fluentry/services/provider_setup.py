"""Adding, editing and removing AI providers.

A port of `ProviderSetupDraft` and the provider-management half of
`AIEnhancementSettingsViewModel`.

The rule that shapes all of it: **credentials are saved first, and a failed
save changes nothing**. If the secret store rejects a write, no provider
record, model list or membership entry is created, and the form stays open
with the user's typing intact. The alternative — a provider that exists but
whose key was never stored — looks configured and silently fails at
dictation time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from ..persistence.settings_store import (
    BUILT_IN_PROVIDER_IDS,
    PRIVATE_AI_PROVIDER_ID,
    SettingsStore,
)
from ..persistence.settings_types import SavedProvider
from .provider_routing import provider_key

ADDED_PROVIDER_IDS_KEY = "AISettingsAddedProviderIDs"

#: Providers that run on the machine and therefore need no API key.
KEYLESS_PROVIDER_IDS = frozenset({"ollama", "lmstudio"})


@dataclass
class ProviderSetupDraft:
    """A local form value, never a live settings binding.

    Cancelling simply discards it, which is why nothing here writes.
    """

    provider_id: str = ""
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    @property
    def trimmed_name(self) -> str:
        return self.name.strip()

    @property
    def trimmed_model(self) -> str:
        return self.model.strip()

    @property
    def trimmed_base_url(self) -> str:
        return self.base_url.strip()

    @property
    def requires_api_key(self) -> bool:
        return bool(self.provider_id) and self.provider_id not in KEYLESS_PROVIDER_IDS

    @property
    def is_valid(self) -> bool:
        if self.requires_api_key and not self.api_key.strip():
            return False
        if not self.trimmed_name:
            return False
        try:
            url = urlparse(self.trimmed_base_url)
        except ValueError:
            return False
        if (url.scheme or "").lower() not in ("http", "https"):
            return False
        try:
            if not url.hostname:
                return False
            # Credentials in the URL would end up in logs and history.
            if url.username is not None or url.password is not None:
                return False
        except ValueError:
            return False
        return True


@dataclass(frozen=True)
class ProviderItem:
    id: str
    name: str
    is_built_in: bool


@dataclass
class ProviderSetupController:
    """The provider list, and the operations that change it."""

    settings: SettingsStore
    #: Persists the whole key map; returns False when the secret store refused.
    persist_api_keys: Callable[[dict[str, str]], bool]
    catalog: list[ProviderItem] = field(default_factory=list)

    provider_api_keys: dict[str, str] = field(default_factory=dict)
    selected_provider_id: str = ""
    managed_original_key: str | None = None
    is_testing_connection: bool = False
    is_fetching_models: bool = False
    cached_added_provider_items: list[ProviderItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.catalog:
            from ..ui.pages import BUILT_IN_PROVIDERS

            self.catalog = [
                ProviderItem(id=identifier, name=label, is_built_in=True)
                for identifier, label, _base_url in BUILT_IN_PROVIDERS
            ]
        self.refresh_provider_items()

    # --- the visible list -------------------------------------------------

    def provider_api_key(self, provider_id: str) -> str:
        return self.provider_api_keys.get(provider_id, "")

    def _explicit_added_ids(self) -> set[str]:
        return set(self.settings.defaults.string_array(ADDED_PROVIDER_IDS_KEY) or [])

    def _set_explicit_added_ids(self, identifiers) -> None:
        self.settings.defaults.set(ADDED_PROVIDER_IDS_KEY, sorted(identifiers))

    def added_provider_items(self, items: list[ProviderItem]) -> list[ProviderItem]:
        """Which providers the user should see in their own list.

        A built-in provider only appears once the user has actually engaged
        with it. Legacy connections stay discoverable even after their
        verification or credentials expire, so nothing silently vanishes.
        """
        explicit = self._explicit_added_ids()
        referenced = {
            configuration.provider_id
            for configuration in self.settings.dictation_prompt_configurations.values()
        }
        visible = []
        for item in items:
            if item.id == PRIVATE_AI_PROVIDER_ID:
                continue
            key = provider_key(item.id)
            if (
                not item.is_built_in
                or item.id in explicit
                or item.id in referenced
                or self.settings.selected_provider_id == item.id
                or self.provider_api_key(item.id).strip()
                or self.settings.verified_provider_fingerprints.get(key) is not None
            ):
                visible.append(item)
        return sorted(visible, key=lambda item: item.name.casefold())

    def refresh_provider_items(self) -> None:
        items = list(self.catalog) + [
            ProviderItem(id=provider.id, name=provider.name, is_built_in=False)
            for provider in self.settings.saved_providers
        ]
        self.cached_added_provider_items = self.added_provider_items(items)

    # --- adding -----------------------------------------------------------

    def add_provider(self, draft: ProviderSetupDraft) -> bool:
        """Save credentials, then record the provider. Never selects it."""
        if not draft.is_valid or self.is_testing_connection or self.is_fetching_models:
            return False

        built_in = bool(draft.provider_id) and draft.provider_id in BUILT_IN_PROVIDER_IDS
        if draft.provider_id and not built_in:
            return False
        if built_in and any(item.id == draft.provider_id for item in self.cached_added_provider_items):
            return False

        models = [draft.trimmed_model] if draft.trimmed_model else []
        provider = SavedProvider(
            id=str(uuid.uuid4()).upper(),
            name=draft.trimmed_name,
            base_url=draft.trimmed_base_url,
            models=models,
        )
        identifier = draft.provider_id if built_in else provider.id
        key = provider_key(identifier)

        api_key = draft.api_key.strip()
        if api_key:
            previous_keys = dict(self.provider_api_keys)
            self.provider_api_keys[identifier] = api_key
            if not self.persist_api_keys(dict(self.provider_api_keys)):
                # Nothing else has happened yet, so undoing the draft is enough.
                self.provider_api_keys = previous_keys
                self.refresh_provider_items()
                return False

        if not built_in:
            self.settings.saved_providers = [*self.settings.saved_providers, provider]

        available = self.settings.available_models_by_provider
        available[key] = models
        self.settings.available_models_by_provider = available

        selected = self.settings.selected_model_by_provider
        selected[key] = models[0] if models else ""
        self.settings.selected_model_by_provider = selected

        self._set_explicit_added_ids(self._explicit_added_ids() | {identifier})
        self.refresh_provider_items()
        return True

    # --- removing ---------------------------------------------------------

    def clear_provider_assignments(self, provider_id: str) -> None:
        """Disconnect routes using this provider, keeping prompts and hotkeys."""
        configurations = self.settings.dictation_prompt_configurations
        changed = False
        for key, configuration in configurations.items():
            if configuration.provider_id == provider_id:
                configuration.provider_id = ""
                configuration.model_name = ""
                configurations[key] = configuration
                changed = True
        if changed:
            self.settings.dictation_prompt_configurations = configurations

        if self.settings.rewrite_mode_selected_provider_id == provider_id:
            self.settings.rewrite_mode_selected_provider_id = ""
            self.settings.rewrite_mode_selected_model = None
        if self.settings.command_mode_selected_provider_id == provider_id:
            self.settings.command_mode_selected_provider_id = ""
            self.settings.command_mode_selected_model = None

    def delete_current_provider(self) -> bool:
        if self.is_fetching_models or self.is_testing_connection:
            return False
        if not self.selected_provider_id or self.selected_provider_id == PRIVATE_AI_PROVIDER_ID:
            return False

        deleted = self.selected_provider_id
        was_default = self.settings.selected_provider_id == deleted
        key = provider_key(deleted)

        previous_keys = dict(self.provider_api_keys)
        persisted_key = (
            self.managed_original_key
            if self.managed_original_key is not None
            else self.provider_api_key(deleted)
        )
        had_persisted_key = bool(persisted_key.strip())

        self.provider_api_keys.pop(key, None)
        if key != deleted:
            self.provider_api_keys.pop(deleted, None)
        # A keyless provider needs no secret-store write to disappear.
        if had_persisted_key and not self.persist_api_keys(dict(self.provider_api_keys)):
            self.provider_api_keys = previous_keys
            return False

        self.clear_provider_assignments(deleted)
        self._set_explicit_added_ids(self._explicit_added_ids() - {deleted})
        self.settings.saved_providers = [
            provider for provider in self.settings.saved_providers if provider.id != deleted
        ]

        available = self.settings.available_models_by_provider
        available.pop(key, None)
        self.settings.available_models_by_provider = available

        selected = self.settings.selected_model_by_provider
        selected.pop(key, None)
        self.settings.selected_model_by_provider = selected

        fingerprints = self.settings.verified_provider_fingerprints
        if fingerprints.pop(key, None) is not None:
            self.settings.verified_provider_fingerprints = fingerprints

        if was_default:
            # Removing the default leaves no provider rather than guessing one.
            self.settings.selected_provider_id = ""
            self.settings.selected_model = None

        self.refresh_provider_items()
        return True

    # --- closing the editor -----------------------------------------------

    def save_managed_provider_before_closing(self, provider_id: str) -> bool:
        if self.is_fetching_models or self.is_testing_connection:
            return False
        return self.save_managed_provider_api_key_if_needed(provider_id)

    def save_managed_provider_api_key_if_needed(self, provider_id: str) -> bool:
        """Persist only an actual key edit in the open editor.

        Selecting an already-configured or keyless provider must not depend
        on having write access to the secret store.
        """
        if self.selected_provider_id != provider_id or self.managed_original_key is None:
            return True
        current = self.provider_api_key(provider_id)
        if self.managed_original_key.strip() == current.strip():
            return True
        if provider_id not in self.provider_api_keys:
            return True
        if not self.persist_api_keys(dict(self.provider_api_keys)):
            return False
        self.managed_original_key = current
        return True
