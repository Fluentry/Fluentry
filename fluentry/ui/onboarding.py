"""The first-run window.

Six steps, one window, a footer that stays in the same place throughout —
the Linux counterpart of `OnboardingFlowView`. The step logic lives in
`services/onboarding_flow.py`; this file only draws it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..persistence import voice_engine_languages as catalog
from ..persistence.speech_model import SpeechModel
from ..services.providers.onnx_asr import engine_is_installable, missing_runtime_message
from ..platform.text_injection import available_backends
from ..services.onboarding_flow import LAST_STEP, OnboardingFlow, Readiness, Step
from .tray import window_icon
from .widgets import (
    Card,
    HeaderBar,
    brand_lockup,
    button,
    hint_label,
    primary_button,
    subtitle_label,
    title_label,
)


class OnboardingWindow(QWidget):
    finished_onboarding = Signal()
    #: Carries a download result back from its worker thread. Qt widgets
    #: may only be touched on the GUI thread.
    _download_finished = Signal(str)
    #: Dictation runs on worker threads; this hops its state to the GUI.
    _dictation_state = Signal(str)

    def __init__(self, app_state, palette) -> None:
        super().__init__()
        self._app = app_state
        self._palette = palette
        self._flow = OnboardingFlow(app_state.settings, analytics=app_state.analytics)
        self._selected_route = None
        self._download_in_progress = False
        self._download_error: str | None = None
        self._download_finished.connect(
            self._on_download_finished, Qt.ConnectionType.QueuedConnection
        )
        self._dictation_phase = "idle"
        self._dictation_state.connect(
            self._on_dictation_state, Qt.ConnectionType.QueuedConnection
        )
        app_state.add_state_observer(self._dictation_state.emit)

        self.setObjectName("Window")
        self.setWindowTitle("Welcome to Fluentry")
        self.setWindowIcon(window_icon(palette.accent))
        self.resize(760, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = HeaderBar("Welcome to Fluentry")
        self._brand = brand_lockup(palette, height=22)
        if self._brand is not None:
            # The wordmark says the name better than a label does.
            self.header.title.setVisible(False)
            self.header.add_action(self._brand, leading=True)
        outer.addWidget(self.header)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(36, 28, 36, 24)
        layout.setSpacing(18)
        outer.addWidget(body, 1)

        self.title = title_label("")
        self.subtitle = subtitle_label("")
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)

        self.stack = QStackedWidget()
        self._pages = {
            Step.LANDING: self._build_landing(),
            Step.LANGUAGE: self._build_language(),
            Step.VOICE_MODEL: self._build_voice_model(),
            Step.PERMISSIONS: self._build_permissions(),
            Step.PLAYGROUND: self._build_playground(),
            Step.AI_ENHANCEMENT: self._build_ai(),
        }
        for step in Step:
            self.stack.addWidget(self._pages[step])
        layout.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        self.back_button = button("Back", self._go_back)
        self.skip_button = button("Skip", self._skip)
        self.continue_button = primary_button("Continue", self._continue)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.skip_button)
        footer.addWidget(self.continue_button)
        layout.addLayout(footer)

        # The playground waits on a real dictation, which finishes on another
        # thread, so the gate is re-checked on a timer rather than pushed.
        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._refresh_footer)
        self._poll.start()

        self._show_step()

    # --- pages ------------------------------------------------------------

    def _build_landing(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            hint_label(
                "Fluentry listens when you hold your shortcut and types what you said "
                "into whatever app you are using. Everything can run on this machine: "
                "no account, no upload."
            )
        )
        layout.addStretch(1)
        return page

    def _build_language(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("Language", "The engine list adapts to what you pick.")
        self._language_buttons = QButtonGroup(page)
        self._language_buttons.setExclusive(True)
        current = self._app.settings.onboarding_selected_language_id
        row: QHBoxLayout | None = None
        for index, language in enumerate(catalog.popular_languages()):
            if index % 4 == 0:
                row = QHBoxLayout()
                card.add_row_layout(row)
            choice = QRadioButton(language.popular_display_name)
            choice.setProperty("languageID", language.id)
            choice.setChecked(language.id == current)
            self._language_buttons.addButton(choice)
            row.addWidget(choice)
        self._language_buttons.buttonClicked.connect(self._language_chosen)
        layout.addWidget(card)
        self.language_hint = hint_label("")
        layout.addWidget(self.language_hint)
        layout.addStretch(1)
        return page

    def _build_voice_model(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.route_card = Card("Voice engine", "Pick one, then download it.")
        self._route_buttons = QButtonGroup(page)
        self._route_container = QVBoxLayout()
        self.route_card.add_row_layout(self._route_container)
        layout.addWidget(self.route_card)

        self.model_status = hint_label("")
        layout.addWidget(self.model_status)
        self.download_button = primary_button("Download", self._download_model)
        layout.addWidget(self.download_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _build_permissions(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.permissions_card = Card("Access", "Fluentry needs two things from the system.")
        self.permissions_labels: list[QLabel] = []
        for _ in range(4):
            label = hint_label("")
            self.permissions_card.add(label)
            self.permissions_labels.append(label)
        layout.addWidget(self.permissions_card)
        layout.addWidget(button("Re-check", self._refresh_permissions), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _build_playground(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.playground_hint = hint_label("")
        layout.addWidget(self.playground_hint)

        # A global hotkey is not available on every Linux desktop, so setup
        # must not depend on one. This button does the same thing.
        self.playground_button = primary_button("Start Recording", self._toggle_playground)
        layout.addWidget(self.playground_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.playground_result = QLabel("")
        self.playground_result.setWordWrap(True)
        self.playground_result.setObjectName("SectionTitle")
        layout.addWidget(self.playground_result)
        layout.addStretch(1)
        return page

    def _toggle_playground(self) -> None:
        # Reflect the click immediately: waiting for the worker thread's
        # first state change leaves the button showing the old label.
        self._dictation_phase = "idle" if self._app.asr.is_running else "recording"
        self._app.toggle_dictation()
        self._refresh_playground()

    def _on_dictation_state(self, state: str) -> None:
        if state in ("recording", "transcribing", "idle"):
            self._dictation_phase = state
            if self._flow.step is Step.PLAYGROUND:
                self._refresh_playground()
                self._refresh_footer()

    def _build_ai(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            hint_label(
                "AI enhancement rewrites each transcript with a language model before it is "
                "typed. It is entirely optional — dictation works without it, and you can "
                "set it up later in Settings."
            )
        )
        layout.addWidget(
            button("Open AI Enhancement settings", self._open_ai_settings),
            0,
            Qt.AlignmentFlag.AlignLeft,
        )
        layout.addStretch(1)
        return page

    # --- step plumbing ----------------------------------------------------

    def _show_step(self) -> None:
        step = self._flow.step
        self.title.setText(step.title)
        self.subtitle.setText(step.subtitle)
        self.header.set_title(
            "Welcome to Fluentry", f"Step {int(step) + 1} of {int(LAST_STEP) + 1}"
        )
        self.progress.setValue(int(self._flow.progress * 100))
        self.stack.setCurrentWidget(self._pages[step])
        if step is Step.LANGUAGE:
            self._language_chosen()
        elif step is Step.VOICE_MODEL:
            self._reload_routes()
        elif step is Step.PERMISSIONS:
            self._refresh_permissions()
        elif step is Step.PLAYGROUND:
            self._refresh_playground()
        self._flow.record_step_viewed()
        self._refresh_footer()

    def _readiness(self) -> Readiness:
        settings = self._app.settings
        microphone_ready = bool(self._app.devices.list_input_devices())
        return Readiness(
            has_language_routes=bool(
                catalog.routes_for_language_id(settings.onboarding_selected_language_id)
            ),
            voice_model_ready=self._app.model_is_ready(settings.selected_speech_model),
            model_preparation_in_progress=self._download_in_progress,
            microphone_ready=microphone_ready,
            typing_ready=bool(available_backends()),
            ai_ready=settings.onboarding_ai_skipped or self._app.has_api_key(),
            playground_ready=(
                settings.onboarding_playground_validated or settings.onboarding_playground_skipped
            ),
            is_recording=self._app.asr.is_running,
        )

    def _refresh_footer(self) -> None:
        step = self._flow.step
        readiness = self._readiness()
        self.back_button.setEnabled(step is not Step.LANDING)
        self.continue_button.setText(step.primary_button_title)
        self.continue_button.setEnabled(self._flow.can_continue(readiness))
        self.skip_button.setVisible(step in (Step.PLAYGROUND, Step.AI_ENHANCEMENT))
        self.skip_button.setEnabled(self._flow.can_skip(readiness))
        if step is Step.PLAYGROUND:
            self._refresh_playground()

    def _go_back(self) -> None:
        self._flow.go_back()
        self._show_step()

    def _continue(self) -> None:
        step = self._flow.step
        if step is Step.LANGUAGE and self._selected_route is not None:
            catalog.apply(self._selected_route, self._app.settings)
            self._app.reload_provider()
        if step is Step.AI_ENHANCEMENT:
            self._flow.finish()
            self.finished_onboarding.emit()
            return
        self._flow.go_next()
        self._show_step()

    def _skip(self) -> None:
        step = self._flow.step
        if step is Step.AI_ENHANCEMENT:
            self._flow.skip_ai_enhancement()
            self.finished_onboarding.emit()
            return
        self._flow.skip_playground()
        self._show_step()

    # --- per-step behaviour ----------------------------------------------

    def _language_chosen(self) -> None:
        checked = self._language_buttons.checkedButton()
        if checked is not None:
            self._app.settings.onboarding_selected_language_id = checked.property("languageID")
        language_id = self._app.settings.onboarding_selected_language_id
        routes = catalog.routes_for_language_id(language_id)
        self.language_hint.setText(
            f"{len(routes)} engine(s) can transcribe this language."
            if routes
            else "No installed engine supports this language yet."
        )
        self._refresh_footer()

    def _reload_routes(self) -> None:
        while self._route_container.count():
            item = self._route_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._route_buttons.removeButton(widget)
                widget.deleteLater()

        settings = self._app.settings
        routes = catalog.routes_for_language_id(settings.onboarding_selected_language_id)
        for route in routes:
            label = route.model.display_name
            if route.badge_text:
                label = f"{label} — {route.badge_text}"
            if not engine_is_installable(route.model):
                # Say so on the option itself rather than after a failed try.
                label = f"{label} — runtime not installed"
            choice = QRadioButton(label)
            choice.setProperty("routeID", route.id)
            choice.setChecked(route.model == settings.selected_speech_model)
            if choice.isChecked():
                self._selected_route = route
            self._route_buttons.addButton(choice)
            self._route_container.addWidget(choice)
        if self._selected_route is None and routes:
            self._selected_route = routes[0]
            self._route_buttons.buttons()[0].setChecked(True)
        self._route_buttons.buttonClicked.connect(self._route_chosen)
        self._refresh_model_status()

    def _route_chosen(self, chosen: QPushButton) -> None:
        route_id = chosen.property("routeID")
        for route in catalog.routes_for_language_id(
            self._app.settings.onboarding_selected_language_id
        ):
            if route.id == route_id:
                self._selected_route = route
                self._download_error = None
                catalog.apply(route, self._app.settings)
                self._app.reload_provider()
                break
        self._refresh_model_status()

    def _refresh_model_status(self) -> None:
        model = self._app.settings.selected_speech_model
        ready = self._app.model_is_ready(model)
        installable = engine_is_installable(model)

        if self._download_in_progress:
            self.model_status.setText(f"Downloading {model.display_name}…")
        elif self._download_error:
            # Whatever went wrong outranks the generic size line: it is the
            # only thing that tells the user what to do next.
            self.model_status.setText(self._download_error)
        elif ready:
            self.model_status.setText(f"{model.display_name} is ready.")
        elif not installable:
            self.model_status.setText(missing_runtime_message(model))
        else:
            self.model_status.setText(
                f"{model.display_name} needs {model.download_size} of download."
            )

        self.download_button.setVisible(
            not ready and not self._download_in_progress and installable
        )
        self._refresh_footer()

    def _download_model(self) -> None:
        model = self._app.settings.selected_speech_model
        self._download_in_progress = True
        self._download_error = None
        self._refresh_model_status()
        # `download_model` calls back on a worker thread; the signal hops it
        # over to the GUI thread before any widget is touched.
        self._app.download_model(model, lambda error: self._download_finished.emit(error or ""))

    def _on_download_finished(self, error: str) -> None:
        self._download_in_progress = False
        self._download_error = error or None
        self._refresh_model_status()

    def _refresh_permissions(self) -> None:
        for label, (name, ok, detail) in zip(
            self.permissions_labels, self._app.readiness_report()
        ):
            label.setText(f"{'✓' if ok else '•'}  {name}: {detail}")
        self._refresh_footer()

    def _refresh_playground(self) -> None:
        shortcut = self._app.settings.primary_dictation_shortcut_display_string or "your shortcut"
        hotkeys_work = any(
            label == "Global hotkey" and ok for label, ok, _detail in self._app.readiness_report()
        )
        if hotkeys_work:
            self.playground_hint.setText(
                f"Hold {shortcut} and say something, or use the button below. "
                "The transcript appears once it lands."
            )
        else:
            # Do not tell the user to press a shortcut this desktop cannot see.
            self.playground_hint.setText(
                "Press the button below and say something. The transcript appears "
                f"once it lands. ({shortcut} will work once global hotkeys are available.)"
            )

        recording = self._dictation_phase == "recording" or self._app.asr.is_running
        self.playground_button.setText("Stop Recording" if recording else "Start Recording")

        text = self._app.asr.final_text
        if recording:
            # A result from a previous run is stale the moment a new one starts.
            self.playground_result.setText("Listening…")
        elif self._dictation_phase == "transcribing" and not text:
            # Without this the label goes blank between speaking and the result.
            self.playground_result.setText("Transcribing…")
        elif text:
            self.playground_result.setText(f"“{text}”")
            if not self._app.settings.onboarding_playground_validated:
                self._flow.mark_playground_validated()
        else:
            self.playground_result.setText("")

    def _open_ai_settings(self) -> None:
        from .navigation import SidebarItem

        self._app.settings.onboarding_ai_skipped = False
        window = self.window()
        if hasattr(window, "show_item"):
            window.show_item(SidebarItem.AI_ENHANCEMENTS)
