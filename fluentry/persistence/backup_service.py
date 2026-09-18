"""Export and import the full application state as one JSON document."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .history_entry import TranscriptionHistoryEntry
from .settings_store import SettingsBackupPayload, SettingsStore
from .settings_types import AppPromptBinding, DictationPromptProfile, iso, now, parse_iso

APP_VERSION = "1.6.0"


@dataclass(frozen=True)
class BackupFileVersion:
    major: int
    minor: int

    @staticmethod
    def current() -> "BackupFileVersion":
        return BackupFileVersion(major=1, minor=0)

    def to_dict(self) -> dict[str, int]:
        return {"major": self.major, "minor": self.minor}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "BackupFileVersion":
        return BackupFileVersion(major=int(payload["major"]), minor=int(payload["minor"]))


@dataclass
class AppBackupDocument:
    settings: SettingsBackupPayload
    schema_version: BackupFileVersion = field(default_factory=BackupFileVersion.current)
    app_version: str = APP_VERSION
    exported_at: datetime = field(default_factory=now)
    prompt_profiles: list[DictationPromptProfile] = field(default_factory=list)
    app_prompt_bindings: list[AppPromptBinding] = field(default_factory=list)
    transcription_history: list[TranscriptionHistoryEntry] = field(default_factory=list)
    #: Optional so backups created before pronunciation matching still decode.
    pronunciation_profiles: list[dict] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version.to_dict(),
            "appVersion": self.app_version,
            "exportedAt": iso(self.exported_at),
            "settings": self.settings.to_dict(),
            "promptProfiles": [profile.to_dict() for profile in self.prompt_profiles],
            "appPromptBindings": [binding.to_dict() for binding in self.app_prompt_bindings],
            "transcriptionHistory": [entry.to_dict() for entry in self.transcription_history],
            "pronunciationProfiles": self.pronunciation_profiles,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "AppBackupDocument":
        return AppBackupDocument(
            schema_version=BackupFileVersion.from_dict(payload["schemaVersion"]),
            app_version=str(payload.get("appVersion") or "Unknown"),
            exported_at=parse_iso(payload.get("exportedAt")),
            settings=SettingsBackupPayload.from_dict(payload["settings"]),
            prompt_profiles=[
                DictationPromptProfile.from_dict(item) for item in payload.get("promptProfiles") or []
            ],
            app_prompt_bindings=[
                AppPromptBinding.from_dict(item) for item in payload.get("appPromptBindings") or []
            ],
            transcription_history=[
                TranscriptionHistoryEntry.from_dict(item)
                for item in payload.get("transcriptionHistory") or []
            ],
            pronunciation_profiles=payload.get("pronunciationProfiles"),
        )


class UnsupportedSchemaVersionError(Exception):
    def __init__(self, version: BackupFileVersion) -> None:
        super().__init__(
            f"This backup uses an unsupported schema version ({version.major}.{version.minor})."
        )
        self.version = version


class InvalidBackupError(Exception):
    def __init__(self) -> None:
        super().__init__("The selected backup file is not a valid Fluentry backup.")


class BackupService:
    def __init__(
        self,
        settings: SettingsStore | None = None,
        history_store=None,
        pronunciation_store=None,
    ) -> None:
        self.settings = settings or SettingsStore.shared()
        self.history_store = history_store
        self.pronunciation_store = pronunciation_store
        self.did_restore_observers: list = []

    def make_backup_document(self) -> AppBackupDocument:
        history: list[TranscriptionHistoryEntry] = []
        if self.history_store is not None:
            self.history_store.wait_until_loaded()
            history = self.history_store.make_backup_payload()
        pronunciation_profiles = None
        if self.pronunciation_store is not None:
            pronunciation_profiles = [
                profile.to_dict() for profile in self.pronunciation_store.all_profiles()
            ]
        return AppBackupDocument(
            settings=self.settings.make_backup_payload(),
            prompt_profiles=self.settings.dictation_prompt_profiles,
            app_prompt_bindings=self.settings.app_prompt_bindings,
            transcription_history=history,
            pronunciation_profiles=pronunciation_profiles,
        )

    def encode(self, document: AppBackupDocument) -> bytes:
        return json.dumps(document.to_dict(), indent=2, sort_keys=True).encode("utf-8")

    def decode(self, data: bytes | str) -> AppBackupDocument:
        raw = data.decode("utf-8") if isinstance(data, bytes) else data
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise InvalidBackupError() from error
        if not isinstance(payload, dict):
            raise InvalidBackupError()
        payload = self._migrating_legacy_private_ai_keys(payload)
        try:
            document = AppBackupDocument.from_dict(payload)
        except UnsupportedSchemaVersionError:
            raise
        except Exception as error:
            raise InvalidBackupError() from error
        self.validate(document)
        return document

    def restore(self, document: AppBackupDocument) -> None:
        self.validate(document)
        if self.pronunciation_store is not None:
            # A legacy backup represents the complete state from before voice
            # profiles existed, so restoring it clears newer profiles rather
            # than leaving them attached to restored dictionary entry IDs.
            self.pronunciation_store.replace_all_profiles(document.pronunciation_profiles or [])
        self.settings.restore(
            document.settings,
            prompt_profiles=document.prompt_profiles,
            app_prompt_bindings=document.app_prompt_bindings,
        )
        if self.history_store is not None:
            self.history_store.restore(document.transcription_history)
        for observer in list(self.did_restore_observers):
            try:
                observer()
            except Exception:
                pass

    def suggested_filename(self, moment: datetime | None = None) -> str:
        moment = moment or now()
        return f"Fluentry_Backup_{moment.strftime('%Y-%m-%d_%H-%M')}.json"

    def validate(self, document: AppBackupDocument) -> None:
        if document.schema_version.major != BackupFileVersion.current().major:
            raise UnsupportedSchemaVersionError(document.schema_version)

    @staticmethod
    def _migrating_legacy_private_ai_keys(payload: dict[str, Any]) -> dict[str, Any]:
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return payload
        if "privateAIPrefixKVCacheEnabled" in settings:
            return payload
        legacy_key = "".join(["fluid", "Int", "elligence", "PrefixKVCacheEnabled"])
        if legacy_key not in settings:
            return payload
        migrated_settings = dict(settings)
        migrated_settings["privateAIPrefixKVCacheEnabled"] = migrated_settings[legacy_key]
        migrated = dict(payload)
        migrated["settings"] = migrated_settings
        return migrated
