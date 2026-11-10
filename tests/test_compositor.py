# SPDX-License-Identifier: GPL-3.0-or-later
"""The compositor interface and its implementations, against fake sockets and replies:
python -m unittest discover tests"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.backend.compositor import Compositor, Hyprland, KWin, Mutter, Niri, detect  # noqa: E402


class Detect(unittest.TestCase):
    """detect() picks the implementation from the environment the app exports into the command the
    backend's service runs."""

    def test_hyprland_from_its_signature(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc"}, clear=True):
            self.assertIsInstance(detect(), Hyprland)

    def test_niri_from_its_socket(self):
        with mock.patch.dict(os.environ, {"NIRI_SOCKET": "/run/user/1000/niri"}, clear=True):
            comp = detect()
        self.assertIsInstance(comp, Niri)
        self.assertEqual(comp.path, "/run/user/1000/niri")

    def test_kde_and_gnome_from_the_desktop(self):
        for desktop, kind in (("KDE", KWin), ("ubuntu:GNOME", Mutter), ("GNOME", Mutter)):
            with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}, clear=True):
                self.assertIsInstance(detect(), kind, desktop)

    def test_unknown_desktop(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Hyprland, "socket_path", lambda self: "/tmp/.socket.sock"):
            self.assertIsInstance(detect(), Hyprland)  # an older command: find Hyprland's socket
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Hyprland, "socket_path", lambda self: None):
            self.assertIs(type(detect()), Compositor)


class TheInterface(unittest.TestCase):
    """The contract of a compositor that can do nothing: no crash, everything says so."""

    def test_defaults(self):
        comp = Compositor()
        self.assertIsNone(comp.cursor())
        self.assertIsNone(comp.layout())
        self.assertFalse(comp.warp(0, 0))
        self.assertFalse(comp.available)
        comp.hide_cursor(True)  # a no-op


# niri's Outputs reply: {"Ok": {"Outputs": [{"logical": {x, y, width, height}}, ...]}}
NIRI_OUTPUTS = {"Ok": {"Outputs": [
    {"name": "DP-1", "logical": {"x": 0, "y": 0, "width": 3440, "height": 1440, "scale": 1.0}},
    {"name": "eDP-1", "logical": {"x": -791, "y": -1440, "width": 1280, "height": 720, "scale": 1.25}},
    {"name": "HDMI-1", "logical": None},  # not part of the layout
]}}


class NiriLayout(unittest.TestCase):
    """niri's IPC: Outputs gives the monitors (logical pixels), but no pointer position and no way
    to move the pointer."""

    def comp(self, reply):
        comp = Niri()
        comp.request = lambda _request: reply
        return comp

    def test_layout_from_the_outputs_reply(self):
        comp = self.comp(json.dumps(NIRI_OUTPUTS))
        self.assertEqual(comp.layout(), [(0, 0, 3440, 1440), (-791, -1440, 489, -720)])

    def test_a_broken_reply_is_no_layout(self):
        self.assertIsNone(self.comp('{"Err": "oh no"}').layout())
        self.assertIsNone(self.comp("not json").layout())
        self.assertIsNone(self.comp(None).layout())

    def test_no_cursor_and_no_warp(self):
        comp = self.comp(json.dumps(NIRI_OUTPUTS))
        self.assertIsNone(comp.cursor())
        self.assertFalse(comp.warp(1, 2))
        self.assertFalse(comp.available)


class NiriSocket(unittest.TestCase):
    """The wire format (the niri wiki, "IPC"): one JSON request line in, one JSON reply line out."""

    def test_one_json_line_each_way(self):
        path = os.path.join(tempfile.mkdtemp(), "niri")
        got = []
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen(1)

        def answer():
            conn, _ = srv.accept()
            got.append(conn.makefile().readline().strip())
            conn.sendall((json.dumps(NIRI_OUTPUTS) + "\n").encode())
            conn.close()

        thread = threading.Thread(target=answer)
        thread.start()
        with mock.patch.dict(os.environ, {"NIRI_SOCKET": path}):
            comp = Niri()
            self.assertEqual(comp.layout(), [(0, 0, 3440, 1440), (-791, -1440, 489, -720)])
        thread.join(2)
        srv.close()
        self.assertEqual(got, ['"Outputs"'])

    def test_no_socket_is_no_layout(self):
        with mock.patch.dict(os.environ, {"NIRI_SOCKET": "/does/not/exist"}):
            self.assertIsNone(Niri().layout())


# Mutter's GetCurrentState: (serial, monitors, logical monitors, properties); a monitor is
# ((connector, vendor, product, serial), modes, properties), a mode (id, width, height, refresh,
# preferred scale, supported scales, properties), a logical monitor (x, y, scale, transform,
# primary, monitors, properties)
MUTTER_STATE = (
    1,
    [(("DP-1", "ACME", "Wide", ""), [("DP-1:3440x1440@100", 3440, 1440, 100.0, 1.0, [1.0],
                                      {"is-current": True})], {}),
     (("eDP-1", "ACME", "Laptop", ""), [("eDP-1:1600x900@60", 1600, 900, 60.0, 1.25, [1.0, 1.25],
                                         {"is-current": True})], {}),
     (("HDMI-1", "ACME", "TV", ""), [("HDMI-1:1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0],
                                      {"is-current": True})], {})],
    [(0, 0, 1.0, 0, True, [("DP-1", "ACME", "Wide", "")], {}),
     (-791, -1440, 1.25, 0, False, [("eDP-1", "ACME", "Laptop", "")], {}),
     (3440, -960, 1.0, 1, False, [("HDMI-1", "ACME", "TV", "")], {})],  # transform 1: 90 degrees
    {"layout-mode": 1},
)


class MutterLayout(unittest.TestCase):
    """GNOME: GetCurrentState gives the logical monitors; the pointer position and moving it are
    not in Mutter's D-Bus API at all."""

    def test_layout_from_get_current_state(self):
        comp = Mutter()
        comp.state = lambda: MUTTER_STATE
        self.assertEqual(comp.layout(), [(0, 0, 3440, 1440), (-791, -1440, 489, -720), (3440, -960, 4520, 960)])
        self.assertIsNone(comp.cursor())
        self.assertFalse(comp.warp(1, 2))

    def test_no_reply_is_no_layout(self):
        comp = Mutter()
        comp.state = lambda: None
        self.assertIsNone(comp.layout())


# kscreen-doctor -j: {"outputs": [{pos: {x, y}, size: {width, height}, scale, rotation,
# connected, enabled}, ...]}; rotation 8 is Right (270 degrees)
KSCREEN = {"outputs": [
    {"name": "DP-1", "pos": {"x": 0, "y": 0}, "size": {"width": 3440, "height": 1440},
     "scale": 1, "rotation": 1, "connected": True, "enabled": True},
    {"name": "eDP-1", "pos": {"x": -791, "y": -1440}, "size": {"width": 2560, "height": 1600},
     "scale": 2, "rotation": 1, "connected": True, "enabled": True},
    {"name": "HDMI-1", "pos": {"x": 3440, "y": 0}, "size": {"width": 1920, "height": 1080},
     "scale": 1, "rotation": 8, "connected": True, "enabled": True},
    {"name": "VGA-1", "connected": False},
]}


class KWinLayout(unittest.TestCase):
    """KDE: kscreen-doctor -j gives the outputs; the pointer position and moving it have no public
    API (a KWin script would be needed)."""

    def test_layout_from_kscreen_doctor(self):
        comp = KWin()
        comp.doctor = lambda: KSCREEN
        self.assertEqual(comp.layout(), [(0, 0, 3440, 1440), (-791, -1440, 489, -640), (3440, 0, 4520, 1920)])
        self.assertIsNone(comp.cursor())
        self.assertFalse(comp.warp(1, 2))

    def test_no_tool_is_no_layout(self):
        comp = KWin()
        comp.doctor = lambda: None
        self.assertIsNone(comp.layout())


if __name__ == "__main__":
    unittest.main()
