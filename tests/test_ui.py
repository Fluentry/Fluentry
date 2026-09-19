"""The Qt layer.

These run against the offscreen platform plugin, so they exercise real
widgets — construction, layout, the signals that connect the app state to
the tray and the overlay — without needing a display. They skip when PySide6
is not installed, since the rest of Fluentry works without it.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="the GUI extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from fluentry.persistence.settings_types import (  # noqa: E402
    AccentColorOption,
    OverlayPosition,
    OverlaySize,
    ThemePreference,
)
from fluentry.services.onboarding_flow import Step  # noqa: E402
from fluentry.ui.navigation import SettingsSection, SidebarItem  # noqa: E402
from fluentry.ui.overlay import (  # noqa: E402
    SIZE_METRICS,
    OverlayMode,
    RecordingOverlay,
    preview_character_limit,
    truncated_preview,
)
from fluentry.ui.theme import Palette, palette_for, resolve_is_dark, stylesheet  # noqa: E402
from fluentry.ui.tray import TrayController, draw_microphone_icon  # noqa: E402


def sample(image, x: int, y: int) -> str:
    """The colour at a logical point.

    `QWidget.grab()` returns a pixmap at the display's scale, so a fixed
    coordinate lands somewhere else entirely on a 2x screen.
    """
    ratio = image.devicePixelRatio() or 1.0
    return image.pixelColor(round(x * ratio), round(y * ratio)).name()


@pytest.fixture(scope="session")
def qt_app():
    application = QApplication.instance() or QApplication(["fluentry-tests"])
    yield application


# --- theme ------------------------------------------------------------------


def test_the_theme_preference_wins_over_the_desktop():
    assert resolve_is_dark(ThemePreference.DARK, system_is_dark=False) is True
    assert resolve_is_dark(ThemePreference.LIGHT, system_is_dark=True) is False


def test_the_system_preference_follows_the_desktop():
    assert resolve_is_dark(ThemePreference.SYSTEM, system_is_dark=True) is True
    assert resolve_is_dark(ThemePreference.SYSTEM, system_is_dark=False) is False


def test_light_and_dark_palettes_actually_differ(qt_app):
    light = palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE, qt_app)
    dark = palette_for(ThemePreference.DARK, AccentColorOption.BLUE, qt_app)
    assert light.window != dark.window
    assert light.text != dark.text
    assert light.accent == dark.accent


def test_every_palette_colour_is_a_hex_string():
    palette = Palette(is_dark=True, accent="#0a84ff")
    for colour in (
        palette.window,
        palette.surface,
        palette.surface_raised,
        palette.text,
        palette.secondary_text,
        palette.separator,
        palette.danger,
        palette.success,
        palette.accent,
    ):
        assert colour.startswith("#")
        assert len(colour) == 7
        int(colour[1:], 16)  # Raises if a stray character crept in.


def test_the_stylesheet_carries_the_accent(qt_app):
    palette = palette_for(ThemePreference.DARK, AccentColorOption.PURPLE, qt_app)
    assert palette.accent in stylesheet(palette)


# --- GNOME conventions ------------------------------------------------------


def test_the_palette_uses_adwaita_greys():
    """These are Adwaita's own values, not an approximation of them."""
    light = Palette(is_dark=False, accent="#E95420")
    dark = Palette(is_dark=True, accent="#E95420")
    assert light.window == "#fafafa"
    assert light.surface == "#ffffff"
    assert dark.window == "#242424"
    assert dark.surface == "#303030"


def test_the_accents_are_ubuntu_colours():
    assert AccentColorOption.ORANGE.hex.upper() == "#E95420"  # Ubuntu orange.
    assert all(option.hex.startswith("#") for option in AccentColorOption)


def test_ubuntu_orange_is_the_default_accent(settings):
    assert settings.accent_color_option is AccentColorOption.ORANGE


def test_the_font_stack_is_the_one_ubuntu_ships():
    from fluentry.ui.theme import FONT_STACK, stylesheet

    assert "Ubuntu Sans" in FONT_STACK
    assert "Cantarell" in FONT_STACK  # GNOME's default elsewhere.
    assert FONT_STACK in stylesheet(Palette(is_dark=False, accent="#E95420"))


def test_toggles_are_drawn_as_switches_not_tick_boxes():
    sheet = stylesheet(Palette(is_dark=False, accent="#E95420"))
    indicator = sheet.split("QCheckBox::indicator {")[1].split("}")[0]
    assert "width: 44px" in indicator
    assert "height: 24px" in indicator
    assert "border-radius: 12px" in indicator  # A pill, not a square.


def test_the_sidebar_selection_is_quiet_rather_than_an_accent_block(qt_app):
    """Adwaita sidebars do not fill the row with the accent colour."""
    palette = Palette(is_dark=False, accent="#E95420")
    sheet = stylesheet(palette)
    selected = sheet.split("QListWidget#Sidebar::item:selected {")[1].split("}")[0]
    assert palette.selected in selected
    assert palette.accent not in selected


def test_accent_buttons_carry_readable_text():
    palette = Palette(is_dark=False, accent="#E95420")
    primary = stylesheet(palette).split("QPushButton#Primary {")[1].split("}")[0]
    assert palette.accent in primary
    assert palette.on_accent == "#ffffff"


def test_every_window_has_a_header_bar(qt_app, app_state):
    from fluentry.ui.main_window import MainWindow
    from fluentry.ui.navigation import SidebarItem
    from fluentry.ui.onboarding import OnboardingWindow
    from fluentry.ui.settings_window import SettingsWindow

    palette = palette_for(ThemePreference.DARK, AccentColorOption.ORANGE)
    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    for window in (
        MainWindow(app_state, palette),
        SettingsWindow(app_state, palette),
        OnboardingWindow(app_state, palette),
    ):
        assert window.header.objectName() == "HeaderBar"
        assert window.header.title.text()


def test_the_header_bar_names_the_current_page(qt_app, app_state):
    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.ORANGE))
    assert window.header.title.text() == SidebarItem.WELCOME.title
    window.show_item(SidebarItem.HISTORY)
    assert window.header.title.text() == SidebarItem.HISTORY.title


def test_the_header_bar_opens_settings(qt_app, app_state):
    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.ORANGE))
    opened = []
    window.on_open_settings = lambda: opened.append(True)
    from PySide6.QtWidgets import QPushButton

    button = window.header.findChild(QPushButton)
    assert button is not None, "the header bar has no actions"
    button.click()
    assert opened == [True]


def test_onboarding_shows_how_far_along_you_are(qt_app, app_state):
    from fluentry.services.onboarding_flow import LAST_STEP
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.ORANGE))
    window._flow.step = Step.PERMISSIONS
    window._show_step()
    assert window.header.subtitle.text() == f"Step 4 of {int(LAST_STEP) + 1}"


def test_the_desktop_decides_the_colour_scheme(monkeypatch):
    """GNOME's own setting wins over anything Qt infers."""
    from fluentry.ui import theme

    theme.system_colour_scheme.cache_clear()
    monkeypatch.setattr(theme, "_read_portal", lambda key: "(<uint32 1>,)")
    monkeypatch.setattr(theme, "_read_gsetting", lambda key: None)
    assert theme.system_colour_scheme() == "dark"

    theme.system_colour_scheme.cache_clear()
    monkeypatch.setattr(theme, "_read_portal", lambda key: "(<uint32 2>,)")
    assert theme.system_colour_scheme() == "light"

    theme.system_colour_scheme.cache_clear()
    monkeypatch.setattr(theme, "_read_portal", lambda key: "(<uint32 0>,)")
    assert theme.system_colour_scheme() is None
    theme.system_colour_scheme.cache_clear()


def test_the_desktop_accent_is_read_as_a_colour(monkeypatch):
    from fluentry.ui import theme

    theme.system_accent.cache_clear()
    monkeypatch.setattr(
        theme,
        "_read_portal",
        lambda key: "(<(0.20784313976764679, 0.51764708757400513, 0.89411765336990356)>,)",
    )
    assert theme.system_accent() == "#3584E4"
    theme.system_accent.cache_clear()


def test_a_named_desktop_accent_is_understood(monkeypatch):
    from fluentry.ui import theme

    theme.system_accent.cache_clear()
    monkeypatch.setattr(theme, "_read_portal", lambda key: None)
    monkeypatch.setattr(theme, "_read_gsetting", lambda key: "purple")
    assert theme.system_accent() == theme.GNOME_ACCENT_HEXES["purple"]
    theme.system_accent.cache_clear()


def test_the_desktop_accent_is_used_until_the_user_picks_one(monkeypatch, settings):
    from fluentry.ui import theme

    monkeypatch.setattr(theme, "system_accent", lambda: "#3584E4")
    assert settings.accent_color_was_chosen is False
    followed = theme.palette_for(
        ThemePreference.DARK, settings.accent_color_option, accent_is_explicit=False
    )
    assert followed.accent == "#3584E4"

    settings.accent_color_option = AccentColorOption.PURPLE
    assert settings.accent_color_was_chosen is True
    chosen = theme.palette_for(
        ThemePreference.DARK,
        settings.accent_color_option,
        accent_is_explicit=settings.accent_color_was_chosen,
    )
    assert chosen.accent == AccentColorOption.PURPLE.hex


def test_the_desktop_font_is_used_when_it_reports_one(monkeypatch):
    from fluentry.ui import theme

    theme.system_font.cache_clear()
    monkeypatch.setattr(theme, "_read_gsetting", lambda key: "Ubuntu Sans 11")
    assert theme.system_font() == ("Ubuntu Sans", 11)
    assert '"Ubuntu Sans"' in theme.stylesheet(Palette(is_dark=False, accent="#3584E4"))
    theme.system_font.cache_clear()


def test_a_switch_paints_a_knob_at_each_end(qt_app):
    from fluentry.ui.widgets import Switch

    palette = Palette(is_dark=False, accent="#3584E4")
    switch = Switch()
    switch.set_palette(palette)

    off = switch.grab().toImage()
    switch.setChecked(True)
    on = switch.grab().toImage()

    assert off != on, "the switch looks the same on and off"
    middle = switch.height() // 2
    # On: the track takes the accent and the knob sits at the right.
    assert sample(on, 4, middle) == palette.accent.lower()
    assert sample(on, switch.width() - 7, middle) == "#ffffff"
    # Off: the knob is back at the left.
    assert sample(off, switch.width() - 7, middle) != "#ffffff"


def test_a_settings_row_puts_the_switch_on_the_right(qt_app):
    from fluentry.ui.widgets import Switch, ToggleRow

    row = ToggleRow("Enhance dictations with AI", "Runs after transcription.")
    assert isinstance(row.checkbox, Switch)
    assert row.label.text() == "Enhance dictations with AI"
    assert row.is_checked() is False
    row.set_checked(True)
    assert row.is_checked() is True


def test_a_page_does_not_repeat_the_title_the_header_bar_shows(qt_app):
    from PySide6.QtWidgets import QLabel

    from fluentry.ui.widgets import page

    container, _layout = page("AI Enhancement", "Optionally clean up each dictation.")
    texts = [label.text() for label in container.findChildren(QLabel)]
    assert "AI Enhancement" not in texts
    assert "Optionally clean up each dictation." in texts


def test_the_window_paints_the_background_and_children_do_not(qt_app, app_state):
    """A label inside a card must not draw the window grey over the card."""
    from fluentry.ui.main_window import MainWindow

    palette = palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE)
    qt_app.setStyleSheet(stylesheet(palette))
    window = MainWindow(app_state, palette)
    window.resize(1000, 680)
    window.show()
    qt_app.processEvents()

    image = window.grab().toImage()
    assert sample(image, 500, 20) == palette.headerbar
    assert sample(image, 100, 300) == palette.sidebar

    # Sample inside a card wherever it happens to be, rather than at fixed
    # coordinates: this is about a label not painting the window grey over
    # its card, and it should not fail the next time the page gains a row.
    from fluentry.ui.widgets import Card

    card = next(
        c for c in window.pages[SidebarItem.WELCOME].findChildren(Card) if c.isVisible()
    )
    top_left = card.mapTo(window, card.rect().topLeft())
    points = [
        (top_left.x() + 12, top_left.y() + 4),
        (top_left.x() + card.width() // 2, top_left.y() + 4),
        (top_left.x() + card.width() - 12, top_left.y() + 4),
    ]
    assert {sample(image, x, y) for x, y in points} == {palette.surface}
    qt_app.setStyleSheet("")


# --- icons and text ---------------------------------------------------------


def test_qt_is_pointed_at_the_desktops_icon_directories(qt_app):
    """Qt ships with no icon search paths, so nothing resolves until told."""
    from PySide6.QtGui import QIcon

    from fluentry.ui.icons import configure_icon_theme, xdg_icon_directories

    configure_icon_theme()
    paths = QIcon.themeSearchPaths()
    for directory in xdg_icon_directories():
        assert directory in paths
    assert QIcon.fallbackThemeName() == "Adwaita"


def test_every_sidebar_item_resolves_to_an_icon(qt_app):
    from fluentry.ui.icons import configure_icon_theme, has_icon

    configure_icon_theme()
    missing = [item.title for item in SidebarItem if not has_icon(*item.icon_names)]
    assert missing == [], f"no icon found for {missing}"


def test_every_settings_section_resolves_to_an_icon(qt_app):
    from fluentry.ui.icons import configure_icon_theme, has_icon

    configure_icon_theme()
    missing = [
        section.title for section in SettingsSection if not has_icon(*section.icon_names)
    ]
    assert missing == [], f"no icon found for {missing}"


def test_symbolic_icons_are_preferred(qt_app):
    """GNOME sidebars use single-colour glyphs that take the text colour."""
    from PySide6.QtGui import QIcon

    from fluentry.ui.icons import configure_icon_theme, themed_icon

    configure_icon_theme()
    themed_icon.cache_clear()
    assert not themed_icon("go-home").isNull()
    assert not QIcon.fromTheme("go-home-symbolic").isNull()


def test_an_unknown_icon_name_falls_through_to_the_next(qt_app):
    from fluentry.ui.icons import configure_icon_theme, themed_icon

    configure_icon_theme()
    themed_icon.cache_clear()
    assert not themed_icon("not-a-real-icon-name", "go-home").isNull()
    assert themed_icon("not-a-real-icon-name").isNull()


def test_the_sidebar_shows_its_icons(qt_app, app_state):
    from fluentry.ui.icons import configure_icon_theme
    from fluentry.ui.main_window import MainWindow

    configure_icon_theme()
    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    for row in range(window.sidebar.count()):
        assert not window.sidebar.item(row).icon().isNull()


def test_font_sizes_are_points_so_they_scale_with_the_display():
    """A hand-rolled point-to-pixel conversion is wrong off 96 dpi."""
    sheet = stylesheet(Palette(is_dark=False, accent="#3584E4"))
    import re

    declared = re.findall(r"font-size:\s*([^;]+);", sheet)
    assert declared
    assert all(value.strip().endswith("pt") for value in declared), declared


def test_the_application_font_comes_from_the_desktop(qt_app, monkeypatch):
    from fluentry.ui import theme

    theme.system_font.cache_clear()
    monkeypatch.setattr(theme, "_read_gsetting", lambda key: "Ubuntu Sans 11")
    theme.apply_system_font(qt_app)
    assert qt_app.font().family() == "Ubuntu Sans"
    assert round(qt_app.font().pointSizeF()) == 11
    theme.system_font.cache_clear()


def test_accessibility_text_scaling_is_honoured(qt_app, monkeypatch):
    from fluentry.ui import theme

    theme.system_font.cache_clear()
    monkeypatch.setattr(
        theme,
        "_read_gsetting",
        lambda key: "1.5" if key == "text-scaling-factor" else "Ubuntu Sans 10",
    )
    theme.apply_system_font(qt_app)
    assert round(qt_app.font().pointSizeF()) == 15
    theme.system_font.cache_clear()


def test_a_nonsense_text_scale_is_ignored(monkeypatch):
    from fluentry.ui import theme

    monkeypatch.setattr(theme, "_read_gsetting", lambda key: "banana")
    assert theme.text_scaling_factor() == 1.0
    monkeypatch.setattr(theme, "_read_gsetting", lambda key: "99")
    assert theme.text_scaling_factor() == 1.0


def test_readiness_uses_icons_rather_than_emoji(qt_app, app_state):
    from fluentry.ui.icons import configure_icon_theme
    from fluentry.ui.pages import WelcomePage

    configure_icon_theme()
    palette = palette_for(ThemePreference.DARK, AccentColorOption.BLUE)
    page_widget = WelcomePage(app_state, palette)
    page_widget.refresh()

    assert page_widget.status_rows, "no readiness rows were built"
    for row in page_widget.status_rows:
        if not row.isVisible() and row is not page_widget.status_rows[0]:
            continue
        assert row.title.text()
        assert not row.icon.pixmap().isNull()
        # The emoji that used to stand in for the icon are gone.
        assert "✅" not in row.title.text() and "⚠" not in row.title.text()


def test_a_status_icon_lines_up_with_its_title(qt_app, app_state):
    """The glyph belongs on the title's line, not at the top of the row."""
    from fluentry.ui.icons import configure_icon_theme
    from fluentry.ui.pages import WelcomePage

    configure_icon_theme()
    palette = palette_for(ThemePreference.DARK, AccentColorOption.BLUE)
    qt_app.setStyleSheet(stylesheet(palette))
    page_widget = WelcomePage(app_state, palette)
    page_widget.resize(600, 520)
    page_widget.show()
    page_widget.refresh()
    qt_app.processEvents()

    for row in page_widget.status_rows:
        icon_centre = row.icon.mapTo(row, row.icon.rect().center()).y()
        title_centre = row.title.mapTo(row, row.title.rect().center()).y()
        assert abs(icon_centre - title_centre) <= 1, (
            f"{row.title.text()}: icon is {icon_centre - title_centre}px off its title"
        )
    qt_app.setStyleSheet("")


def test_a_status_row_title_has_no_section_margin(qt_app):
    """Section headings are spaced apart; a row title inside one is not."""
    from fluentry.ui.widgets import StatusRow

    row = StatusRow()
    assert row.title.objectName() == "RowTitle"
    sheet = stylesheet(Palette(is_dark=False, accent="#3584E4"))
    row_rule = sheet.split("QLabel#RowTitle {")[1].split("}")[0]
    assert "margin-top" not in row_rule


def test_sidebar_icons_are_drawn_at_their_native_size(qt_app, app_state):
    """Symbolic icons are 16px; asking for 18 resamples and softens them."""
    from fluentry.ui.main_window import MainWindow
    from fluentry.ui.settings_window import SettingsWindow

    palette = palette_for(ThemePreference.DARK, AccentColorOption.BLUE)
    for window in (MainWindow(app_state, palette), SettingsWindow(app_state, palette)):
        assert window.sidebar.iconSize().width() == 16
        assert window.sidebar.iconSize().height() == 16


# --- overlay ----------------------------------------------------------------


def test_the_overlay_never_takes_focus(qt_app, settings):
    from PySide6.QtCore import Qt

    overlay = RecordingOverlay(palette_for(ThemePreference.DARK, AccentColorOption.BLUE), settings)
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_each_overlay_size_has_its_own_geometry(qt_app, settings):
    overlay = RecordingOverlay(palette_for(ThemePreference.DARK, AccentColorOption.BLUE), settings)
    seen = set()
    for size in OverlaySize:
        overlay.apply_size(size)
        bars, expected = SIZE_METRICS[size]
        assert overlay.size() == expected
        assert overlay._bar_count == bars
        seen.add((bars, expected.width()))
    assert len(seen) == len(OverlaySize)


def test_presenting_and_dismissing_the_overlay(qt_app, settings):
    overlay = RecordingOverlay(palette_for(ThemePreference.DARK, AccentColorOption.BLUE), settings)
    assert overlay.is_presented is False
    overlay.present(OverlayMode.DICTATION)
    assert overlay.is_presented is True
    overlay.dismiss()
    assert overlay.is_presented is False


def test_the_overlay_caption_names_the_mode():
    assert OverlayMode.DICTATION.caption == "Listening"
    assert OverlayMode.TRANSCRIBING.caption == "Transcribing"
    assert OverlayMode.COMMAND.caption == "Command"


def test_the_overlay_sits_at_the_configured_edge(qt_app, settings):
    overlay = RecordingOverlay(palette_for(ThemePreference.DARK, AccentColorOption.BLUE), settings)
    settings.overlay_position = OverlayPosition.BOTTOM
    overlay.reposition()
    bottom = overlay.y()
    settings.overlay_position = OverlayPosition.TOP
    overlay.reposition()
    assert overlay.y() < bottom


def test_the_preview_keeps_the_most_recent_words():
    assert truncated_preview("short", 20) == "short"
    truncated = truncated_preview("abcdefghij", 4)
    assert truncated == "…ghij"
    assert truncated_preview("abc", 0) == "abc"


def test_the_preview_limit_comes_from_settings(settings):
    settings.transcription_preview_char_limit = 200
    assert preview_character_limit(settings) == 200
    # A settings-shaped object that has no such value still yields a sane limit.
    assert preview_character_limit(object()) == 150


# --- tray -------------------------------------------------------------------


def test_the_tray_icon_is_drawn_at_a_usable_size(qt_app):
    icon = draw_microphone_icon("#0a84ff")
    assert not icon.isNull()
    assert icon.pixmap(64, 64).width() == 64


def test_the_tray_menu_offers_every_action(qt_app):
    calls = []
    from fluentry.ui.tray import TrayActions

    controller = TrayController(
        palette_for(ThemePreference.DARK, AccentColorOption.BLUE, qt_app),
        TrayActions(
            toggle_dictation=lambda: calls.append("toggle"),
            open_main_window=lambda: calls.append("open"),
            open_settings=lambda: calls.append("settings"),
            open_history=lambda: calls.append("history"),
            quit=lambda: calls.append("quit"),
        ),
    )
    actions = controller.tray.contextMenu().actions()
    titles = [action.text() for action in actions if action.text()]
    assert "Start Dictation" in titles
    assert "Quit Fluentry" in titles

    for action in actions:
        if action.isEnabled() and action.text():
            action.trigger()
    assert set(calls) == {"toggle", "open", "settings", "history", "quit"}


def test_the_tray_label_follows_the_recording_state(qt_app):
    from fluentry.ui.tray import TrayActions

    noop = lambda: None  # noqa: E731
    controller = TrayController(
        palette_for(ThemePreference.DARK, AccentColorOption.BLUE, qt_app),
        TrayActions(noop, noop, noop, noop, noop),
    )
    controller.set_recording(True)
    assert "Stop" in controller._toggle_action.text()
    controller.set_recording(False)
    assert "Start" in controller._toggle_action.text()

    controller.set_status("Transcribing…")
    assert controller._status_action.text() == "Transcribing…"


# --- windows ----------------------------------------------------------------


def test_the_main_window_shows_every_sidebar_item(qt_app, app_state):
    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    assert window.sidebar.count() > 0
    for row in range(window.sidebar.count()):
        window.sidebar.setCurrentRow(row)
        assert window.stack.currentWidget() is not None
    window.refresh_current_page()


def test_the_main_window_can_be_pointed_at_one_page(qt_app, app_state):
    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window.show_item(SidebarItem.HISTORY)
    current = window.sidebar.currentItem()
    from PySide6.QtCore import Qt

    assert current.data(Qt.ItemDataRole.UserRole) == SidebarItem.HISTORY.value


def test_closing_the_main_window_leaves_the_app_running_in_the_tray(qt_app, app_state):
    from PySide6.QtGui import QCloseEvent

    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    app_state.tray_is_visible = True
    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() or not window.isVisible()


def test_every_settings_section_builds(qt_app, app_state):
    from fluentry.ui.settings_window import SettingsWindow

    window = SettingsWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    for section in SettingsSection:
        window.show_section(section)
        assert window.stack.currentWidget() is not None


def press(recorder, qt_key, scan_code, modifiers):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    # X11 reports the evdev code plus 8, which is what the recorder undoes.
    recorder.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, qt_key, modifiers, scan_code + 8, 0, 0)
    )


def test_the_shortcut_recorder_reports_a_linux_key_code(qt_app):
    from PySide6.QtCore import Qt

    from fluentry.models.keycodes import KEY_A, ModifierFlags
    from fluentry.ui.settings_window import ShortcutRecorder
    from fluentry.models.hotkey import HotkeyShortcut

    captured = []
    recorder = ShortcutRecorder(
        HotkeyShortcut.keyboard(KEY_A, ModifierFlags.NONE), captured.append
    )
    recorder.setChecked(True)
    recorder._toggle()

    press(recorder, Qt.Key.Key_A, KEY_A, Qt.KeyboardModifier.ControlModifier)
    assert captured and captured[0].key_code == KEY_A
    assert ModifierFlags.CONTROL in captured[0].modifier_flags


def test_a_bare_modifier_is_a_valid_shortcut(qt_app):
    from PySide6.QtCore import Qt

    from fluentry.models.hotkey import HotkeyShortcut
    from fluentry.models.keycodes import KEY_A, KEY_RIGHTALT, ModifierFlags
    from fluentry.ui.settings_window import ShortcutRecorder

    captured = []
    recorder = ShortcutRecorder(
        HotkeyShortcut.keyboard(KEY_A, ModifierFlags.NONE), captured.append
    )
    recorder.setChecked(True)
    recorder._toggle()

    press(recorder, Qt.Key.Key_Alt, KEY_RIGHTALT, Qt.KeyboardModifier.AltModifier)
    assert captured[0].key_code == KEY_RIGHTALT
    assert captured[0].modifier_key_codes == (KEY_RIGHTALT,)


# --- onboarding -------------------------------------------------------------


def test_every_onboarding_step_renders(qt_app, app_state):
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    for step in Step:
        window._flow.step = step
        window._show_step()
        assert window.title.text() == step.title
        assert window.continue_button.text() == step.primary_button_title


def test_the_onboarding_footer_matches_the_step(qt_app, app_state):
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))

    window._flow.step = Step.LANDING
    window._show_step()
    assert window.back_button.isEnabled() is False
    assert window.skip_button.isVisible() is False

    window._flow.step = Step.AI_ENHANCEMENT
    window._show_step()
    assert window.back_button.isEnabled() is True


def test_the_playground_can_be_driven_without_a_global_hotkey(qt_app, app_state):
    """Not every Linux desktop can deliver a global hotkey, so setup must
    not depend on one."""
    from fluentry.platform.text_injection import RecordingBackend
    from fluentry.services.providers.base import ScriptedTranscriptionProvider
    from fluentry.ui.onboarding import OnboardingWindow

    class SilentCapture:
        def __init__(self):
            self._on_pcm = None

        def start(self, device_uid, on_pcm):
            self._on_pcm = on_pcm

        def stop(self):
            self._on_pcm = None

        def speak(self):
            self._on_pcm([0.3] * 16_000, 0.3, 0.3)

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    app_state.provider = app_state.asr.provider = ScriptedTranscriptionProvider(
        responses=["spoken in the playground"]
    )
    capture = SilentCapture()
    app_state.capture = app_state.asr.capture_backend = capture
    app_state.typing.backend = RecordingBackend()

    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window._flow.step = Step.PLAYGROUND
    window._show_step()
    assert window.continue_button.isEnabled() is False

    window.playground_button.click()
    assert app_state.asr.is_running is True
    assert window.playground_button.text() == "Stop Recording"

    capture.speak()
    window.playground_button.click()
    for _ in range(200):
        if not app_state.asr.is_running and app_state.asr.final_text:
            break
        import time

        time.sleep(0.02)

    window._refresh_playground()
    window._refresh_footer()
    assert app_state.typing.backend.typed == ["spoken in the playground"]
    assert app_state.settings.onboarding_playground_validated is True
    assert window.continue_button.isEnabled() is True


def test_the_playground_does_not_name_a_shortcut_that_cannot_work(qt_app, app_state, monkeypatch):
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    monkeypatch.setattr(
        app_state, "readiness_report", lambda: [("Global hotkey", False, "no backend")]
    )
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window._flow.step = Step.PLAYGROUND
    window._show_step()
    assert "Press the button below" in window.playground_hint.text()

    monkeypatch.setattr(
        app_state, "readiness_report", lambda: [("Global hotkey", True, "works")]
    )
    window._refresh_playground()
    assert "Hold" in window.playground_hint.text()


def test_an_engine_without_its_runtime_is_labelled_and_offers_no_download(
    qt_app, app_state, monkeypatch
):
    from fluentry.ui import onboarding as onboarding_module
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    monkeypatch.setattr(onboarding_module, "engine_is_installable", lambda model: False)

    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window._flow.step = Step.VOICE_MODEL
    window._show_step()

    labels = [button.text() for button in window._route_buttons.buttons()]
    assert labels, "no engines were offered"
    assert all("runtime not installed" in label for label in labels)
    assert window.download_button.isVisible() is False


def test_a_download_failure_is_shown_and_not_overwritten(qt_app, app_state):
    """The error is the only thing telling the user what to do next."""
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window._flow.step = Step.VOICE_MODEL
    window._show_step()

    window._on_download_finished("Could not download: the network went away")
    assert window.model_status.text() == "Could not download: the network went away"

    # A later refresh must not silently replace it with the generic size line.
    window._refresh_model_status()
    assert "network went away" in window.model_status.text()


def test_finishing_onboarding_emits_once(qt_app, app_state):
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    finished = []
    window.finished_onboarding.connect(lambda: finished.append(True))

    window._flow.step = Step.AI_ENHANCEMENT
    window._continue()
    assert finished == [True]
    assert app_state.settings.onboarding_completed is True


def test_skipping_ai_enhancement_also_finishes(qt_app, app_state):
    from fluentry.ui.onboarding import OnboardingWindow

    app_state.settings.bootstrap_onboarding_state(is_true_first_open=True)
    window = OnboardingWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    finished = []
    window.finished_onboarding.connect(lambda: finished.append(True))

    window._flow.step = Step.AI_ENHANCEMENT
    window._skip()
    assert finished == [True]
    assert app_state.settings.onboarding_ai_skipped is True


# --- the application shell --------------------------------------------------


def test_the_application_wires_state_changes_to_the_ui(qt_app, app_state, monkeypatch):
    from fluentry.ui.application import FluentryApplication

    application = FluentryApplication(argv=["fluentry"], app_state=app_state)
    application._on_state_changed("recording")
    assert application.overlay.is_presented is True

    application._on_state_changed("transcribing")
    assert application.overlay._mode is OverlayMode.TRANSCRIBING

    application._on_state_changed("idle")
    assert application.overlay.is_presented is False


def test_changing_the_accent_repaints_everything(qt_app, app_state):
    from fluentry.ui.application import FluentryApplication

    application = FluentryApplication(argv=["fluentry"], app_state=app_state)
    before = application.palette.accent
    app_state.settings.accent_color_option = AccentColorOption.PURPLE
    application._on_state_changed("appearance")
    assert application.palette.accent != before
    assert application.palette.accent in application.qt.styleSheet()


def test_the_overlay_follows_a_size_change(qt_app, app_state):
    from fluentry.ui.application import FluentryApplication

    application = FluentryApplication(argv=["fluentry"], app_state=app_state)
    app_state.settings.overlay_size = OverlaySize.LARGE
    application._on_state_changed("overlay")
    assert application.overlay.size() == SIZE_METRICS[OverlaySize.LARGE][1]


def test_the_app_does_not_quit_when_the_last_window_closes(qt_app, app_state):
    from fluentry.ui.application import FluentryApplication

    application = FluentryApplication(argv=["fluentry"], app_state=app_state)
    assert application.qt.quitOnLastWindowClosed() is False


# --- a fresh install with no model ------------------------------------------


def test_the_welcome_page_offers_a_way_out_when_no_model_is_installed(qt_app, app_state, monkeypatch):
    """The Setup card used to state the problem and offer nothing to do.

    On a fresh install that is the first thing somebody sees: "still needs
    to be downloaded", and no button anywhere that downloads it.
    """
    from fluentry.ui.main_window import MainWindow
    from fluentry.ui.navigation import SidebarItem

    monkeypatch.setattr(type(app_state), "model_is_ready", lambda self, model: False)
    window = MainWindow(app_state, palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE))
    welcome = window.pages[SidebarItem.WELCOME]
    welcome.refresh()
    assert welcome.setup_card.isVisibleTo(window)

    opened = []
    window.on_open_setup = lambda: opened.append(True)
    welcome.setup_button.click()
    assert opened == [True], "the button has to actually start the wizard"


def test_the_way_out_disappears_once_a_model_is_installed(qt_app, app_state, monkeypatch):
    from fluentry.ui.main_window import MainWindow
    from fluentry.ui.navigation import SidebarItem

    monkeypatch.setattr(type(app_state), "model_is_ready", lambda self, model: True)
    window = MainWindow(app_state, palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE))
    welcome = window.pages[SidebarItem.WELCOME]
    welcome.refresh()
    assert not welcome.setup_card.isVisibleTo(window)


def test_a_download_shows_that_something_is_happening(qt_app, app_state, monkeypatch):
    """A model is hundreds of megabytes; silence reads as a hang."""
    from fluentry.ui.main_window import MainWindow
    from fluentry.ui.navigation import SidebarItem

    monkeypatch.setattr(type(app_state), "model_is_ready", lambda self, model: False)
    started = {}

    def fake_download(self, model, completion, runtime=None, on_progress=None):
        started["progress"] = on_progress
        started["completion"] = completion

    monkeypatch.setattr(type(app_state), "download_model", fake_download)
    window = MainWindow(app_state, palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE))
    engine = window.pages[SidebarItem.VOICE_ENGINE]
    engine.refresh()

    assert not engine.download_progress.isVisibleTo(engine)
    engine._download()
    assert engine.download_progress.isVisibleTo(engine), "the user is shown it is working"
    assert engine.download_status.text(), "and told what is being fetched"
    assert not engine.download_button.isEnabled(), "and cannot start it twice"

    started["completion"]("")
    qt_app.processEvents()
    assert not engine.download_progress.isVisibleTo(engine), "and it stops when finished"
