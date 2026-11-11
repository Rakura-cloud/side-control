# SPDX-License-Identifier: GPL-3.0-or-later
"""The computer's clipboard, read for Share Clipboard (the backend runs without a Wayland session)."""

import shutil
import subprocess

from gi.repository import Gdk, GLib


WL_PASTE_TIMEOUT_S = 1.0  # wl-paste answers in milliseconds; this only bounds a stuck compositor


def send_clipboard(backend):
    """Answer a paste chord: read the clipboard's text and send it to the backend to be typed on the
    device in control (Share Clipboard), or no text when there is none to type - the chord then goes
    to the device as usual."""

    def done(text):
        backend.send(cmd="type_text", text=text or None)

    if shutil.which("wl-paste"):
        try:
            # reads the clipboard without focus on wlroots/KWin (wlr-data-control); where it fails
            # (GNOME's Mutter has no data-control protocol), the GTK clipboard below is the fallback
            out = subprocess.run(["wl-paste", "--no-newline", "--type", "text"], capture_output=True,
                                 timeout=WL_PASTE_TIMEOUT_S, text=True)
            if out.returncode == 0:
                done(out.stdout)
                return
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass

    def got(clipboard, result, _user=None):
        try:
            done(clipboard.read_text_finish(result))
        except GLib.Error:
            done(None)

    Gdk.Display.get_default().get_clipboard().read_text_async(None, got, None)
