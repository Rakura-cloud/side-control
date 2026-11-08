# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the PC pointer is, through Hyprland's IPC socket."""

import glob
import json
import os
import socket
import time

from .config import CURSOR_MARK, RUNTIME_DIR


class Hyprland:
    """Pointer position and warping through Hyprland's IPC socket (the only compositor supported)."""

    def __init__(self):
        self.path = None
        self.rects = None
        self.rects_t = 0.0
        self.hidden = os.path.exists(CURSOR_MARK)  # a backend that was killed left it hidden

    def socket_path(self):
        if self.path and os.path.exists(self.path):
            return self.path
        socks = glob.glob(f"{RUNTIME_DIR}/hypr/*/.socket.sock")
        self.path = max(socks, key=os.path.getmtime) if socks else None
        return self.path

    @property
    def available(self):
        return self.socket_path() is not None

    def request(self, command):
        path = self.socket_path()
        if not path:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                sock.connect(path)
                sock.sendall(command.encode())
                chunks = []
                while chunk := sock.recv(65536):
                    chunks.append(chunk)
            return b"".join(chunks).decode()
        except OSError:
            self.path = None
            return None

    def cursor(self):
        try:
            pos = json.loads(self.request("j/cursorpos"))
            return pos["x"], pos["y"]
        except (TypeError, ValueError, KeyError):
            return None

    def layout(self):
        """[(left, top, right, bottom)] of every monitor in layout pixels, cached for a few seconds."""
        now = time.monotonic()
        if now - self.rects_t > 3:
            self.rects_t = now
            try:
                rects = []
                for m in json.loads(self.request("j/monitors")):
                    w, h = m["width"] / m["scale"], m["height"] / m["scale"]
                    if m.get("transform", 0) % 2:  # rotated 90/270 degrees
                        w, h = h, w
                    rects.append((m["x"], m["y"], m["x"] + round(w), m["y"] + round(h)))
                self.rects = rects or None
            except (TypeError, ValueError, KeyError):
                self.rects = None
        return self.rects

    def warp(self, x, y):
        reply = self.request(f"dispatch hl.dsp.cursor.move({{x={x:.0f},y={y:.0f}}})")
        if not (reply or "").startswith("ok"):
            self.request(f"dispatch movecursor {x:.0f} {y:.0f}")  # Hyprland before the Lua API

    def set_invisible(self, invisible):
        value = "true" if invisible else "false"
        reply = self.request(f"eval hl.config({{cursor = {{invisible = {value}}}}})")
        if not (reply or "").startswith("ok"):
            reply = self.request(f"keyword cursor:invisible {value}")  # Hyprland before the Lua config
        return (reply or "").startswith("ok")

    def hide_cursor(self, hide):
        """Hide the pointer on every monitor, or show it again. Only a pointer hidden here is shown again,
        and CURSOR_MARK remembers it was, so a backend killed meanwhile shows it on its next start."""
        if hide == self.hidden:
            return
        if hide:
            try:
                option = json.loads(self.request("j/getoption cursor:invisible"))
                if option.get("bool", option.get("int")):  # the user's own setting: leave it alone
                    return
            except (TypeError, ValueError, AttributeError):
                return
            if not self.set_invisible(True):
                return
            open(CURSOR_MARK, "w").close()
        else:
            self.set_invisible(False)
            if os.path.exists(CURSOR_MARK):
                os.unlink(CURSOR_MARK)
        self.hidden = hide
