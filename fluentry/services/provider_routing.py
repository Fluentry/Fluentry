"""Which AI provider, model and key a dictation actually uses.

A port of `DictationProviderRoute`, `DictationAIPostProcessingGate`,
`DictationDefaultProvider` and `ProviderModelVerificationStore`.

Resolution has to survive a lot of overlapping settings — a global prompt
selection, per-profile provider overrides, per-app bindings, and the Private
AI provider — so it is kept in one place and returns a single resolved
`ProviderRoute`. The gate then answers one question: may AI post-processing
run right now?

The gate deliberately fails closed. An unverified provider, a missing key on
a remote endpoint, or a provider whose base URL changed since verification
all mean "no", because the alternative is quietly shipping the user's
dictation to an endpoint they did not approve.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..persistence.settings_store import (
    BUILT_IN_PROVIDER_IDS,
    PRIVATE_AI_PROVIDER_ID,
    SettingsStore,
)
from ..persistence.settings_types import (
    DictationPromptConfiguration,
    DictationPromptSelection,
    DictationShortcutSlot,
    PromptMode,
    PromptRoutingScope,
)

CUSTOM_PROVIDER_PREFIX = "custom:"


def provider_key(provider_id: str) -> str:
    """The key a provider's model, API key and verification are filed under.

    Built-in providers use their bare id; everything else is namespaced, so a
    user-added provider called "openai" cannot shadow the real one.
    """
    trimmed = provider_id.strip()
    if not trimmed:
        return ""
    if trimmed in BUILT_IN_PROVIDER_IDS:
        return trimmed
    if trimmed.startswith(CUSTOM_PROVIDER_PREFIX):
        return trimmed
    return f"{CUSTOM_PROVIDER_PREFIX}{trimmed}"


def default_base_url(provider_id: str) -> str:
    """The published endpoint for a built-in provider.

    An unknown provider returns "", which fails closed rather than silently
    being treated as OpenAI.
    """
    from ..ui.pages import BUILT_IN_PROVIDERS

    trimmed = provider_id.strip()
    for identifier, _label, base_url in BUILT_IN_PROVIDERS:
        if identifier == trimmed:
            return base_url
    return ""


def default_models(provider_id: str) -> list[str]:
    """Models to offer before the provider has been asked.

    The macOS build shipped a curated list per provider. Nothing here
    hard-codes model names that go stale, so the list is empty and the
    Voice Engine screen fetches the real one from the provider.
    """
    return []


def is_local_endpoint(url: str) -> bool:
    """Whether this endpoint is on the machine or the local network.

    A local endpoint may legitimately have no API key; a remote one may not.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def provider_fingerprint(base_url: str, api_key: str) -> str | None:
    """Identifies the endpoint-and-credential pair that was verified.

    Only a hash is stored, never the key itself.
    """
    trimmed_base = base_url.strip()
    if not trimmed_base:
        return None
    payload = f"{trimmed_base}|{api_key.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def private_ai_verification_fingerprint(model_id: str, settings: SettingsStore) -> str:
    """The Private AI provider verifies a model, not an endpoint.

    The backend preference is part of it: a model verified under llama.cpp
    has not been verified under ONNX.
    """
    preference = settings.private_ai_backend_preference
    normalized = preference.system_default() if preference.value == "auto" else preference
    return f"private-ai-provider|{model_id}|backend:{normalized.value}"


# --- the resolved route -----------------------------------------------------


@dataclass(frozen=True)
class ProviderRoute:
    provider_id: str = ""
    provider_key: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""

    @property
    def uses_private_ai(self) -> bool:
        return PRIVATE_AI_PROVIDER_ID in (
            self.provider_id,
            self.provider_key,
            self.provider_key.removeprefix(CUSTOM_PROVIDER_PREFIX)
            if self.provider_key.startswith(CUSTOM_PROVIDER_PREFIX)
            else self.provider_key,
        )

    @property
    def is_empty(self) -> bool:
        return not self.provider_id.strip()


EMPTY_ROUTE = ProviderRoute()


def private_ai_verified_model_id(settings: SettingsStore, runtime=None) -> str | None:
    """The Private AI model that is both installed and verified, if any."""
    runtime = runtime or PrivateAIRuntime()
    if not runtime.is_available:
        return None
    key = provider_key(PRIVATE_AI_PROVIDER_ID)
    model_id = settings.selected_model_by_provider.get(key) or runtime.default_model_id
    model_id = runtime.canonical_model_id(model_id) or model_id
    if not model_id or not runtime.is_model_installed(model_id):
        return None
    expected = private_ai_verification_fingerprint(model_id, settings)
    if (
        settings.verified_private_ai_model_fingerprints.get(model_id) == expected
        or settings.verified_provider_fingerprints.get(key) == expected
    ):
        return model_id
    return None


@dataclass(frozen=True)
class PrivateAIRuntime:
    """Stands in for the local-model runtime.

    The shipped build has none — the macOS app resolved this through a
    private framework that is not part of the source tree — so the default
    reports unavailable and every Private AI route resolves to nothing.
    """

    is_available: bool = False
    default_model_id: str = ""
    installed_model_ids: frozenset[str] = frozenset()

    def canonical_model_id(self, value: str) -> str | None:
        trimmed = (value or "").strip()
        return trimmed if trimmed in self.installed_model_ids else None

    def is_model_installed(self, model_id: str) -> bool:
        return model_id in self.installed_model_ids


def private_ai_route(settings: SettingsStore, runtime=None) -> ProviderRoute:
    model_id = private_ai_verified_model_id(settings, runtime)
    if model_id is None:
        return EMPTY_ROUTE
    return ProviderRoute(
        provider_id=PRIVATE_AI_PROVIDER_ID,
        provider_key=PRIVATE_AI_PROVIDER_ID,
        base_url=default_base_url(PRIVATE_AI_PROVIDER_ID),
        model=model_id,
        api_key="",
    )


def external_fallback_provider_id(provider_id: str) -> str:
    """Private AI is never an accidental fallback: it has to be chosen."""
    trimmed = provider_id.strip()
    return "" if trimmed == PRIVATE_AI_PROVIDER_ID else trimmed


def should_use_legacy_private_ai_route(
    selected_provider_id: str, configured_provider_id: str, configured_model: str
) -> bool:
    """Before per-prompt providers existed, Private AI was just the selection."""
    return (
        selected_provider_id == PRIVATE_AI_PROVIDER_ID
        and not configured_provider_id
        and not configured_model
    )


def allows_private_ai_route(selection: DictationPromptSelection, selected_provider_id: str) -> bool:
    return selection.kind == "privateAI" or (
        selection.kind == "default" and selected_provider_id == PRIVATE_AI_PROVIDER_ID
    )


def effective_prompt_selection(
    settings: SettingsStore,
    dictation_slot: DictationShortcutSlot,
    app_bundle_id: str | None,
) -> DictationPromptSelection:
    """The selection after per-app bindings have had their say."""
    selection = settings.dictation_prompt_selection
    if selection.kind == "off":
        return selection

    uses_only_app_bindings = (
        settings.prompt_routing_scope(PromptMode.DICTATE) is PromptRoutingScope.SELECTED_APPS_ONLY
    )
    supports_override = SettingsStore.dictation_selection_supports_app_override(selection)
    if not uses_only_app_bindings and not supports_override:
        return selection

    binding = settings.app_prompt_binding(PromptMode.DICTATE, app_bundle_id)
    if binding is None:
        # "Selected apps only" means silence in every app that has no binding.
        return DictationPromptSelection.OFF if uses_only_app_bindings else selection

    prompt_id = binding.prompt_id
    if not prompt_id or not any(
        profile.id == prompt_id and profile.mode.normalized is PromptMode.DICTATE
        for profile in settings.dictation_prompt_profiles
    ):
        return DictationPromptSelection.DEFAULT
    return DictationPromptSelection.profile(prompt_id)


def resolve_route(
    settings: SettingsStore,
    dictation_slot: DictationShortcutSlot | None = None,
    app_bundle_id: str | None = None,
    api_keys: dict[str, str] | None = None,
    runtime=None,
) -> ProviderRoute:
    api_keys = api_keys or {}
    configured_model: str | None = None

    if dictation_slot is not None:
        selection = effective_prompt_selection(settings, dictation_slot, app_bundle_id)
        if selection.kind == "off":
            return EMPTY_ROUTE
        if selection.kind == "privateAI":
            return private_ai_route(settings, runtime)

        configuration = settings.dictation_prompt_configuration(selection)
        provider_id = configuration.provider_id.strip()
        model = configuration.model_name.strip()
        if provider_id and model:
            selected_provider_id = provider_id
            configured_model = model
        else:
            has_app_binding = (
                settings.app_prompt_binding(PromptMode.DICTATE, app_bundle_id) is not None
            )
            if (
                selection.kind == "default"
                and not has_app_binding
                and should_use_legacy_private_ai_route(
                    settings.selected_provider_id, provider_id, model
                )
            ):
                return private_ai_route(settings, runtime)
            selected_provider_id = external_fallback_provider_id(settings.selected_provider_id)
    else:
        selected_provider_id = settings.selected_provider_id

    selected_models = settings.selected_model_by_provider

    for saved in settings.saved_providers:
        if saved.id == selected_provider_id:
            key = f"{CUSTOM_PROVIDER_PREFIX}{saved.id}"
            return ProviderRoute(
                provider_id=selected_provider_id,
                provider_key=key,
                base_url=saved.base_url,
                model=configured_model
                or selected_models.get(key)
                or (saved.models[0] if saved.models else ""),
                api_key=api_keys.get(key) or api_keys.get(selected_provider_id) or "",
            )

    if selected_provider_id in BUILT_IN_PROVIDER_IDS:
        return ProviderRoute(
            provider_id=selected_provider_id,
            provider_key=selected_provider_id,
            base_url=default_base_url(selected_provider_id),
            model=configured_model
            or selected_models.get(selected_provider_id)
            or (default_models(selected_provider_id) or [""])[0],
            api_key=api_keys.get(selected_provider_id) or "",
        )

    return ProviderRoute(
        provider_id=selected_provider_id,
        provider_key=selected_provider_id,
        base_url="",
        model=configured_model or selected_models.get(selected_provider_id) or "",
        api_key=api_keys.get(selected_provider_id) or "",
    )


def resolve_route_for_provider(
    settings: SettingsStore,
    provider_id: str,
    model: str,
    api_keys: dict[str, str] | None = None,
) -> ProviderRoute:
    api_keys = api_keys or {}
    trimmed_provider = provider_id.strip()
    trimmed_model = model.strip()
    for saved in settings.saved_providers:
        if saved.id == trimmed_provider:
            key = f"{CUSTOM_PROVIDER_PREFIX}{saved.id}"
            return ProviderRoute(
                provider_id=trimmed_provider,
                provider_key=key,
                base_url=saved.base_url,
                model=trimmed_model,
                api_key=api_keys.get(key) or api_keys.get(trimmed_provider) or "",
            )
    return ProviderRoute(
        provider_id=trimmed_provider,
        provider_key=trimmed_provider,
        base_url=default_base_url(trimmed_provider),
        model=trimmed_model,
        api_key=api_keys.get(trimmed_provider) or "",
    )


def resolve_route_for_post_processing(
    settings: SettingsStore,
    dictation_slot: DictationShortcutSlot = DictationShortcutSlot.PRIMARY,
    api_keys: dict[str, str] | None = None,
    runtime=None,
) -> ProviderRoute:
    if settings.dictation_prompt_selection.kind == "privateAI":
        return private_ai_route(settings, runtime)
    if settings.prompt_routing_scope(PromptMode.DICTATE) is PromptRoutingScope.SELECTED_APPS_ONLY:
        # Post-processing has no focused app to bind against, so it uses the
        # global provider rather than resolving to "off".
        return resolve_route(settings, api_keys=api_keys, runtime=runtime)
    return resolve_route(
        settings, dictation_slot=dictation_slot, api_keys=api_keys, runtime=runtime
    )


# --- the gate ---------------------------------------------------------------


def route_is_configured(
    route: ProviderRoute, settings: SettingsStore, runtime=None
) -> bool:
    if route.uses_private_ai:
        return private_ai_verified_model_id(settings, runtime) is not None

    provider_id = route.provider_id.strip()
    model = route.model.strip()
    if not provider_id or not model:
        return False

    stored = settings.verified_provider_fingerprints.get(route.provider_key)
    if stored is None:
        return False

    base_url = route.base_url.strip()
    api_key = route.api_key.strip()
    # A remote endpoint without a key cannot work, whatever was verified.
    if not is_local_endpoint(base_url) and not api_key:
        return False
    return provider_fingerprint(base_url, api_key) == stored


def ai_post_processing_is_configured(
    settings: SettingsStore,
    dictation_slot: DictationShortcutSlot = DictationShortcutSlot.PRIMARY,
    app_bundle_id: str | None = None,
    api_keys: dict[str, str] | None = None,
    runtime=None,
) -> bool:
    """Whether AI post-processing may run for this dictation."""
    if settings.dictation_prompt_selection.kind == "off":
        return False
    if (
        app_bundle_id is not None
        and settings.prompt_routing_scope(PromptMode.DICTATE)
        is PromptRoutingScope.SELECTED_APPS_ONLY
        and not settings.has_app_prompt_binding(PromptMode.DICTATE, app_bundle_id)
    ):
        return False

    route = resolve_route(
        settings,
        dictation_slot=dictation_slot,
        app_bundle_id=app_bundle_id,
        api_keys=api_keys,
        runtime=runtime,
    )
    return route_is_configured(route, settings, runtime)


def provider_is_configured(
    settings: SettingsStore, api_keys: dict[str, str] | None = None, runtime=None
) -> bool:
    """Whether the selected provider is usable, ignoring the prompt selection.

    This is what gates the prompt-mode shortcut, which runs AI regardless of
    whether dictation post-processing is switched on.
    """
    route = resolve_route(settings, api_keys=api_keys, runtime=runtime)
    return route_is_configured(route, settings, runtime)


# --- the main shortcut's provider ------------------------------------------


def setup_issue(
    requires_api_key: bool,
    has_api_key: bool,
    has_model: bool,
    is_verified: bool,
    verification_failed: bool,
) -> str | None:
    """What is still missing, phrased for the settings row.

    Note what is *not* here: never having run a test is fine. Only missing
    setup, or a test that actually failed, is worth reporting.
    """
    if requires_api_key and not has_api_key:
        return "API key missing"
    if not has_model:
        return "Choose a model"
    if verification_failed:
        return "Verification failed"
    return None


def dictation_provider_id(
    selection: DictationPromptSelection,
    configuration: DictationPromptConfiguration,
    selected_provider_id: str,
    private_provider_id: str = PRIVATE_AI_PROVIDER_ID,
) -> str:
    """Names the provider the main shortcut uses, without reading credentials."""
    if selection.kind == "off":
        return ""
    if selection.kind == "privateAI":
        return private_provider_id

    provider_id = configuration.provider_id.strip()
    model = configuration.model_name.strip()
    if provider_id and model:
        return provider_id
    if (
        selection.kind == "default"
        and selected_provider_id == private_provider_id
        and not provider_id
        and not model
    ):
        return private_provider_id
    fallback = selected_provider_id.strip()
    return "" if fallback == private_provider_id else fallback


# --- optional model verifications -------------------------------------------


@dataclass
class ProviderModelVerificationStore:
    """Remembers which provider/model combinations passed a test.

    Only a hash and a timestamp are stored — never the API key, the URL or
    anything from the request.
    """

    DEFAULTS_KEY = "ProviderModelVerificationsV1"
    MAXIMUM_ENTRIES = 256

    defaults: object
    _successes: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        stored = self.defaults.dictionary(self.DEFAULTS_KEY) or {}
        usable = {
            key: float(value)
            for key, value in stored.items()
            if isinstance(key, str) and len(key) == 64 and isinstance(value, (int, float))
        }
        newest = sorted(usable.items(), key=lambda item: item[1], reverse=True)
        self._successes = dict(newest[: self.MAXIMUM_ENTRIES])

    @staticmethod
    def identity(provider_id: str, base_url: str, api_key: str, model: str) -> str:
        fields = [
            value.strip() for value in (provider_id, base_url, api_key, model)
        ]
        payload = json.dumps(fields, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def contains(self, identity: str) -> bool:
        return identity in self._successes

    def record_success(self, identity: str, now: float | None = None) -> None:
        self._successes[identity] = now if now is not None else time.time()
        while len(self._successes) > self.MAXIMUM_ENTRIES:
            oldest = min(self._successes, key=lambda key: self._successes[key])
            del self._successes[oldest]
        self._persist()

    def remove(self, identity: str) -> None:
        if self._successes.pop(identity, None) is None:
            return
        self._persist()

    def _persist(self) -> None:
        self.defaults.set(self.DEFAULTS_KEY, dict(self._successes))
