"""The Qt application shell.

The Linux counterpart of `fluidApp` + `AppDelegate`: it owns the tray, the
overlay and the windows, and forwards state changes from `AppState` onto the
UI thread.

Everything that touches a widget is marshalled through a queued signal,
because dictation runs on worker threads and Qt widgets may only be touched
from the GUI thread.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from ..analytics.identity import ensure_first_open_recorded, first_open_at
from ..app import APP_VERSION, AppState
from ..i18n import is_rtl, resolve as _resolve_language, set_language, tr
from ..services.localapi.server import Configuration, LocalAPIServer
from .icons import configure_icon_theme
from .main_window import MainWindow
from .navigation import SettingsSection, SidebarItem
from .onboarding import OnboardingWindow
from .overlay import OverlayMode, RecordingOverlay, truncated_preview
from .settings_window import SettingsWindow
from .single_instance import SingleInstance
from .theme import apply_system_font, palette_for, stylesheet
from .tray import TrayActions, TrayController


def _apply_language(app: QApplication, preferred: str) -> None:
    """Activate the stored (or locale-implied) interface language."""
    set_language(_resolve_language(preferred))
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
    )


class _Bridge(QObject):
    """Moves worker-thread callbacks onto the GUI thread."""

    state_changed = Signal(str)
    notice = Signal(str, str)


class FluentryApplication:
    def __init__(self, argv: list[str] | None = None, app_state: AppState | None = None) -> None:
        argv = argv if argv is not None else sys.argv
        self.qt = QApplication.instance() or QApplication(argv)
        self.qt.setApplicationName("Fluentry")
        self.qt.setApplicationDisplayName("Fluentry")
        self.qt.setDesktopFileName("fluentry")
        # The app lives in the tray; closing the last window must not quit it.
        self.qt.setQuitOnLastWindowClosed(False)
        # Qt finds no themed icons until it is told where they live.
        configure_icon_theme()
        apply_system_font(self.qt)

        self.state = app_state or AppState()
        # Decide the interface language before any widget text is built.
        _apply_language(self.qt, self.state.settings.ui_language)
        self.palette = self._palette()
        self.qt.setStyleSheet(stylesheet(self.palette))

        self.bridge = _Bridge()
        self.bridge.state_changed.connect(self._on_state_changed, Qt.ConnectionType.QueuedConnection)
        self.bridge.notice.connect(self._on_notice, Qt.ConnectionType.QueuedConnection)
        self.state.add_state_observer(self.bridge.state_changed.emit)
        self.state.add_notice_observer(self.bridge.notice.emit)

        self.overlay = RecordingOverlay(self.palette, self.state.settings)
        # Only a real session has an overlay to take down and a compositor to
        # hand focus back, so the wait is set here rather than in AppState,
        # which is deliberately Qt-free and is what the tests drive.
        self.state.asr.focus_return_seconds = 0.25
        self.main_window = MainWindow(self.state, self.palette)
        self.settings_window = SettingsWindow(self.state, self.palette, self.main_window)
        self.settings_window.on_request_restart = self.restart
        self.main_window.on_open_settings = lambda: self._open_settings(SettingsSection.GENERAL)
        # A fresh install has no engine, and the wizard is the thing that
        # walks somebody through choosing and downloading one.
        self.main_window.on_open_setup = self.show_onboarding
        self.onboarding: OnboardingWindow | None = None

        self._instance = SingleInstance()
        self.local_api: LocalAPIServer | None = None
        self._background = False
        self._repaint_switches()
        self.tray: TrayController | None = None
        if TrayController.is_available():
            self.tray = TrayController(
                self.palette,
                TrayActions(
                    toggle_dictation=self.state.toggle_dictation,
                    open_main_window=self._open_main_window,
                    open_settings=lambda: self._open_settings(SettingsSection.GENERAL),
                    open_history=lambda: self._open_item(SidebarItem.HISTORY),
                    quit=self.quit,
                ),
            )
            self.tray.show()
            self.state.tray_is_visible = True

        # Drives the overlay's level meter from the capture thread's samples.
        self._level_timer = QTimer()
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self._pump_level)

    # --- lifecycle --------------------------------------------------------

    def start_in_background(self) -> None:
        """Launched from the autostart entry: stay in the tray."""
        self._background = True

    def run(self) -> int:
        # Before anything claims the hotkey or the tray.
        if not self._instance.claim():
            return 0
        self._instance.another_launch.connect(
            self._open_main_window, Qt.ConnectionType.QueuedConnection
        )

        # Deciding this before anything else keeps a brand-new install
        # deterministic: onboarding shows exactly once.
        is_true_first_open = ensure_first_open_recorded(self.state.settings.defaults)
        self.state.settings.bootstrap_onboarding_state(is_true_first_open)
        self.state.settings.repair_forced_onboarding_reset_if_needed(
            first_open_at(self.state.settings.defaults)
        )
        self._start_local_api()

        if self.state.settings.should_show_onboarding:
            self.show_onboarding()
        elif self._background and self.tray is not None:
            pass  # Autostart with a tray: no window until the user asks.
        elif self.state.settings.show_main_window_at_login_launch or self.tray is None:
            # With no tray there is nowhere else to go, so always show a window.
            self._open_main_window()
        return self.qt.exec()

    def _start_local_api(self) -> None:
        configuration = Configuration.current(self.state.settings.defaults)
        if not configuration.enabled:
            return
        server = LocalAPIServer.from_app(self.state, version=APP_VERSION)
        try:
            server.start()
        except OSError as error:
            self._on_notice("Local API unavailable", str(error))
            return
        self.local_api = server

    def quit(self) -> None:
        self._instance.release()
        self.overlay.dismiss()
        self._level_timer.stop()
        if self.local_api is not None:
            self.local_api.stop()
        self.state.shutdown()
        self.qt.quit()

    def restart(self) -> None:
        """Relaunch in a fresh process, in place.

        A freshly installed speech runtime is a native extension, and Python
        cannot swap one that is already loaded - so the app has to come up
        again to use it. Rather than telling the user to do that, it does it
        itself: os.execv replaces this process with a new one, which loads
        the new runtime first thing in main(). Onboarding is persisted, so
        the wizard resumes where it left off.
        """
        import os
        import sys

        # Tear down anything that holds a system resource the new process
        # will want - above all the single-instance lock, which is an
        # abstract socket the replacement would otherwise find still bound
        # (fds survive execv) and mistake for another copy already running.
        self._instance.release()
        self.overlay.dismiss()
        self._level_timer.stop()
        if self.local_api is not None:
            self.local_api.stop()
        self.state.shutdown()

        # Re-exec the same command. sys.argv[0] is the launcher; running it
        # through the current interpreter reproduces how it was started.
        os.execv(sys.executable, [sys.executable, sys.argv[0], *sys.argv[1:]])

    def show_onboarding(self) -> None:
        self.onboarding = OnboardingWindow(self.state, self.palette)
        self.onboarding.finished_onboarding.connect(self._finish_onboarding)
        self.onboarding.on_request_restart = self.restart
        self.onboarding.show()

    def _finish_onboarding(self) -> None:
        # The flow sets the flag itself; this only tidies up the window.
        if self.onboarding is not None:
            self.onboarding.close()
            self.onboarding = None
        self._open_main_window()

    # --- windows ----------------------------------------------------------

    def _open_main_window(self) -> None:
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()
        self.main_window.refresh_current_page()

    def _open_item(self, item: SidebarItem) -> None:
        self.main_window.show_item(item)

    def _open_settings(self, section: SettingsSection) -> None:
        self.settings_window.show_section(section)

    # --- state ------------------------------------------------------------

    def _on_state_changed(self, state: str) -> None:
        if state == "recording":
            self.overlay.present(OverlayMode.DICTATION)
            self._level_timer.start()
            if self.tray is not None:
                self.tray.set_recording(True)
                self.tray.set_status(tr("Recording…"))
        elif state == "transcribing":
            self.overlay.set_mode(OverlayMode.TRANSCRIBING)
            if self.tray is not None:
                self.tray.set_recording(False)
                self.tray.set_status(tr("Transcribing…"))
        elif state == "inserting":
            # Take the overlay down before the text is inserted: while it is
            # up it holds the keyboard focus, and the insertion would land on
            # it rather than on the window the user was writing in.
            self._level_timer.stop()
            self.overlay.dismiss()
            if self.tray is not None:
                self.tray.set_recording(False)
                self.tray.set_status(tr("Inserting…"))
        elif state == "idle":
            self._level_timer.stop()
            self.overlay.dismiss()
            if self.tray is not None:
                self.tray.set_recording(False)
                self.tray.set_status(tr("Idle"))
            self.main_window.refresh_current_page()
        elif state == "appearance":
            self._reload_appearance()
        elif state == "overlay":
            self.overlay.apply_size(self.state.settings.overlay_size)
            self.overlay.reposition()

    def _on_notice(self, title: str, message: str) -> None:
        if self.tray is not None:
            self.tray.notify(title, message)
        elif self.main_window.isVisible():
            QMessageBox.information(self.main_window, title, message)

    def _pump_level(self) -> None:
        self.overlay.set_level(self.state.asr.last_level)
        preview = self.state.asr.partial_transcription
        if preview and self.state.settings.enable_streaming_preview:
            self.overlay.set_preview(
                truncated_preview(preview, self.state.settings.transcription_preview_char_limit)
            )

    def _repaint_switches(self) -> None:
        """Switches paint themselves, so they need the palette directly."""
        from .widgets import Switch

        for window in (self.main_window, self.settings_window, self.onboarding):
            if window is None:
                continue
            for switch in window.findChildren(Switch):
                switch.set_palette(self.palette)

    def _palette(self):
        """Follow the desktop unless the user has chosen for themselves."""
        settings = self.state.settings
        return palette_for(
            settings.theme_preference,
            settings.accent_color_option,
            self.qt,
            accent_is_explicit=settings.accent_color_was_chosen,
        )

    def _reload_appearance(self) -> None:
        self.palette = self._palette()
        self.qt.setStyleSheet(stylesheet(self.palette))
        self.overlay.set_palette(self.palette)
        self.main_window.set_palette(self.palette)
        self._repaint_switches()
        if self.tray is not None:
            self.tray.set_palette(self.palette)
