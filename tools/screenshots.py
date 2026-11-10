# SPDX-License-Identifier: GPL-3.0-or-later
"""Render every page of Side Control to PNG against a fake backend - no Bluetooth or root needed.

Run it inside a Wayland session, or headless through tools/screenshots.sh:
    python tools/screenshots.py [output dir]   (default: docs/screenshots)
It also prints every command the window sent to the backend, which is handy to check a change.
"""
import json
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots"))
TMP = tempfile.mkdtemp(prefix="side-control-shots-")
SOCK = os.path.join(TMP, "sock")
os.environ["SIDE_CONTROL_SOCK"] = SOCK  # the window talks to serve() below instead of the backend
os.environ["XDG_CONFIG_HOME"] = TMP     # and keeps its settings out of yours
os.makedirs(os.path.join(TMP, "side-control"))
with open(os.path.join(TMP, "side-control", "settings.json"), "w") as f:
    json.dump({"hid_banner_dismissed": True}, f)

STATE = {
    "type": "state", "target": None, "inputs": ["Touchpad"], "scanning": True,
    "handoff": {"available": True, "edge": None}, "sharing": True, "locked": False,
    "nearby": None, "pairing": None,
    "devices": [
        {"address": "AA:00:00:00:00:01", "name": "My iPhone", "model": "iPhone 15 Pro", "kind": "iPhone",
         "os": "iOS", "form": "phone", "software": "18.1", "battery": 82, "connected": True,
         "handoff": {"zone": None, "require_near": False, "phone_w": 393, "phone_h": 852, "speed": 1.0},
         "signal": {"rssi": -52, "avg": -53.0, "zone": "NEAR"}},
        {"address": "AA:00:00:00:00:02", "name": "iPad Air", "model": None, "kind": "iPad",
         "os": "iPadOS", "form": "tablet", "software": None, "battery": 23, "connected": False,
         "handoff": {"zone": None, "require_near": False, "phone_w": 1180, "phone_h": 820, "speed": 1.0},
         "signal": None}]}
NEARBY = [{"address": "BB:00:00:00:00:01", "name": "Pixel Tablet", "os": "Android", "form": "tablet"},
          {"address": "BB:00:00:00:00:02", "name": "Galaxy S24", "os": "Android", "form": "phone"}]
received = []


def serve():
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(SOCK)
    srv.listen(1)
    conn, _ = srv.accept()
    send = lambda msg: conn.send((json.dumps(msg) + "\n").encode())  # noqa: E731
    send(STATE)
    buf = b""
    while data := conn.recv(4096):
        *lines, buf = (buf + data).split(b"\n")
        for line in lines:
            msg = json.loads(line)
            received.append(msg)
            if msg.get("cmd") == "discover" and msg.get("on"):
                send(dict(STATE, nearby=NEARBY, pairing=NEARBY[1]["address"]))


threading.Thread(target=serve, daemon=True).start()

from side_control.app.application import App  # noqa: E402
from side_control.app.window import PAGES, Window  # noqa: E402
from side_control.app import config as app_config  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

app_config.SHORTCUT_URL = "https://www.icloud.com/shortcuts/0123456789abcdef"  # both QR codes show


def shot(win, name):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=win).snapshot(snapshot, win.get_width(), win.get_height())
    node = snapshot.to_node()
    if node is None:  # not drawn yet
        GLib.timeout_add(300, lambda: shot(win, name) and False)
        return
    win.get_renderer().render_texture(node, None).save_to_png(os.path.join(OUT, name + ".png"))
    print("wrote", os.path.join(OUT, name + ".png"))


def close_dialog(win):
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.force_close()


def script(win):
    steps = []
    for i, (name, *_rest) in enumerate(PAGES):
        steps += [lambda i=i: win.show_page(i), lambda name=name: shot(win, name.lower())]
    steps += [lambda: win.show_add_to_iphone(), lambda: shot(win, "add-to-iphone"), lambda: close_dialog(win),
              lambda: win.show_page(0),
              lambda: win.show_guide("AA:00:00:00:00:01"), lambda: shot(win, "guide"), lambda: close_dialog(win),
              lambda: win.ask_forget("AA:00:00:00:00:02"), lambda: shot(win, "forget"), lambda: close_dialog(win),
              lambda: win.show_pair(), lambda: shot(win, "pair"), lambda: close_dialog(win),
              lambda: print("sent to the backend:", *received, sep="\n  "),
              lambda: win.get_application().quit()]

    def run():
        steps.pop(0)()
        return bool(steps)
    GLib.timeout_add(900, run)


class ScreenshotApp(App):
    def do_activate(self):
        self.window = win = Window(self)
        win.started_backend = True  # never try to start the real one
        win.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        win.present()
        GLib.timeout_add(1500, lambda: script(win) and False)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    app = ScreenshotApp()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)  # not handed to a Side Control that is already running
    app.run([])
