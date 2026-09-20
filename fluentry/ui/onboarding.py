"""The first-run window.

Six steps, one window, a footer that stays in the same place throughout —
the Linux counterpart of `OnboardingFlowView`. The step logic lives in
`services/onboarding_flow.py`; this file only draws it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
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

from ..i18n import tr
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
    #: Progress lines from the same worker thread.
    _download_progress_text = Signal(str)
    #: Raised from the download thread when a freshly installed runtime
    #: needs a fresh process to take effect.
    _restart_needed = Signal()
    #: Dictation runs on worker threads; this hops its state to the GUI.
    _dictation_state = Signal(str)

    def __init__(self, app_state, palette) -> None:
        super().__init__()
        self._app = app_state
        self._palette = palette
        self._flow = OnboardingFlow(app_state.settings, analytics=app_state.analytics)
        self._selected_route = None
        self._routes_connected = False
        self._download_in_progress = False
        self._download_error: str | None = None
        self._download_finished.connect(
            self._on_download_finished, Qt.ConnectionType.QueuedConnection
        )
        self._download_progress_text.connect(
            self._on_download_progress, Qt.ConnectionType.QueuedConnection
        )
        #: Set by the application: relaunch the whole app in place.
        self.on_request_restart = None
        self._restart_needed.connect(
            self._on_restart_needed, Qt.ConnectionType.QueuedConnection
        )
        self._dictation_phase = "idle"
        self._dictation_state.connect(
            self._on_dictation_state, Qt.ConnectionType.QueuedConnection
        )
        app_state.add_state_observer(self._dictation_state.emit)

        self.setObjectName("Window")
        self.setWindowTitle(tr("Welcome to Fluentry"))
        self.setWindowIcon(window_icon(palette.accent))
        self.resize(760, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = HeaderBar(tr("Welcome to Fluentry"))
        self._brand = brand_lockup(palette, height=26)  # 20% larger than the 22 used elsewhere
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
        # A greyed-out Continue with nothing next to it leaves the user to
        # guess what is still wanted — which, on the engine step, is a
        # download they have no reason to know is required.
        self.blocked_reason = hint_label("")
        footer.addWidget(self.blocked_reason)
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
                tr(
                    "Fluentry listens when you hold your shortcut and types what you "
                    "said into whatever app you are using. Everything can run on this "
                    "machine: no account, no upload."
                )
            )
        )
        layout.addStretch(1)
        return page

    def _build_language(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card(tr("Language"), tr("The engine list adapts to what you pick."))
        self._language_buttons = QButtonGroup(page)
        self._language_buttons.setExclusive(True)
        current = self._app.settings.onboarding_selected_language_id
        # A grid, not a row of rows: separate QHBoxLayouts size each item to
        # its own text, so the columns drift apart and a short last row
        # spreads itself across the width instead of lining up.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        columns = 4
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        card.add_row_layout(grid)
        for index, language in enumerate(catalog.popular_languages()):
            choice = QRadioButton(tr(language.popular_display_name))
            choice.setProperty("languageID", language.id)
            choice.setChecked(language.id == current)
            self._language_buttons.addButton(choice)
            grid.addWidget(choice, index // columns, index % columns)
        self._language_buttons.buttonClicked.connect(self._language_chosen)
        layout.addWidget(card)
        self.language_hint = hint_label("")
        layout.addWidget(self.language_hint)
        layout.addStretch(1)
        return page

    def _build_voice_model(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.route_card = Card(tr("Voice engine"), tr("Pick one, then download it."))
        self._route_buttons = QButtonGroup(page)
        self._route_container = QVBoxLayout()
        self.route_card.add_row_layout(self._route_container)

        # Size, action and progress live inside the card with the list they
        # act on. They used to float in the page below it, so the primary
        # button of the whole step sat orphaned in empty space.
        self.model_status = hint_label("")
        self.route_card.add(self.model_status)
        self.download_button = primary_button(tr("Download"), self._download_model)
        self.route_card.add_actions(self.download_button)
        # Hundreds of megabytes with no sign of movement reads as a freeze.
        self.download_progress = QProgressBar()
        self.download_progress.setTextVisible(False)
        self.download_progress.setRange(0, 0)
        self.download_progress.setMaximumHeight(6)
        self.route_card.add(self.download_progress)
        self.download_progress.setVisible(False)

        layout.addWidget(self.route_card)
        layout.addStretch(1)
        return page

    def _build_permissions(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.permissions_card = Card(tr("Access"), tr("What Fluentry needs from your system to dictate."))
        # Rows are created on demand to match the readiness report, which is
        # one longer on GNOME (the terminal-support line).
        self.permissions_labels: list[QLabel] = []
        layout.addWidget(self.permissions_card)
        layout.addWidget(button(tr("Re-check"), self._refresh_permissions), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _build_playground(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        # The step used to be a button and a sentence loose on the page,
        # with the transcript appearing somewhere below it. Everything the
        # step is about now sits in one card, and the place the words will
        # land is visible before they land, so the screen does not change
        # shape underneath the person reading it.
        card = Card(tr("Try a dictation"), tr("Nothing here is saved or sent anywhere."))
        # The instruction is the interface: hold the real shortcut and speak,
        # exactly as dictation works everywhere else. Clicking a "Start
        # Recording" button and watching it flip to "Stop" was a second,
        # made-up way to do the one thing the app is about, and it collided
        # with the permission prompt. The button stays only as a fallback
        # for desktops where the global hotkey cannot work.
        self.playground_hint = QLabel("")
        self.playground_hint.setWordWrap(True)
        self.playground_hint.setObjectName("RowTitle")
        card.add(self.playground_hint)

        self.playground_button = primary_button(tr("Start Recording"), self._toggle_playground)
        card.add_actions(self.playground_button)

        self.playground_result = QLabel(tr("Your words will appear here."))
        self.playground_result.setWordWrap(True)
        self.playground_result.setObjectName("SectionTitle")
        self.playground_result.setMinimumHeight(64)
        self.playground_result.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        card.add(self.playground_result)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _toggle_playground(self) -> None:
        # Reflect the click immediately: waiting for the worker thread's
        # first state change leaves the button showing the old label.
        self._dictation_phase = "idle" if self._app.asr.is_running else "recording"
        self._app.toggle_dictation()
        self._refresh_playground()

    def _on_dictation_state(self, state: str) -> None:
        # "failed" and "silent" used to fall through here unhandled, so a
        # dictation that went wrong simply put the placeholder back and the
        # step looked as though nothing had happened at all.
        if state in ("recording", "transcribing", "idle", "failed", "silent", "empty"):
            self._dictation_phase = state
            if self._flow.step is Step.PLAYGROUND:
                self._refresh_playground()
                self._refresh_footer()

    def _build_ai(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            hint_label(
                tr(
                    "AI enhancement rewrites each transcript with a language model "
                    "before it is typed. It is entirely optional — dictation works "
                    "without it, and you can set it up later in Settings."
                )
            )
        )
        layout.addWidget(
            button(tr("Open AI Enhancement settings"), self._open_ai_settings),
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
            tr("Welcome to Fluentry"),
            tr("Step {current} of {total}").format(
                current=int(step) + 1, total=int(LAST_STEP) + 1
            ),
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

    def _blocked_reason(self, step, readiness) -> str:
        """Why Continue is unavailable, in the words of what to do next."""
        if step is Step.VOICE_MODEL:
            if readiness.model_preparation_in_progress:
                return tr("Downloading…")
            if not readiness.has_language_routes:
                return tr("No engine supports this language yet.")
            if not engine_is_installable(self._app.settings.selected_speech_model):
                return tr("This engine's runtime is not installed.")
            return tr("Download the engine first.")
        if step is Step.PERMISSIONS:
            if not readiness.microphone_ready:
                return tr("No microphone was found.")
            if not readiness.typing_ready:
                return tr("No way to type into other apps yet.")
        if step is Step.PLAYGROUND and not readiness.playground_ready:
            return tr("Try a dictation, or skip.")
        return ""

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
        can_continue = self._flow.can_continue(readiness)
        self.continue_button.setEnabled(can_continue)
        self.blocked_reason.setText("" if can_continue else self._blocked_reason(step, readiness))
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
            tr("{count} engine(s) can transcribe this language.").format(count=len(routes))
            if routes
            else tr("No installed engine supports this language yet.")
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
        # The app's own default is the recommendation; everything else is
        # ordered behind it, and engines whose runtime is absent go last.
        # The list used to arrive in catalogue order, which put "runtime not
        # installed" above the obvious choice and left it third.
        recommended = SpeechModel.default_model()
        routes = sorted(
            routes,
            key=lambda route: (
                not engine_is_installable(route.model),
                route.model is not recommended,
                not bool(route.badge_text),
            ),
        )
        for route in routes:
            label = route.model.display_name
            if route.badge_text:
                label = f"{label} — {route.badge_text}"
            if route.model is recommended:
                # Named from what the app actually defaults to, not from
                # whichever entry the sort happened to put first.
                label = f"{label}   ·   " + tr("Recommended")
            if not engine_is_installable(route.model):
                # Say so on the option itself rather than after a failed try.
                label = f"{label} — " + tr("runtime not installed")
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
        if not self._routes_connected:
            # This method runs every time the step is shown, and connecting
            # here each time made one click fire the handler repeatedly.
            self._route_buttons.buttonClicked.connect(self._route_chosen)
            self._routes_connected = True
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

        # A failure is shown in the danger colour, not as another grey hint
        # the eye slides over - a runtime that could not be installed is the
        # one thing on this step the user has to act on.
        self._set_status_is_error(bool(self._download_error) and not self._download_in_progress)
        if self._download_in_progress:
            self.model_status.setText(tr("Downloading {name}…").format(name=model.display_name))
        elif self._download_error:
            # Whatever went wrong outranks the generic size line: it is the
            # only thing that tells the user what to do next.
            self.model_status.setText(self._download_error)
        elif ready:
            self.model_status.setText(tr("{name} is ready.").format(name=model.display_name))
        elif not installable:
            self.model_status.setText(missing_runtime_message(model))
        else:
            self.model_status.setText(
                tr("{name} needs {size} of download.").format(
                    name=model.display_name, size=model.download_size
                )
            )

        self.download_button.setVisible(
            not ready and not self._download_in_progress and installable
        )
        self.download_progress.setVisible(self._download_in_progress)
        self._refresh_footer()

    def _set_status_is_error(self, is_error: bool) -> None:
        name = "Error" if is_error else "Hint"
        if self.model_status.objectName() == name:
            return
        self.model_status.setObjectName(name)
        # Qt only re-reads the stylesheet after the object name changes if
        # the widget is unpolished and polished again.
        self.model_status.style().unpolish(self.model_status)
        self.model_status.style().polish(self.model_status)

    def _download_model(self) -> None:
        from ..services.runtime_installer import runtime_for

        model = self._app.settings.selected_speech_model
        # The engine needs its runtime as well as its weights. The system
        # one may be missing, or - on Ubuntu - present but unable to
        # transcribe; either way this fetches a working copy first. Without
        # it the wizard downloaded the weights, reached "Try Fluentry", and
        # every dictation was refused for want of a runtime nobody installed.
        runtime = runtime_for(model)
        self._download_in_progress = True
        self._download_error = None
        self._refresh_model_status()
        # `download_model` calls back on a worker thread; the signal hops it
        # over to the GUI thread before any widget is touched.
        self._app.download_model(
            model,
            lambda error: self._download_finished.emit(error or ""),
            runtime=runtime,
            on_progress=lambda message: self._download_progress_text.emit(message),
            on_restart_needed=lambda: self._restart_needed.emit(),
        )

    def _on_restart_needed(self) -> None:
        """The engine is installed; the app relaunches itself to use it.

        The user asked to download an engine, not to restart the app, so it
        does that itself rather than leaving a "please restart" note. The
        wizard resumes after the relaunch, on a ready engine.
        """
        self._download_in_progress = False
        self.model_status.setText(tr("Setup complete — restarting Fluentry…"))
        self.download_progress.setVisible(False)
        if self.on_request_restart is not None:
            # A beat so the line above is readable before the window blinks.
            QTimer.singleShot(900, self.on_request_restart)

    def _on_download_progress(self, message: str) -> None:
        self.model_status.setText(message)

    def _on_download_finished(self, error: str) -> None:
        self._download_in_progress = False
        self._download_error = error or None
        self._refresh_model_status()

    def _refresh_permissions(self) -> None:
        report = self._app.readiness_report()
        while len(self.permissions_labels) < len(report):
            label = hint_label("")
            self.permissions_card.add(label)
            self.permissions_labels.append(label)
        for index, label in enumerate(self.permissions_labels):
            if index < len(report):
                name, ok, detail = report[index]
                label.setText(f"{'✓' if ok else '•'}  {tr(name)}: {tr(detail)}")
                label.setVisible(True)
            else:
                label.setVisible(False)
        self._refresh_footer()

    def _refresh_playground(self) -> None:
        shortcut = self._app.settings.primary_dictation_shortcut_display_string or tr("your shortcut")
        hotkeys_work = any(
            label == "Global hotkey" and ok for label, ok, _detail in self._app.readiness_report()
        )
        recording = self._dictation_phase == "recording" or self._app.asr.is_running
        if hotkeys_work:
            # The hotkey is the whole feature; let the user try it directly.
            self.playground_hint.setText(
                tr("Hold {shortcut}, say a few words, then release.").format(shortcut=shortcut)
            )
            self.playground_button.setVisible(False)
        else:
            # No global hotkey on this desktop, so offer the button instead
            # of telling the user to press a shortcut nothing will hear.
            self.playground_hint.setText(
                tr(
                    "Press the button and say a few words. ({shortcut} will work "
                    "once global hotkeys are available.)"
                ).format(shortcut=shortcut)
            )
            self.playground_button.setVisible(True)
            self.playground_button.setText(tr("Stop Recording") if recording else tr("Start Recording"))

        text = self._app.asr.final_text
        if recording:
            # A result from a previous run is stale the moment a new one starts.
            self.playground_result.setText(tr("Listening…"))
        elif self._dictation_phase == "silent":
            self.playground_result.setText(
                tr("That recording was silent. Check the microphone and try again.")
            )
        elif self._dictation_phase == "empty":
            self.playground_result.setText(
                tr(
                    "Nothing was recognised in that recording. Try speaking a little "
                    "longer, or check that the right microphone is selected."
                )
            )
        elif self._dictation_phase == "failed":
            # Saying what went wrong beats reverting to the placeholder and
            # leaving the user to conclude the app simply ignored them.
            self.playground_result.setText(
                self._app.last_error or tr("That dictation failed. Try again.")
            )
        elif self._dictation_phase == "transcribing" and not text:
            # Without this the label goes blank between speaking and the result.
            self.playground_result.setText(tr("Transcribing…"))
        elif text:
            self.playground_result.setText(f"“{text}”")
            if not self._app.settings.onboarding_playground_validated:
                self._flow.mark_playground_validated()
        else:
            # Not blank: the label is where the transcript will appear, and
            # an empty one leaves the step looking like a button on a void.
            self.playground_result.setText(tr("Your words will appear here."))

    def _open_ai_settings(self) -> None:
        from .navigation import SidebarItem

        self._app.settings.onboarding_ai_skipped = False
        window = self.window()
        if hasattr(window, "show_item"):
            window.show_item(SidebarItem.AI_ENHANCEMENTS)
