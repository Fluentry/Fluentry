"""The main window's pages.

Linux counterparts of `WelcomeView`, `VoiceEngineSettingsView`,
`AIEnhancementSettingsView`, `CustomDictionaryView`, `StatsView` and
`TranscriptionHistoryView`.

Each page takes the services it needs and refreshes itself when shown, so
nothing recomputes while it is hidden — the same rule the
`StatsSnapshotStore` enforced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..persistence.settings_types import (
    AccentColorOption,
    CustomDictionaryEntry,
    TextInsertionMode,
    ThemePreference,
)
from ..persistence.speech_model import SpeechModel
from ..services.stats_snapshot import StatsSnapshot
from .widgets import (
    Card,
    MetricTile,
    SparklineChart,
    StatusRow,
    ToggleRow,
    button,
    combo,
    hint_label,
    page,
    primary_button,
    scrollable,
    section_label,
)


class Page(QWidget):
    """Base page: `refresh` is called every time the page becomes visible."""

    def refresh(self) -> None:
        return None


# --- welcome ----------------------------------------------------------------


class WelcomePage(Page):
    def __init__(self, app_state, palette=None) -> None:
        super().__init__()
        self._app = app_state
        self._palette = palette
        self.status_rows: list[StatusRow] = []
        container, layout = page(
            "Fluentry",
            "Press your dictation hotkey anywhere, speak, and the text lands in the app "
            "you were already using.",
        )

        self.shortcut_card = Card("Dictation shortcut")
        self.shortcut_label = QLabel()
        self.shortcut_card.add(self.shortcut_label)
        self.shortcut_card.add(hint_label("Change this in Settings → Dictation."))
        layout.addWidget(self.shortcut_card)

        self.status_card = Card("Setup")
        layout.addWidget(self.status_card)

        self.today_card = Card("Today")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self.words_tile = MetricTile("0", "Words dictated")
        self.transcriptions_tile = MetricTile("0", "Dictations")
        self.saved_tile = MetricTile("< 1m", "Time saved")
        for tile in (self.words_tile, self.transcriptions_tile, self.saved_tile):
            row_layout.addWidget(tile)
        self.today_card.add(row)
        layout.addWidget(self.today_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(container))

    def set_palette(self, palette) -> None:
        self._palette = palette
        for row in self.status_rows:
            row.set_palette(palette)
        self.refresh()

    def refresh(self) -> None:
        settings = self._app.settings
        self.shortcut_label.setText(settings.primary_dictation_shortcut_display_string)

        report = self._app.readiness_report()
        while len(self.status_rows) < len(report):
            row = StatusRow(self._palette)
            self.status_card.add(row)
            self.status_rows.append(row)
        for row, (label, ok, detail) in zip(self.status_rows, report):
            row.set_state(label, ok, detail)
            row.setVisible(True)
        for row in self.status_rows[len(report) :]:
            row.setVisible(False)

        summary = self._app.history.today_summary
        self.words_tile.set_value(str(summary.words))
        self.transcriptions_tile.set_value(str(summary.transcriptions))
        from ..persistence.history_store import formatted_time_saved

        self.saved_tile.set_value(
            formatted_time_saved(summary, typing_wpm=settings.user_typing_wpm)
        )


# --- voice engine -----------------------------------------------------------


class VoiceEnginePage(Page):
    def __init__(self, app_state) -> None:
        super().__init__()
        self._app = app_state
        container, layout = page(
            "Voice Engine", "Choose the speech model that runs on this machine."
        )

        self.model_card = Card("Speech model")
        self.model_combo = QComboBox()
        for model in SpeechModel.available_models():
            self.model_combo.addItem(
                f"{model.display_name} — {model.download_size}", model.value
            )
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.model_card.add_labelled("Model", self.model_combo)
        self.model_detail = hint_label("")
        self.model_card.add(self.model_detail)
        self.download_button = primary_button("Download model", self._download)
        self.model_card.add_row(self.download_button)
        self.download_status = hint_label("")
        self.model_card.add(self.download_status)
        layout.addWidget(self.model_card)

        self.language_card = Card(
            "Language", "Automatic detection works well for most people."
        )
        self.language_combo = QComboBox()
        self.language_combo.addItem("Automatic", None)
        for code, name in LANGUAGES:
            self.language_combo.addItem(name, code)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.language_card.add_labelled("Spoken language", self.language_combo)
        layout.addWidget(self.language_card)

        self.microphone_card = Card(
            "Microphone", "Fluentry uses the first working microphone in this order."
        )
        self.microphone_list = QListWidget()
        self.microphone_list.setMaximumHeight(160)
        self.microphone_card.add(self.microphone_list)
        self.microphone_card.add_row(
            button("Move up", lambda: self._move_microphone(-1)),
            button("Move down", lambda: self._move_microphone(1)),
            button("Refresh", self.refresh),
        )
        layout.addWidget(self.microphone_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(container))

    def refresh(self) -> None:
        settings = self._app.settings
        index = self.model_combo.findData(settings.selected_speech_model.value)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self._update_model_detail()

        language = settings.selected_whisper_language_code
        language_index = self.language_combo.findData(language)
        self.language_combo.setCurrentIndex(max(0, language_index))

        self.microphone_list.clear()
        for device in self._app.available_microphones():
            item = QListWidgetItem(device.name)
            item.setData(Qt.ItemDataRole.UserRole, device.uid)
            self.microphone_list.addItem(item)

    def _selected_model(self) -> SpeechModel:
        return SpeechModel.from_raw(self.model_combo.currentData(), SpeechModel.default_model())

    def _update_model_detail(self) -> None:
        from ..services.runtime_installer import runtime_for

        model = self._selected_model()
        ready = self._app.model_is_ready(model)
        warning = f"  {model.memory_warning}" if model.memory_warning else ""
        # A missing runtime used to read as "not downloaded", which is the
        # one thing it is not: no amount of downloading weights will help.
        # Say what is actually missing, before the engine is chosen rather
        # than after it fails.
        runtime = None if ready else runtime_for(model)
        if runtime is not None:
            detail = (
                f"{model.human_readable_name} · {model.language_support} · "
                f"needs the {runtime.name} runtime (about {runtime.megabytes} MB)"
            )
            if runtime.alternative:
                detail += f", or {runtime.alternative}"
        else:
            detail = (
                f"{model.human_readable_name} · {model.language_support} · "
                f"{'downloaded' if ready else 'not downloaded'}{warning}"
            )
        self.model_detail.setText(detail)
        self.download_button.setEnabled(not ready)
        if ready:
            self.download_button.setText("Downloaded")
        elif runtime is not None:
            self.download_button.setText(f"Install {runtime.name} and download")
        else:
            self.download_button.setText("Download model")

    def _model_changed(self) -> None:
        model = self._selected_model()
        self._app.settings.selected_speech_model = model
        self._app.reload_provider()
        self._update_model_detail()

    def _language_changed(self) -> None:
        self._app.settings.selected_whisper_language_code = self.language_combo.currentData()

    def _download(self) -> None:
        from ..services.runtime_installer import runtime_for

        model = self._selected_model()
        runtime = runtime_for(model)
        if runtime is not None and not self._confirm_runtime(runtime):
            return

        self.download_button.setEnabled(False)
        self.download_status.setText("Downloading…")

        def finished(error: str | None) -> None:
            self.download_status.setText(error or "Download complete.")
            self._update_model_detail()

        self._app.download_model(model, finished, runtime=runtime, on_progress=self._report)

    def _report(self, message: str) -> None:
        self.download_status.setText(message)

    def _confirm_runtime(self, runtime) -> bool:
        """Ask before fetching code, which is not the same as fetching weights.

        Model weights are data; a runtime is software that will be executed.
        That deserves a sentence naming what it is and where it comes from,
        rather than a progress bar the user never agreed to.
        """
        alternative = f"\n\nAlready have {runtime.alternative}? Cancel — it will be used instead." if runtime.alternative else ""
        answer = QMessageBox.question(
            self,
            f"Install {runtime.name}?",
            f"{self._selected_model().display_name} needs the {runtime.name} "
            f"runtime, which is not installed.\n\nFluentry can download it from "
            f"PyPI (about {runtime.megabytes} MB) into its own folder under "
            f"~/.local/share/fluentry/runtimes. Nothing outside that folder is "
            f"changed, and deleting it undoes this.{alternative}",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Ok,
        )
        return answer is QMessageBox.StandardButton.Ok

    def _move_microphone(self, offset: int) -> None:
        item = self.microphone_list.currentItem()
        if item is None:
            return
        self._app.settings.move_microphone_priority_by(
            item.data(Qt.ItemDataRole.UserRole), offset
        )
        self.refresh()


LANGUAGES = [
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("pl", "Polish"),
    ("ru", "Russian"), ("uk", "Ukrainian"), ("sv", "Swedish"), ("da", "Danish"),
    ("fi", "Finnish"), ("cs", "Czech"), ("el", "Greek"), ("hu", "Hungarian"),
    ("ro", "Romanian"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"),
    ("ar", "Arabic"), ("hi", "Hindi"), ("tr", "Turkish"), ("vi", "Vietnamese"),
]


# --- AI enhancement ---------------------------------------------------------


class AIEnhancementPage(Page):
    def __init__(self, app_state) -> None:
        super().__init__()
        self._app = app_state
        container, layout = page(
            "AI Enhancement",
            "Optionally clean up each dictation with a language model. Everything works "
            "without this.",
        )

        self.enable_card = Card("Enhancement")
        self.enable_toggle = ToggleRow(
            "Enhance dictations with AI",
            "Runs after transcription. If the provider fails, your transcript is typed anyway.",
        )
        self.enable_toggle.toggled.connect(self._set_enabled)
        self.enable_card.add(self.enable_toggle)
        self.stream_toggle = ToggleRow(
            "Stream the response", "Shows words in the overlay as the model produces them."
        )
        self.stream_toggle.toggled.connect(self._set_streaming)
        self.enable_card.add(self.stream_toggle)
        layout.addWidget(self.enable_card)

        self.provider_card = Card("Provider")
        self.provider_combo = QComboBox()
        for identifier, label, base_url in BUILT_IN_PROVIDERS:
            self.provider_combo.addItem(label, identifier)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.provider_card.add_labelled("Provider", self.provider_combo)

        self.base_url_field = QLineEdit()
        self.base_url_field.setPlaceholderText("https://api.example.com/v1")
        self.base_url_field.editingFinished.connect(self._base_url_changed)
        self.provider_card.add_labelled("Base URL", self.base_url_field)

        self.key_field = QLineEdit()
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_field.setPlaceholderText("Stored in your keyring")
        self.key_field.editingFinished.connect(self._key_changed)
        self.provider_card.add_labelled("API key", self.key_field)

        self.model_field = QLineEdit()
        self.model_field.setPlaceholderText("gpt-4o-mini")
        self.model_field.editingFinished.connect(self._model_changed)
        self.provider_card.add_labelled("Model", self.model_field)

        self.key_storage_hint = hint_label("")
        self.provider_card.add(self.key_storage_hint)
        self.verify_status = hint_label("")
        self.provider_card.add_row(button("Test connection", self._verify))
        self.provider_card.add(self.verify_status)
        layout.addWidget(self.provider_card)

        self.prompt_card = Card(
            "Prompt", "The instruction sent with every dictation. Leave blank for the default."
        )
        self.prompt_editor = QPlainTextEdit()
        self.prompt_editor.setPlaceholderText(
            "Clean up this dictation. Return only the corrected text."
        )
        self.prompt_editor.setMaximumHeight(140)
        self.prompt_editor.textChanged.connect(self._prompt_changed)
        self.prompt_card.add(self.prompt_editor)
        layout.addWidget(self.prompt_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(container))

    def refresh(self) -> None:
        settings = self._app.settings
        self.enable_toggle.set_checked(settings.enable_ai_processing)
        self.stream_toggle.set_checked(settings.enable_ai_streaming)

        index = self.provider_combo.findData(settings.selected_provider_id)
        self.provider_combo.setCurrentIndex(max(0, index))
        self.base_url_field.setText(self._app.active_base_url())
        self.model_field.setText(settings.selected_model or "")
        self.key_field.setText("" if not self._app.has_api_key() else "••••••••")

        from ..persistence.keychain import secret_backend_name

        self.key_storage_hint.setText(f"Keys are stored in your {secret_backend_name()}.")
        override = settings.default_dictation_prompt_override
        if override is not None and override != self.prompt_editor.toPlainText():
            self.prompt_editor.setPlainText(override)

    def _set_enabled(self, value: bool) -> None:
        self._app.settings.enable_ai_processing = value

    def _set_streaming(self, value: bool) -> None:
        self._app.settings.enable_ai_streaming = value

    def _provider_changed(self) -> None:
        identifier = self.provider_combo.currentData()
        self._app.select_provider(identifier)
        self.base_url_field.setText(self._app.active_base_url())

    def _base_url_changed(self) -> None:
        self._app.set_provider_base_url(self.base_url_field.text().strip())

    def _key_changed(self) -> None:
        text = self.key_field.text()
        if text and not text.startswith("•"):
            self._app.store_api_key(text)
            self.key_field.setText("••••••••")

    def _model_changed(self) -> None:
        self._app.settings.selected_model = self.model_field.text().strip() or None

    def _prompt_changed(self) -> None:
        text = self.prompt_editor.toPlainText()
        self._app.settings.default_dictation_prompt_override = text or None

    def _verify(self) -> None:
        self.verify_status.setText("Testing…")
        self.verify_status.setText(self._app.verify_provider())


BUILT_IN_PROVIDERS = [
    ("openai", "OpenAI", "https://api.openai.com/v1"),
    ("groq", "Groq", "https://api.groq.com/openai/v1"),
    ("anthropic", "Anthropic", "https://api.anthropic.com/v1"),
    ("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    ("cerebras", "Cerebras", "https://api.cerebras.ai/v1"),
    ("xai", "xAI", "https://api.x.ai/v1"),
    ("ollama", "Ollama (local)", "http://localhost:11434/v1"),
    ("lmstudio", "LM Studio (local)", "http://localhost:1234/v1"),
    ("compatible", "OpenAI-compatible", ""),
]


# --- dictionary -------------------------------------------------------------


class DictionaryPage(Page):
    def __init__(self, app_state) -> None:
        super().__init__()
        self._app = app_state
        container, layout = page(
            "Custom Dictionary",
            "Fix names and jargon the model mishears. Replacements are applied to every "
            "dictation, before anything else.",
        )

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Heard as", "Replace with"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(primary_button("Add entry", self._add))
        controls_layout.addWidget(button("Remove selected", self._remove))
        controls_layout.addWidget(button("Import…", self._import))
        controls_layout.addWidget(button("Export…", self._export))
        controls_layout.addStretch(1)
        layout.addWidget(controls)

        layout.addWidget(
            hint_label(
                'Separate several mishearings with commas: "fluent tree, fluently" → '
                '"Fluentry".'
            )
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

    def refresh(self) -> None:
        entries = self._app.settings.custom_dictionary_entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            from ..services.custom_dictionary import CustomDictionaryManualEntry

            self.table.setItem(row, 0, QTableWidgetItem(", ".join(entry.triggers)))
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    CustomDictionaryManualEntry.replacement_display_text(entry.replacement)
                ),
            )

    def _add(self) -> None:
        triggers, ok = QInputDialog.getText(
            self, "Add entry", "Heard as (comma separated):"
        )
        if not ok or not triggers.strip():
            return
        replacement, ok = QInputDialog.getText(self, "Add entry", "Replace with:")
        if not ok:
            return
        self._app.add_dictionary_entry(triggers, replacement)
        self.refresh()

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self._app.remove_dictionary_entry(row)
        self.refresh()

    def _import(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Import dictionary", "", "JSON (*.json)")
        if not path:
            return
        message = self._app.import_dictionary(path)
        QMessageBox.information(self, "Import dictionary", message)
        self.refresh()

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        suggested = self._app.dictionary_transfer.suggested_filename()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export dictionary", suggested, "JSON (*.json)"
        )
        if not path:
            return
        self._app.export_dictionary(path)


# --- stats ------------------------------------------------------------------


class StatsPage(Page):
    def __init__(self, app_state, palette) -> None:
        super().__init__()
        self._app = app_state
        container, layout = page("Stats", "Everything here is computed locally from your history.")

        totals = QWidget()
        totals_layout = QHBoxLayout(totals)
        totals_layout.setContentsMargins(0, 0, 0, 0)
        self.words_tile = MetricTile("0", "Words")
        self.transcriptions_tile = MetricTile("0", "Dictations")
        self.saved_tile = MetricTile("< 1m", "Time saved")
        self.streak_tile = MetricTile("0", "Day streak")
        for tile in (self.words_tile, self.transcriptions_tile, self.saved_tile, self.streak_tile):
            totals_layout.addWidget(tile)
        layout.addWidget(totals)

        self.chart_card = Card("Last 30 days")
        self.chart = SparklineChart(palette)
        self.chart_card.add(self.chart)
        layout.addWidget(self.chart_card)

        self.details_card = Card("Details")
        self.details_label = QLabel()
        self.details_label.setTextFormat(Qt.TextFormat.RichText)
        self.details_card.add(self.details_label)
        layout.addWidget(self.details_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(container))

    def set_palette(self, palette) -> None:
        self.chart.set_palette(palette)

    def refresh(self) -> None:
        settings = self._app.settings
        # Local time, not UTC: `build` takes its zone from `now`, and days,
        # streaks and the busiest hour have to be the user's own — a dictation
        # at 01:00 belongs to the day they were awake for, not the day UTC
        # happened to be on.
        snapshot = StatsSnapshot.build(
            self._app.history.entries, now=datetime.now().astimezone()
        ).using_weekdays(settings.weekends_dont_break_streak)

        self.words_tile.set_value(f"{snapshot.total_words:,}")
        self.transcriptions_tile.set_value(f"{snapshot.total_transcriptions:,}")
        self.saved_tile.set_value(snapshot.formatted_time_saved(settings.user_typing_wpm))
        self.streak_tile.set_value(str(snapshot.current_streak))
        self.chart.set_values([words for _, words in snapshot.daily_word_counts(30)])

        top_apps = ", ".join(snapshot.top_apps_formatted(5)) or "—"
        self.details_label.setText(
            f"<b>Average words per dictation:</b> {snapshot.average_words_per_transcription}<br>"
            f"<b>Longest dictation:</b> {snapshot.longest_transcription_words} words<br>"
            f"<b>Best streak:</b> {snapshot.best_streak} days<br>"
            f"<b>AI enhanced:</b> {snapshot.ai_enhancement_rate}%<br>"
            f"<b>Busiest hour:</b> {snapshot.peak_hour_formatted}<br>"
            f"<b>Top apps:</b> {top_apps}<br>"
            f"<b>Milestones:</b> {snapshot.total_milestones_achieved} of "
            f"{snapshot.total_milestones_possible}"
        )


# --- history ----------------------------------------------------------------


class HistoryPage(Page):
    def __init__(self, app_state) -> None:
        super().__init__()
        self._app = app_state
        container, layout = page("History", "Every dictation, stored only on this machine.")

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search history")
        self.search_field.textChanged.connect(lambda _text: self.refresh())
        layout.addWidget(self.search_field)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.entry_list = QListWidget()
        self.entry_list.currentRowChanged.connect(self._selection_changed)
        splitter.addWidget(self.entry_list)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        self.detail_header = QLabel()
        self.detail_header.setObjectName("Subtitle")
        self.detail_header.setWordWrap(True)
        detail_layout.addWidget(self.detail_header)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        detail_layout.addWidget(self.detail_text, 1)
        self.metrics_label = hint_label("")
        detail_layout.addWidget(self.metrics_label)
        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.addWidget(primary_button("Copy", self._copy))
        buttons_layout.addWidget(button("Delete", self._delete))
        buttons_layout.addStretch(1)
        detail_layout.addWidget(buttons)
        splitter.addWidget(detail)
        splitter.setSizes([280, 520])
        layout.addWidget(splitter, 1)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addStretch(1)
        clear_button = button("Clear all history", self._clear)
        clear_button.setObjectName("Destructive")
        controls_layout.addWidget(clear_button)
        layout.addWidget(controls)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        self._entries = []

    def refresh(self) -> None:
        query = self.search_field.text()
        self._entries = self._app.history.search(query)
        current = self.entry_list.currentRow()
        self.entry_list.clear()
        for entry in self._entries:
            timestamp = entry.timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
            self.entry_list.addItem(f"{timestamp}  ·  {entry.preview_text}")
        if self._entries:
            self.entry_list.setCurrentRow(min(max(0, current), len(self._entries) - 1))
        else:
            self.detail_text.setPlainText("")
            self.detail_header.setText("")
            self.metrics_label.setText("")

    def _selected_entry(self):
        row = self.entry_list.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    def _selection_changed(self, _row: int) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self.detail_header.setText(
            f"{entry.app_name or 'Unknown app'} · {entry.window_title or 'no window title'}"
        )
        self.detail_text.setPlainText(entry.processed_text)

        if not self._app.settings.show_history_performance_metrics:
            self.metrics_label.setText("")
            return
        from ..persistence.history_entry import TranscriptionHistoryEntry

        parts = []
        if entry.transcription_duration_milliseconds is not None:
            parts.append(
                "ASR "
                + TranscriptionHistoryEntry.formatted_duration(
                    entry.transcription_duration_milliseconds
                )
            )
        if entry.ai_processing_duration_milliseconds is not None:
            parts.append(
                "AI "
                + TranscriptionHistoryEntry.formatted_duration(
                    entry.ai_processing_duration_milliseconds
                )
            )
        if entry.ai_tokens_per_second is not None:
            parts.append(
                TranscriptionHistoryEntry.formatted_tokens_per_second(
                    entry.ai_tokens_per_second, compact=True
                )
            )
        if entry.ai_processing_error:
            parts.append(f"AI failed: {entry.ai_processing_error}")
        self.metrics_label.setText(" · ".join(parts))

    def _copy(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.clipboard_text is None:
            return
        self._app.clipboard.write_text(entry.clipboard_text)

    def _delete(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._app.history.delete_entry(entry.id)
        self.refresh()

    def _clear(self) -> None:
        confirmation = QMessageBox.question(
            self,
            "Clear all history",
            "Delete every stored dictation? This cannot be undone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation is QMessageBox.StandardButton.Yes:
            self._app.history.clear_all_history()
            self.refresh()
