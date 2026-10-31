# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the PC pointer is, through Hyprland's IPC socket."""

import glob
import json
import os
import socket
import time


class Hyprland:
    """Pointer position and warping through Hyprland's IPC socket (the only compositor supported)."""

    def __init__(self):
        uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        self.runtime = f"/run/user/{uid}" if uid else None
        self.path = None
        self.box = None
        self.box_t = 0.0

    def socket_path(self):
        if self.path and os.path.exists(self.path):
            return self.path
        socks = glob.glob(f"{self.runtime}/hypr/*/.socket.sock") if self.runtime else []
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
        """(left, top, right, bottom) of all monitors in layout pixels, cached for a few seconds."""
        now = time.monotonic()
        if now - self.box_t > 3:
            self.box_t = now
            try:
                boxes = []
                for m in json.loads(self.request("j/monitors")):
                    w, h = m["width"] / m["scale"], m["height"] / m["scale"]
                    if m.get("transform", 0) % 2:  # rotated 90/270 degrees
                        w, h = h, w
                    boxes.append((m["x"], m["y"], m["x"] + round(w), m["y"] + round(h)))
                self.box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes))
            except (TypeError, ValueError, KeyError):
                self.box = None
        return self.box

    def warp(self, x, y):
        reply = self.request(f"dispatch hl.dsp.cursor.move({{x={x:.0f},y={y:.0f}}})")
        if not (reply or "").startswith("ok"):
            self.request(f"dispatch movecursor {x:.0f} {y:.0f}")  # Hyprland before the Lua API
