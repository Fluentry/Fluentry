#!/usr/bin/env python3
"""Send Ctrl+V through libei and see whether the compositor delivers it.

ydotool's key events reach the kernel and mutter reads its device, but the
focused window never acts on them. libei is the input path GNOME actually
sanctions: the compositor creates the virtual device itself, so events
arrive as input it already trusts rather than as a uinput device it may
choose to ignore.

Run it with the cursor in a text field. Whatever is on the clipboard should
be pasted.

    python scripts/try_libei_paste.py
"""

from __future__ import annotations

import select
import sys
import time

from libei import ei, oeffis

KEY_LEFTCTRL = 29
KEY_V = 47
#: A chord sent with no gap can arrive before the modifier has been applied.
CHORD_GAP_SECONDS = 0.02


def main() -> int:
    print("requesting input permission through the RemoteDesktop portal...")
    session = oeffis.Oeffis.create(devices=oeffis.DeviceType.KEYBOARD)
    deadline = time.monotonic() + 90
    while True:
        if time.monotonic() > deadline:
            print("timed out waiting for consent")
            return 1
        select.select([session.fd], [], [], 5)
        try:
            if session.dispatch():
                break
        except Exception as error:
            print(f"portal refused: {type(error).__name__}: {error}")
            return 1
    print("permission granted")

    sender = ei.Sender.create_for_fd(session.eis_fd, name="fluentry")
    device = None
    deadline = time.monotonic() + 20
    while device is None and time.monotonic() < deadline:
        select.select([sender.fd], [], [], 2)
        sender.dispatch()
        for event in sender.events:
            if event.event_type is ei.EventType.SEAT_ADDED:
                event.seat.bind((ei.DeviceCapability.KEYBOARD,))
            elif event.event_type is ei.EventType.DEVICE_RESUMED:
                # A device arrives paused; only a resumed one accepts input.
                device = event.device
                break
    if device is None:
        print("the compositor never resumed a keyboard device")
        return 1
    print(f"device ready: {device.name!r}")

    print("sending Ctrl+V in 3 seconds — put your cursor in a text field")
    for remaining in (3, 2, 1):
        print(f"  {remaining}...")
        time.sleep(1)

    # Each frame() commits one logical hardware event, so the modifier and
    # the key it modifies are separate frames with a gap between them.
    device.start_emulating()
    device.keyboard_key(KEY_LEFTCTRL, True).frame()
    time.sleep(CHORD_GAP_SECONDS)
    device.keyboard_key(KEY_V, True).frame()
    time.sleep(CHORD_GAP_SECONDS)
    device.keyboard_key(KEY_V, False).frame()
    time.sleep(CHORD_GAP_SECONDS)
    device.keyboard_key(KEY_LEFTCTRL, False).frame()
    device.stop_emulating()
    # The events are queued on the connection; give it a moment to flush
    # before the session is torn down by the interpreter exiting.
    time.sleep(0.5)
    print("sent. Did the clipboard paste?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
