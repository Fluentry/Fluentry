"""The main window: sidebar plus pages.

A port of `ContentView`'s navigation shell. Closing the window hides it
rather than quitting, because the app's real home is the tray and the global
hotkey.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import themed_icon
from .navigation import SidebarItem
from .pages import (
    AIEnhancementPage,
    DictionaryPage,
    HistoryPage,
    StatsPage,
    VoiceEnginePage,
    WelcomePage,
)
from .tray import window_icon
from .widgets import HeaderBar, button

SIDEBAR_ITEMS = [
    SidebarItem.WELCOME,
    SidebarItem.VOICE_ENGINE,
    SidebarItem.AI_ENHANCEMENTS,
    SidebarItem.CUSTOM_DICTIONARY,
    SidebarItem.STATS,
    SidebarItem.HISTORY,
]


class MainWindow(QMainWindow):
    def __init__(self, app_state, palette) -> None:
        super().__init__()
        self._app = app_state
        #: Set by the application; the header bar's Settings button.
        self.on_open_settings = None
        self.setWindowTitle("Fluentry")
        self.setWindowIcon(window_icon(palette.accent))
        self.resize(1000, 680)
        self.setMinimumSize(760, 520)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Adwaita's split view: the sidebar and the content pane each carry
        # their own header, aligned into one continuous bar across the top.
        # This is how GNOME Settings, Files and Text Editor are built.
        sidebar_pane = QWidget()
        sidebar_column = QVBoxLayout(sidebar_pane)
        sidebar_column.setContentsMargins(0, 0, 0, 0)
        sidebar_column.setSpacing(0)
        self.sidebar_header = HeaderBar("Fluentry")
        self.sidebar_header.setObjectName("SidebarHeader")
        sidebar_column.addWidget(self.sidebar_header)

        content_pane = QWidget()
        content_column = QVBoxLayout(content_pane)
        content_column.setContentsMargins(0, 0, 0, 0)
        content_column.setSpacing(0)
        self.header = HeaderBar("Welcome")
        self.header.add_action(button("Settings", self._open_settings))
        content_column.addWidget(self.header)

        layout.addWidget(sidebar_pane)
        layout.addWidget(content_pane, 1)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(220)
        self.sidebar.setIconSize(QSize(16, 16))
        for item in SIDEBAR_ITEMS:
            entry = QListWidgetItem(themed_icon(*item.icon_names), item.title)
            entry.setData(Qt.ItemDataRole.UserRole, item.value)
            self.sidebar.addItem(entry)
        self.sidebar.currentRowChanged.connect(self._sidebar_changed)
        sidebar_column.addWidget(self.sidebar, 1)

        self.stack = QStackedWidget()
        self.pages = {
            SidebarItem.WELCOME: WelcomePage(app_state, palette),
            SidebarItem.VOICE_ENGINE: VoiceEnginePage(app_state),
            SidebarItem.AI_ENHANCEMENTS: AIEnhancementPage(app_state),
            SidebarItem.CUSTOM_DICTIONARY: DictionaryPage(app_state),
            SidebarItem.STATS: StatsPage(app_state, palette),
            SidebarItem.HISTORY: HistoryPage(app_state),
        }
        for item in SIDEBAR_ITEMS:
            self.stack.addWidget(self.pages[item])
        content_column.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.sidebar.setCurrentRow(0)

    def _open_settings(self) -> None:
        if self.on_open_settings is not None:
            self.on_open_settings()

    def show_item(self, item: SidebarItem) -> None:
        if item not in SIDEBAR_ITEMS:
            return
        self.sidebar.setCurrentRow(SIDEBAR_ITEMS.index(item))
        self.show()
        self.raise_()
        self.activateWindow()

    def set_palette(self, palette) -> None:
        self.setWindowIcon(window_icon(palette.accent))
        for page_widget in self.pages.values():
            setter = getattr(page_widget, "set_palette", None)
            if callable(setter):
                setter(palette)

    def refresh_current_page(self) -> None:
        current = self.stack.currentWidget()
        if current is not None:
            current.refresh()

    def _sidebar_changed(self, row: int) -> None:
        if row < 0 or row >= len(SIDEBAR_ITEMS):
            return
        self.stack.setCurrentIndex(row)
        self.header.set_title(SIDEBAR_ITEMS[row].title)
        # Pages only compute while visible; nothing runs in the background.
        self.stack.currentWidget().refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        """Hide instead of quitting: the hotkey must keep working."""
        if self._app.tray_is_visible:
            event.ignore()
            self.hide()
            return
        event.accept()
