"""The settings window.

A port of `SettingsView`'s sections. Each section is a page of cards bound
directly to `SettingsStore`, so a change takes effect immediately — there is
no apply button and no separate draft state to get out of sync.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import language_choices, tr
from ..models.hotkey import HotkeyShortcut
from ..models.keycodes import MODIFIER_KEY_FLAGS, ModifierFlags
from ..persistence.settings_store import HotkeyActivationMode
from ..persistence.settings_types import (
    AccentColorOption,
    OverlayPosition,
    OverlaySize,
    SpokenSendKey,
    HistoryAutoClearInterval,
    TextInsertionMode,
    ThemePreference,
    TranscriptionStartSound,
)
from .icons import themed_icon
from .navigation import SettingsSection
from .widgets import (
    Card,
    HeaderBar,
    ToggleRow,
    button,
    combo,
    hint_label,
    page,
    primary_button,
    scrollable,
)

SECTIONS = list(SettingsSection)


class ShortcutRecorder(QPushButton):
    """Click, then press the shortcut you want.

    Modifier-only shortcuts are first-class: tapping Right Alt on its own is
    a valid dictation hotkey, which is why a bare modifier press is recorded
    rather than ignored.
    """

    def __init__(self, shortcut: HotkeyShortcut, on_change) -> None:
        super().__init__()
        self._shortcut = shortcut
        self._on_change = on_change
        self._recording = False
        self.setCheckable(True)
        self.clicked.connect(self._toggle)
        self._update_text()

    def _toggle(self) -> None:
        self._recording = self.isChecked()
        self.setText(tr("Press a shortcut…") if self._recording else self._shortcut.display_string)
        if self._recording:
            self.grabKeyboard()
        else:
            self.releaseKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt naming
        if not self._recording:
            super().keyPressEvent(event)
            return
        key_code = event.nativeScanCode() - 8  # X11 keycode → evdev code
        if key_code <= 0:
            return
        modifiers = ModifierFlags.NONE
        qt_modifiers = event.modifiers()
        if qt_modifiers & Qt.KeyboardModifier.ControlModifier:
            modifiers = modifiers.union(ModifierFlags.CONTROL)
        if qt_modifiers & Qt.KeyboardModifier.ShiftModifier:
            modifiers = modifiers.union(ModifierFlags.SHIFT)
        if qt_modifiers & Qt.KeyboardModifier.AltModifier:
            modifiers = modifiers.union(ModifierFlags.ALT)
        if qt_modifiers & Qt.KeyboardModifier.MetaModifier:
            modifiers = modifiers.union(ModifierFlags.SUPER)

        if key_code in MODIFIER_KEY_FLAGS:
            shortcut = HotkeyShortcut.keyboard(key_code, ModifierFlags.NONE, [key_code])
        else:
            shortcut = HotkeyShortcut.keyboard(key_code, modifiers)

        self._shortcut = shortcut
        self._recording = False
        self.setChecked(False)
        self.releaseKeyboard()
        self._update_text()
        self._on_change(shortcut)

    def _update_text(self) -> None:
        self.setText(self._shortcut.display_string)


class SettingsWindow(QDialog):
    def __init__(self, app_state, palette, parent=None) -> None:
        super().__init__(parent)
        self._app = app_state
        self._palette = palette
        #: Set by the application: relaunch in place so a new interface
        #: language takes effect. None falls back to just storing the choice.
        self.on_request_restart = None
        self.setWindowTitle(tr("Fluentry Settings"))
        self.resize(820, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = HeaderBar(tr("Settings"))
        outer.addWidget(self.header)

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        outer.addWidget(body, 1)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(190)
        self.sidebar.setIconSize(QSize(16, 16))
        for section in SECTIONS:
            item = QListWidgetItem(themed_icon(*section.icon_names), section.title)
            item.setData(Qt.ItemDataRole.UserRole, section.value)
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self._section_changed)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self._builders = {
            SettingsSection.GENERAL: self._build_general,
            SettingsSection.DICTATION: self._build_dictation,
            SettingsSection.NOTIFICATIONS: self._build_notifications,
            SettingsSection.AUDIO: self._build_audio,
            SettingsSection.OVERLAY: self._build_overlay,
            SettingsSection.DATA_AND_DIAGNOSTICS: self._build_data,
            SettingsSection.EXPERIMENTAL: self._build_experimental,
        }
        for section in SECTIONS:
            self.stack.addWidget(self._builders[section]())
        layout.addWidget(self.stack, 1)

        self.sidebar.setCurrentRow(0)

    def show_section(self, section: SettingsSection) -> None:
        self.sidebar.setCurrentRow(SECTIONS.index(section))
        self.show()
        self.raise_()
        self.activateWindow()

    def _set_history_auto_clear(self, interval: HistoryAutoClearInterval) -> None:
        self._app.settings.history_auto_clear_interval = interval
        self.history_clearing_hint.setText(tr(interval.description))
        # Apply it now rather than at the next launch.
        self._app.prune_expired_history()

    def _section_changed(self, row: int) -> None:
        if 0 <= row < len(SECTIONS):
            self.header.set_title(SECTIONS[row].title)
        if 0 <= row < len(SECTIONS):
            self.stack.setCurrentIndex(row)

    # --- sections ---------------------------------------------------------

    def _build_general(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("General"))

        appearance = Card(tr("Appearance"))
        theme = combo(
            [(tr(option.display_name), option.value) for option in ThemePreference],
            settings.theme_preference.value,
        )
        theme.currentIndexChanged.connect(
            lambda: self._app.set_theme(ThemePreference(theme.currentData()))
        )
        appearance.add_labelled(tr("Theme"), theme)

        accent = combo(
            [(option.value, option.value) for option in AccentColorOption],
            settings.accent_color_option.value,
        )
        accent.currentIndexChanged.connect(
            lambda: self._app.set_accent(AccentColorOption(accent.currentData()))
        )
        appearance.add_labelled(tr("Accent colour"), accent)

        language = combo(
            [(tr("Match the system language"), "system")]
            + [(name, code) for code, name in language_choices()],
            settings.ui_language,
        )
        language.currentIndexChanged.connect(
            lambda: self._change_language(language.currentData())
        )
        appearance.add_labelled(tr("Language"), language)
        appearance.add(
            hint_label(tr("Fluentry restarts to apply a new interface language."))
        )
        layout.addWidget(appearance)

        startup = Card(tr("Startup"))
        launch = ToggleRow(
            tr("Start Fluentry at login"),
            tr("Adds a desktop autostart entry under ~/.config/autostart."),
            settings.launch_at_startup,
        )
        launch.toggled.connect(self._app.set_launch_at_startup)
        startup.add(launch)

        show_window = ToggleRow(
            tr("Open the main window at login"),
            tr("Leave this off to start quietly in the tray."),
            settings.show_main_window_at_login_launch,
        )
        show_window.toggled.connect(
            lambda value: setattr(settings, "show_main_window_at_login_launch", value)
        )
        startup.add(show_window)
        layout.addWidget(startup)

        layout.addStretch(1)
        return scrollable(container)

    def _change_language(self, code: str) -> None:
        if not code or code == self._app.settings.ui_language:
            return
        self._app.settings.ui_language = code
        # The interface is built once, so a live language swap would mean
        # rebuilding every window; a clean relaunch is simpler and is what
        # the app already does after a runtime install.
        answer = QMessageBox.question(
            self,
            tr("Change language"),
            tr("Fluentry needs to restart to change the interface language. Restart now?"),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        )
        if answer is QMessageBox.StandardButton.Ok and self.on_request_restart is not None:
            self.on_request_restart()

    def _build_dictation(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("Dictation"))

        shortcut_card = Card(
            tr("Shortcut"), tr("A bare modifier tap (for example Right Alt) works as a shortcut.")
        )
        recorder = ShortcutRecorder(settings.hotkey_shortcut, self._app.set_primary_shortcut)
        shortcut_card.add_labelled(tr("Dictation shortcut"), recorder)

        mode = combo(
            [
                (tr(HotkeyActivationMode.DISPLAY_NAMES[value]), value)
                for value in HotkeyActivationMode.ALL
            ],
            settings.hotkey_mode,
        )
        mode.currentIndexChanged.connect(
            lambda: self._app.set_hotkey_mode(mode.currentData())
        )
        shortcut_card.add_labelled(tr("Activation"), mode)
        shortcut_card.add(
            hint_label(tr(HotkeyActivationMode.DESCRIPTIONS[settings.hotkey_mode]))
        )
        layout.addWidget(shortcut_card)

        insertion = Card(tr("Text insertion"))
        insertion_mode = combo(
            [(tr(value.display_name), value.value) for value in TextInsertionMode],
            settings.text_insertion_mode.value,
        )
        insertion_mode.currentIndexChanged.connect(
            lambda: setattr(
                settings, "text_insertion_mode", TextInsertionMode(insertion_mode.currentData())
            )
        )
        insertion.add_labelled(tr("Mode"), insertion_mode)
        insertion.add(hint_label(tr(settings.text_insertion_mode.description)))

        copy_toggle = ToggleRow(
            tr("Also copy each dictation to the clipboard"),
            checked=settings.copy_transcription_to_clipboard,
        )
        copy_toggle.toggled.connect(
            lambda value: setattr(settings, "copy_transcription_to_clipboard", value)
        )
        insertion.add(copy_toggle)
        layout.addWidget(insertion)

        formatting = Card(tr("Formatting"))
        for title, hint, attribute in [
            (
                tr("Spoken punctuation"),
                tr('Say "literal comma" to type a comma.'),
                "auto_convert_punctuation_enabled",
            ),
            (
                tr("Slash commands and mentions"),
                tr('Turns "run slash deploy" into "/deploy" in supported apps.'),
                "literal_dictation_formatting_enabled",
            ),
            (tr("Remove filler words"), tr('Drops "um", "uh" and similar.'), "remove_filler_words_enabled"),
            (
                tr("Lowercase the first letter"),
                tr("Useful for search boxes and chat."),
                "gaav_lowercase_first_letter_enabled",
            ),
            (tr("Remove the trailing period"), None, "gaav_remove_trailing_period_enabled"),
            (
                tr("Continuous dictation spacing"),
                tr("Adds the spaces that make consecutive dictations read as one document."),
                "continuous_dictation_spacing_enabled",
            ),
            (
                tr("Context-aware capitalization"),
                tr("Capitalizes only when the previous text ended a sentence."),
                "context_aware_capitalization_enabled",
            ),
        ]:
            toggle = ToggleRow(title, hint, getattr(settings, attribute))
            toggle.toggled.connect(
                lambda value, name=attribute: setattr(settings, name, value)
            )
            formatting.add(toggle)

        prefix = QLineEdit(settings.punctuation_dictionary_prefix)
        prefix.editingFinished.connect(
            lambda: setattr(settings, "punctuation_dictionary_prefix", prefix.text())
        )
        formatting.add_labelled(tr("Punctuation prefix"), prefix)
        layout.addWidget(formatting)

        send = Card(tr("Spoken send"), tr("End a dictation with a phrase to submit it."))
        send_toggle = ToggleRow(tr("Enable spoken send"), checked=settings.spoken_send_enabled)
        send_toggle.toggled.connect(lambda value: setattr(settings, "spoken_send_enabled", value))
        send.add(send_toggle)

        phrase = QLineEdit(settings.spoken_send_phrase)
        phrase.editingFinished.connect(
            lambda: setattr(settings, "spoken_send_phrase", phrase.text())
        )
        send.add_labelled(tr("Phrase"), phrase)

        send_key = combo(
            [(tr(value.display_name), value.value) for value in SpokenSendKey],
            settings.spoken_send_key.value,
        )
        send_key.currentIndexChanged.connect(
            lambda: setattr(settings, "spoken_send_key", SpokenSendKey(send_key.currentData()))
        )
        send.add_labelled(tr("Send with"), send_key)
        layout.addWidget(send)

        layout.addStretch(1)
        return scrollable(container)

    def _build_notifications(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("Notifications"))

        card = Card(tr("Desktop notifications"))
        for title, hint, attribute in [
            (
                tr("Tell me when AI enhancement fails"),
                tr("Your transcript is still typed; this explains why it was not cleaned up."),
                "notify_ai_processing_failures",
            ),
            (
                tr("Tell me when the microphone changes"),
                tr("Shown when Fluentry switches to a different input device."),
                "show_microphone_change_alerts",
            ),
        ]:
            toggle = ToggleRow(title, hint, getattr(settings, attribute))
            toggle.toggled.connect(lambda value, name=attribute: setattr(settings, name, value))
            card.add(toggle)
        layout.addWidget(card)

        sounds = Card(tr("Sounds"))
        sound = combo(
            [(tr(value.display_name), value.value) for value in TranscriptionStartSound],
            settings.transcription_start_sound.value,
        )
        sound.currentIndexChanged.connect(
            lambda: setattr(
                settings,
                "transcription_start_sound",
                TranscriptionStartSound(sound.currentData()),
            )
        )
        sounds.add_labelled(tr("Start sound"), sound)

        volume = QDoubleSpinBox()
        volume.setRange(0.0, 1.0)
        volume.setSingleStep(0.05)
        volume.setValue(settings.transcription_sound_volume)
        volume.valueChanged.connect(
            lambda value: setattr(settings, "transcription_sound_volume", value)
        )
        sounds.add_labelled(tr("Volume"), volume)
        layout.addWidget(sounds)

        layout.addStretch(1)
        return scrollable(container)

    def _build_audio(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("Audio"))

        card = Card(tr("Capture"))
        card.add(hint_label(self._app.capture_backend_description()))

        threshold = QDoubleSpinBox()
        threshold.setRange(0.0, 0.95)
        threshold.setSingleStep(0.05)
        threshold.setValue(settings.visualizer_noise_threshold)
        threshold.valueChanged.connect(
            lambda value: setattr(settings, "visualizer_noise_threshold", value)
        )
        card.add_labelled(tr("Level meter noise floor"), threshold)

        skip_silence = ToggleRow(
            tr("Skip clearly silent recordings"),
            tr("Avoids running the model on an accidental short press."),
            settings.skip_silent_recordings_enabled,
        )
        skip_silence.toggled.connect(
            lambda value: setattr(settings, "skip_silent_recordings_enabled", value)
        )
        card.add(skip_silence)
        layout.addWidget(card)

        media = Card(tr("Other media"))
        pause_media = ToggleRow(
            tr("Pause playback while dictating"),
            tr("Uses MPRIS, and only resumes what Fluentry paused."),
            settings.pause_media_during_transcription,
        )
        pause_media.toggled.connect(
            lambda value: setattr(settings, "pause_media_during_transcription", value)
        )
        media.add(pause_media)
        media.add(hint_label(self._app.media_backend_description()))
        layout.addWidget(media)

        layout.addStretch(1)
        return scrollable(container)

    def _build_overlay(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("Overlay"))

        card = Card(tr("Recording overlay"))
        size = combo(
            [(tr(value.display_name), value.value) for value in OverlaySize],
            settings.overlay_size.value,
        )
        size.currentIndexChanged.connect(
            lambda: self._app.set_overlay_size(OverlaySize(size.currentData()))
        )
        card.add_labelled(tr("Size"), size)

        position = combo(
            [(tr(value.display_name), value.value) for value in OverlayPosition],
            settings.overlay_position.value,
        )
        position.currentIndexChanged.connect(
            lambda: self._app.set_overlay_position(OverlayPosition(position.currentData()))
        )
        card.add_labelled(tr("Position"), position)

        offset = QSpinBox()
        offset.setRange(10, 1000)
        offset.setValue(int(settings.overlay_bottom_offset))
        offset.valueChanged.connect(lambda value: self._app.set_overlay_offset(float(value)))
        card.add_labelled(tr("Distance from edge"), offset)

        preview = QSpinBox()
        preview.setRange(50, 800)
        preview.setSingleStep(50)
        preview.setValue(settings.transcription_preview_char_limit)
        preview.valueChanged.connect(
            lambda value: setattr(settings, "transcription_preview_char_limit", value)
        )
        card.add_labelled(tr("Preview characters"), preview)

        streaming = ToggleRow(
            tr("Show a live preview while speaking"), checked=settings.enable_streaming_preview
        )
        streaming.toggled.connect(
            lambda value: setattr(settings, "enable_streaming_preview", value)
        )
        card.add(streaming)
        layout.addWidget(card)

        layout.addStretch(1)
        return scrollable(container)

    def _build_data(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(tr("Data & Diagnostics"))

        history = Card(tr("History"))
        save_history = ToggleRow(
            tr("Keep a history of my dictations"),
            tr("Stored only on this machine, in an SQLite database."),
            settings.save_transcription_history,
        )
        save_history.toggled.connect(
            lambda value: setattr(settings, "save_transcription_history", value)
        )
        history.add(save_history)

        clearing = combo(
            [(tr(value.display_name), value.value) for value in HistoryAutoClearInterval],
            settings.history_auto_clear_interval.value,
        )
        self.history_clearing_hint = hint_label(
            tr(settings.history_auto_clear_interval.description)
        )
        clearing.currentIndexChanged.connect(
            lambda: self._set_history_auto_clear(
                HistoryAutoClearInterval(clearing.currentData())
            )
        )
        history.add_labelled(tr("Clear history automatically"), clearing)
        history.add(self.history_clearing_hint)

        metrics = ToggleRow(
            tr("Show performance details in History"), checked=settings.show_history_performance_metrics
        )
        metrics.toggled.connect(
            lambda value: setattr(settings, "show_history_performance_metrics", value)
        )
        history.add(metrics)

        wpm = QSpinBox()
        wpm.setRange(10, 200)
        wpm.setValue(settings.user_typing_wpm)
        wpm.valueChanged.connect(lambda value: setattr(settings, "user_typing_wpm", value))
        history.add_labelled(tr("My typing speed (WPM)"), wpm, tr("Used for the time-saved estimate."))
        layout.addWidget(history)

        backup = Card(tr("Backup"))
        backup.add_row(
            primary_button(tr("Export settings…"), self._export_backup),
            button(tr("Import settings…"), self._import_backup),
        )
        backup.add(hint_label(tr("Includes settings, prompts, dictionary and history.")))
        layout.addWidget(backup)

        privacy = Card(tr("Privacy"))
        analytics = ToggleRow(
            tr("Share anonymous usage analytics"),
            tr(
                "Daily counts only, aggregated locally first. No transcripts ever "
                "leave this machine."
            ),
            settings.share_detailed_analytics,
        )
        analytics.toggled.connect(self._app.set_analytics_enabled)
        privacy.add(analytics)

        logs = ToggleRow(tr("Write debug logs"), checked=settings.enable_debug_logs)
        logs.toggled.connect(lambda value: setattr(settings, "enable_debug_logs", value))
        privacy.add(logs)
        privacy.add(hint_label(self._app.log_location_description()))
        layout.addWidget(privacy)

        layout.addStretch(1)
        return scrollable(container)

    def _build_experimental(self) -> QWidget:
        settings = self._app.settings
        container, layout = page(
            tr("Experimental"), tr("These are on by default and can be turned off if they misbehave.")
        )

        card = Card(tr("Dictation"))
        incremental = ToggleRow(
            tr("Incremental finalization"),
            tr("Reuses already-processed audio so long recordings finish faster."),
            settings.experimental_parakeet_unified_final_enabled,
        )
        incremental.toggled.connect(
            lambda value: setattr(
                settings, "experimental_parakeet_unified_final_enabled", value
            )
        )
        card.add(incremental)

        learning = ToggleRow(
            tr("Learn from my corrections"),
            tr("Suggests a dictionary entry after you fix the same word twice."),
            settings.automatic_dictionary_learning_enabled,
        )
        learning.toggled.connect(
            lambda value: setattr(settings, "automatic_dictionary_learning_enabled", value)
        )
        card.add(learning)

        boosting = ToggleRow(
            tr("Vocabulary boosting"),
            tr("Biases the model toward your custom words."),
            settings.vocabulary_boosting_enabled,
        )
        boosting.toggled.connect(
            lambda value: setattr(settings, "vocabulary_boosting_enabled", value)
        )
        card.add(boosting)
        layout.addWidget(card)

        api = Card(tr("Local API"), tr("Lets other tools on this machine drive Fluentry."))
        api.add(hint_label(self._app.local_api_description()))
        layout.addWidget(api)

        layout.addStretch(1)
        return scrollable(container)

    # --- actions ----------------------------------------------------------

    def _export_backup(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        suggested = self._app.backup.suggested_filename()
        path, _ = QFileDialog.getSaveFileName(self, tr("Export settings"), suggested, "JSON (*.json)")
        if path:
            self._app.export_backup(path)

    def _import_backup(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, tr("Import settings"), "", "JSON (*.json)")
        if not path:
            return
        message = self._app.import_backup(path)
        QMessageBox.information(self, tr("Import settings"), message)
