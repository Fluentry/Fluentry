"""Port of PasteClipboardTests, PasteKeyCodeResolverTests, the paste-cache
regression tests and TypingServiceTransientPasteboardTests."""

import pytest

from fluentry.models.keycodes import KEY_V, ModifierFlags
from fluentry.persistence.settings_types import TextInsertionMode
from fluentry.platform.clipboard import (
    TEXT_MIME,
    ClipboardService,
    InMemoryClipboard,
    PreservedClipboardSnapshot,
)
from fluentry.platform.paste_key import (
    FALLBACK_PASTE_KEY_CODE,
    PasteKeyCodeCache,
    resolve_from_keymap,
)
from fluentry.platform.text_injection import (
    TRANSIENT_MIME_TYPES,
    RecordingBackend,
    TypingService,
    can_dispatch_post_insertion_action,
    can_insert_before_post_insertion_action,
    make_transient_clipboard_item,
)

QWERTY_KEYMAP = """
keycode   9 = Escape NoSymbol Escape
keycode  38 = a A a A
keycode  55 = v V v V v V
"""

DVORAK_KEYMAP = """
keycode   9 = Escape NoSymbol Escape
keycode  48 = v V v V
keycode  55 = k K k K
"""


# --- clipboard preservation -------------------------------------------------


def test_rich_clipboard_round_trip():
    clipboard = InMemoryClipboard()
    clipboard.write_all(
        [
            {TEXT_MIME: b"original", "text/rtf": bytes([0, 1, 2, 255])},
            {"image/png": bytes([3, 4, 5])},
        ]
    )
    preserved = PreservedClipboardSnapshot.capture(clipboard)

    clipboard.write_text("dictated")
    owned = clipboard.change_count
    assert preserved.restore(clipboard, if_unchanged_since=owned)
    assert PreservedClipboardSnapshot.capture(clipboard).items == preserved.items


def test_identical_text_copied_again_belongs_to_the_user():
    clipboard = InMemoryClipboard()
    clipboard.write_text("original")
    preserved = PreservedClipboardSnapshot.capture(clipboard)

    clipboard.write_text("dictated")
    old_generation = clipboard.change_count
    clipboard.write_text("dictated")  # the user copied the same text themselves
    user_copy = clipboard.change_count

    assert not preserved.restore(clipboard, if_unchanged_since=old_generation)
    assert clipboard.change_count == user_copy
    assert clipboard.read_text() == "dictated"


def test_a_newer_user_copy_is_never_overwritten():
    clipboard = InMemoryClipboard()
    preserved = PreservedClipboardSnapshot.capture(clipboard)
    clipboard.write_text("dictated")
    old_generation = clipboard.change_count
    clipboard.write_text("new user copy")

    assert not preserved.restore(clipboard, if_unchanged_since=old_generation)
    assert clipboard.read_text() == "new user copy"


def test_an_originally_empty_clipboard_keeps_the_borrowed_text():
    """Nothing to put back means nothing to do — not "wipe it".

    Clearing here used to be the tidier answer: the clipboard was empty
    before, so leave it empty. But the app cannot tell whether the paste
    chord actually reached the focused window, and when it did not, that
    borrowed text is the only copy of what the user said. Leaving a
    transcript on an empty clipboard is a much smaller cost than losing it.
    """
    clipboard = InMemoryClipboard()
    empty = PreservedClipboardSnapshot.capture(clipboard)
    clipboard.write_text("temporary")

    assert not empty.restore(clipboard, if_unchanged_since=clipboard.change_count)
    assert clipboard.read_text() == "temporary"


def test_clipboard_service_skips_empty_text():
    clipboard = InMemoryClipboard()
    service = ClipboardService(clipboard)

    assert not service.copy_to_clipboard("")
    assert clipboard.change_count == 0
    assert service.copy_to_clipboard("hello")
    assert service.get_from_clipboard() == "hello"


# --- paste key resolution ---------------------------------------------------


def test_paste_key_resolves_from_the_active_layout():
    # X11 keycode 55 is evdev 47 (KEY_V) on a QWERTY layout.
    assert resolve_from_keymap(QWERTY_KEYMAP) == KEY_V
    # Dvorak puts "v" on X11 keycode 48, i.e. evdev 40.
    assert resolve_from_keymap(DVORAK_KEYMAP) == 40


def test_paste_key_falls_back_when_layout_data_is_unavailable():
    assert resolve_from_keymap(None) == FALLBACK_PASTE_KEY_CODE
    assert resolve_from_keymap("") == FALLBACK_PASTE_KEY_CODE
    assert resolve_from_keymap("garbage without keycodes") == FALLBACK_PASTE_KEY_CODE


def test_paste_key_cache_resolves_once_at_startup_and_never_on_paste():
    selected = [KEY_V]
    cache = PasteKeyCodeCache(resolve=lambda: selected[0])

    cache.start()
    cache.start()
    assert cache.lookup_count == 1, "startup must resolve exactly once"

    for _ in range(10_000):
        assert cache.snapshot() == KEY_V
    assert cache.lookup_count == 1, "pastes must not query the layout"


def test_paste_key_cache_refreshes_on_a_layout_change():
    selected = [KEY_V]
    cache = PasteKeyCodeCache(resolve=lambda: selected[0])
    cache.start()

    for key_code in (40, 12, KEY_V, 40, KEY_V):
        selected[0] = key_code
        cache.layout_did_change()
        assert cache.snapshot() == key_code


def test_paste_key_cache_ignores_layout_changes_before_start():
    cache = PasteKeyCodeCache(resolve=lambda: 40)
    cache.layout_did_change()
    assert cache.lookup_count == 0
    assert cache.snapshot() == FALLBACK_PASTE_KEY_CODE


def test_paste_key_cache_falls_back_when_the_resolver_throws():
    def broken() -> int:
        raise RuntimeError("no display")

    cache = PasteKeyCodeCache(resolve=broken)
    cache.start()
    assert cache.snapshot() == FALLBACK_PASTE_KEY_CODE


# --- transient clipboard item -----------------------------------------------


def test_transient_clipboard_item_carries_text():
    item = make_transient_clipboard_item("hello world")
    assert item[TEXT_MIME].decode("utf-8") == "hello world"
    assert item["text/plain"].decode("utf-8") == "hello world"


def test_transient_clipboard_item_is_marked_for_clipboard_managers():
    item = make_transient_clipboard_item("hello world")
    for marker in TRANSIENT_MIME_TYPES:
        assert marker in item
        assert item[marker] == b""


def test_transient_clipboard_item_is_not_marked_as_a_secret():
    # A "password manager hint" would hide a transcript for the wrong reason.
    item = make_transient_clipboard_item("hello world")
    assert "x-kde-passwordManagerHint" not in item


def test_transient_markers_survive_a_clipboard_write():
    clipboard = InMemoryClipboard()
    clipboard.write_all([make_transient_clipboard_item("hello world")])
    stored = clipboard.read_all()[0]

    assert stored[TEXT_MIME].decode("utf-8") == "hello world"
    for marker in TRANSIENT_MIME_TYPES:
        assert marker in stored


# --- typing service ---------------------------------------------------------


def make_service(**kwargs) -> tuple[TypingService, RecordingBackend, InMemoryClipboard]:
    backend = RecordingBackend()
    clipboard = InMemoryClipboard()
    service = TypingService(
        backend=backend, clipboard=clipboard, paste_settle_seconds=0, **kwargs
    )
    return service, backend, clipboard


def test_standard_mode_types_directly_and_leaves_the_clipboard_alone():
    service, backend, clipboard = make_service()
    clipboard.write_text("user content")
    before = clipboard.change_count

    assert service.type_text("dictated text")

    assert backend.typed == ["dictated text"]
    assert backend.chords == []
    assert clipboard.change_count == before
    assert clipboard.read_text() == "user content"


def test_reliable_paste_mode_borrows_and_restores_the_clipboard():
    service, backend, clipboard = make_service(insertion_mode=TextInsertionMode.RELIABLE_PASTE)
    clipboard.write_text("user content")

    assert service.type_text("dictated text")

    assert backend.typed == [], "reliable paste never types character by character"
    assert backend.chords == [("v", ("ctrl",))]
    assert clipboard.read_text() == "user content", "the user's clipboard is restored"


def test_standard_mode_falls_back_to_pasting_when_typing_fails():
    backend = RecordingBackend(succeeds=False)
    clipboard = InMemoryClipboard()
    clipboard.write_text("user content")
    service = TypingService(backend=backend, clipboard=clipboard, paste_settle_seconds=0)

    # A native Wayland client cannot be typed into by an X11-only backend, but
    # the clipboard path still reaches it.
    assert not service.type_text("dictated text")
    assert backend.typed == ["dictated text"]
    assert backend.chords == [("v", ("ctrl",))]
    assert clipboard.read_text() == "user content"


def test_empty_text_is_a_no_op():
    service, backend, clipboard = make_service()
    assert service.type_text("")
    assert backend.typed == []
    assert clipboard.change_count == 0


def test_send_key_maps_modifier_flags_to_backend_names():
    service, backend, _ = make_service()
    service.send_key("Return", ModifierFlags.CONTROL | ModifierFlags.SHIFT)

    key, modifiers = backend.chords[0]
    assert key == "Return"
    assert set(modifiers) == {"ctrl", "shift"}


# --- post-insertion action gating -------------------------------------------


def test_post_insertion_action_requires_the_exact_focused_target():
    assert can_dispatch_post_insertion_action(
        preferred_target_pid=42,
        required_target_pid=42,
        is_secure_text_field=False,
        modifiers_released=True,
        exact_focus_is_active=True,
    )
    # A different window is now focused: sending Enter there would be wrong.
    assert not can_dispatch_post_insertion_action(42, 43, False, True, True)
    assert not can_dispatch_post_insertion_action(None, None, False, True, True)
    assert not can_dispatch_post_insertion_action(0, 0, False, True, True)
    # A password field must never receive a synthetic Enter.
    assert not can_dispatch_post_insertion_action(42, 42, True, True, True)
    # The dictation hotkey is still held, so Enter would become a shortcut.
    assert not can_dispatch_post_insertion_action(42, 42, False, False, True)
    assert not can_dispatch_post_insertion_action(42, 42, False, True, False)


def test_insert_before_action_ignores_modifier_state():
    assert can_insert_before_post_insertion_action(42, 42, False, True)
    assert not can_insert_before_post_insertion_action(42, 43, False, True)
    assert not can_insert_before_post_insertion_action(42, 42, True, True)
    assert not can_insert_before_post_insertion_action(42, 42, False, False)
