"""Navigation state shared by the main window and the settings sheet.

A port of `AppNavigationState`. The interesting rule is the return
destination: opening Settings from History and closing it must land back on
History, and switching between settings sections must not overwrite where the
user came from.

Icon names are freedesktop icon-theme names instead of SF Symbols, so they
resolve on GNOME, KDE and anything else following the icon-naming spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SidebarItem(str, Enum):
    WELCOME = "welcome"
    VOICE_ENGINE = "voiceEngine"
    AI_ENHANCEMENTS = "aiEnhancements"
    CLEANUP_STYLES = "cleanupStyles"
    MEETING_TOOLS = "meetingTools"
    CUSTOM_DICTIONARY = "customDictionary"
    STATS = "stats"
    HISTORY = "history"
    CHANGELOG = "changelog"
    FEEDBACK = "feedback"
    COMMAND_MODE = "commandMode"
    REWRITE_MODE = "rewriteMode"

    @property
    def title(self) -> str:
        return {
            SidebarItem.WELCOME: "Welcome",
            SidebarItem.VOICE_ENGINE: "Voice Engine",
            SidebarItem.AI_ENHANCEMENTS: "AI Enhancement",
            SidebarItem.CLEANUP_STYLES: "Cleanup Styles",
            SidebarItem.MEETING_TOOLS: "Meeting Tools",
            SidebarItem.CUSTOM_DICTIONARY: "Custom Dictionary",
            SidebarItem.STATS: "Stats",
            SidebarItem.HISTORY: "History",
            SidebarItem.CHANGELOG: "Changelog",
            SidebarItem.FEEDBACK: "Feedback",
            SidebarItem.COMMAND_MODE: "Command Mode",
            SidebarItem.REWRITE_MODE: "Edit Mode",
        }[self]

    @property
    def icon_name(self) -> str:
        return self.icon_names[0]

    @property
    def icon_names(self) -> tuple[str, ...]:
        """Preferred icon first, then names a sparser theme is likelier to have."""
        return {
            SidebarItem.WELCOME: ("go-home", "user-home"),
            SidebarItem.VOICE_ENGINE: ("audio-input-microphone", "audio-card"),
            SidebarItem.AI_ENHANCEMENTS: ("applications-science", "starred"),
            SidebarItem.CLEANUP_STYLES: ("format-text-bold", "format-justify-left"),
            SidebarItem.MEETING_TOOLS: ("system-users", "user-available"),
            SidebarItem.CUSTOM_DICTIONARY: ("accessories-dictionary", "view-list"),
            SidebarItem.STATS: ("view-grid", "office-chart-bar"),
            SidebarItem.HISTORY: ("document-open-recent", "document-properties"),
            SidebarItem.CHANGELOG: ("text-x-changelog", "text-x-generic"),
            SidebarItem.FEEDBACK: ("mail-send", "mail-message-new"),
            SidebarItem.COMMAND_MODE: ("utilities-terminal", "system-run"),
            SidebarItem.REWRITE_MODE: ("document-edit", "accessories-text-editor"),
        }[self]


class SettingsSection(str, Enum):
    GENERAL = "general"
    DICTATION = "dictation"
    NOTIFICATIONS = "notifications"
    AUDIO = "audio"
    OVERLAY = "overlay"
    DATA_AND_DIAGNOSTICS = "dataAndDiagnostics"
    EXPERIMENTAL = "experimental"

    @property
    def title(self) -> str:
        return {
            SettingsSection.GENERAL: "General",
            SettingsSection.DICTATION: "Dictation",
            SettingsSection.NOTIFICATIONS: "Notifications",
            SettingsSection.AUDIO: "Audio",
            SettingsSection.OVERLAY: "Overlay",
            SettingsSection.DATA_AND_DIAGNOSTICS: "Data & Diagnostics",
            SettingsSection.EXPERIMENTAL: "Experimental",
        }[self]

    @property
    def icon_name(self) -> str:
        return self.icon_names[0]

    @property
    def icon_names(self) -> tuple[str, ...]:
        return {
            SettingsSection.GENERAL: ("preferences-system", "emblem-system"),
            SettingsSection.DICTATION: ("input-keyboard", "preferences-desktop-keyboard"),
            SettingsSection.NOTIFICATIONS: (
                "preferences-system-notifications",
                "preferences-desktop-notification",
            ),
            SettingsSection.AUDIO: ("audio-volume-high", "audio-speakers"),
            SettingsSection.OVERLAY: ("video-display", "preferences-desktop-display"),
            SettingsSection.DATA_AND_DIAGNOSTICS: ("drive-harddisk", "applications-utilities"),
            SettingsSection.EXPERIMENTAL: ("applications-engineering", "system-run"),
        }[self]


@dataclass
class SettingsNavigationState:
    selected_section: SettingsSection | None = None
    return_destination: SidebarItem = SidebarItem.WELCOME

    @property
    def is_presented(self) -> bool:
        return self.selected_section is not None

    def is_leaving(self, section: SettingsSection, destination: SettingsSection | None) -> bool:
        return self.selected_section is section and destination is not section

    def present(
        self, section: SettingsSection, returning_to: SidebarItem | None = None
    ) -> None:
        # Only the *first* presentation records where to go back to; moving
        # between sections must not overwrite it.
        if not self.is_presented:
            self.return_destination = returning_to or SidebarItem.WELCOME
        self.selected_section = section

    def dismiss(self) -> SidebarItem:
        self.selected_section = None
        return self.return_destination

    def leave_for_app(self) -> None:
        """Closing Settings by navigating the app itself keeps no destination."""
        self.selected_section = None
