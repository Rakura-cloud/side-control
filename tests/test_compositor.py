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
import dbus  # noqa: E402
import dbus.service  # noqa: E402
from side_control.backend.compositor import (  # noqa: E402
    CURSOR_PATH,
    CURSOR_SCRIPT,
    SCRIPT_PLUGIN,
    SHELL_TIMEOUT_S,
    Compositor,
    CursorReceiver,
    Hyprland,
    KWin,
    Mutter,
    Niri,
    detect,
)


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


class FakeShell:
    """A fake io.github.sidecontrol.Shell, what the GNOME Shell extension serves on the session
    bus: GetCursor and Warp, recording what Warp got. present=False stands for the extension not
    being installed, which is what every call looks like then."""

    def __init__(self, cursor=(640, 400), present=True):
        self.cursor, self.present, self.warped = cursor, present, None

    def get_object(self, name, path):
        return self

    def get_dbus_method(self, method, interface):
        def call(*args, timeout=None):
            self.timeout = timeout
            if not self.present:
                raise dbus.DBusException("no such name")
            if method == "GetCursor":
                return self.cursor
            self.warped = args
        return call


class MutterLayout(unittest.TestCase):
    """GNOME: GetCurrentState gives the logical monitors; the pointer position and moving it come
    from the GNOME Shell extension (MutterExtension below)."""

    def test_layout_from_get_current_state(self):
        comp = Mutter()
        comp.state = lambda: MUTTER_STATE
        self.assertEqual(comp.layout(), [(0, 0, 3440, 1440), (-791, -1440, 489, -720), (3440, -960, 4520, 960)])
        with mock.patch("side_control.backend.compositor.dbus.SessionBus",
                        return_value=FakeShell(present=False)):  # the extension is not running
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


class MutterExtension(unittest.TestCase):
    """GNOME: cursor() and warp() go through the Side Control GNOME Shell extension
    (data/gnome-shell/...), and fall back to None and False when it is not running."""

    def test_cursor_and_warp_through_the_extension(self):
        shell = FakeShell()
        comp = Mutter()
        with mock.patch("side_control.backend.compositor.dbus.SessionBus", return_value=shell):
            self.assertEqual(comp.cursor(), (640, 400))
            self.assertEqual(shell.timeout, SHELL_TIMEOUT_S)  # a stuck Shell must not stall input
            self.assertTrue(comp.warp(100.4, 200.6))
            self.assertEqual(shell.warped, (100, 201))
            self.assertTrue(comp.available)

    def test_without_the_extension(self):
        comp = Mutter()
        with mock.patch("side_control.backend.compositor.dbus.SessionBus",
                        return_value=FakeShell(present=False)):
            self.assertIsNone(comp.cursor())
            self.assertFalse(comp.warp(1, 2))
            self.assertFalse(comp.available)


class FakeKWinObject:
    """A fake D-Bus object of FakeKWin: get_dbus_method returns a recorder that answers, or
    raises where the real KWin would not answer."""

    def __init__(self, kwin, path):
        self.kwin, self.path = kwin, path

    def get_dbus_method(self, method, interface):
        def call(*args):
            self.kwin.calls.append((self.path, method, args))
            return self.kwin.answer(self.path, method, args)
        return call


class FakeKWin:
    """A fake session bus with org.kde.KWin on it: Scripting's loadScript / unloadScript and the
    script object's run, recording every call. run_on is where run() answers (KWin 6's
    /Scripting/Script<id>, KWin 5's /<id>); running=False stands for KWin not being there."""

    def __init__(self, run_on=("/Scripting/Script7", "/7"), running=True):
        self.run_on, self.running = run_on, running
        self.calls = []
        self.exported = None  # the object path the cursor receiver was exported at
        self._bus_names = {}

    # the connection side of the bus (what dbus.service.BusName and Object touch)
    def request_name(self, name, flags):
        return dbus.service._dbus_bindings.REQUEST_NAME_REPLY_PRIMARY_OWNER

    def release_name(self, name):
        pass

    def _register_object_path(self, path, message_cb, unregister_cb, fallback):
        self.exported = path

    # the org.kde.KWin side
    def get_object(self, name, path):
        return FakeKWinObject(self, path)

    def answer(self, path, method, args):
        if not self.running:
            raise dbus.DBusException("no KWin here")
        if method == "loadScript":
            return 7
        if method == "run" and path not in self.run_on:
            raise dbus.DBusException(f"no script at {path}")
        return True  # what unloadScript replies


class CursorReceiverReports(unittest.TestCase):
    """The session-bus receiver the KWin script calls: Report is what cursor() returns."""

    def test_reports_update_the_cursor(self):
        comp = KWin()
        receiver = CursorReceiver(comp, FakeKWin())
        receiver.Report("640", "400.4")  # what the script sends: strings, see its comment
        self.assertEqual(comp.cursor(), (640, 400))
        receiver.Report("not a number", "400")  # ignored: the cursor stays
        self.assertEqual(comp.cursor(), (640, 400))


class KWinCursorScript(unittest.TestCase):
    """The KWin cursor script over a fake Scripting D-Bus object: loaded and run at start(),
    unloaded at exit, and cursor() is what its receiver reports."""

    def start(self, **kwargs):
        kwin = FakeKWin(**kwargs)
        comp = KWin()
        with mock.patch("side_control.backend.compositor.dbus.SessionBus", return_value=kwin):
            comp.start()
        return comp, kwin

    def test_load_and_run(self):
        comp, kwin = self.start()
        self.assertEqual(kwin.calls, [("/Scripting", "unloadScript", (SCRIPT_PLUGIN,)),  # a stale one
                                      ("/Scripting", "loadScript", (CURSOR_SCRIPT, SCRIPT_PLUGIN)),
                                      ("/Scripting/Script7", "run", ())])  # KWin 6's script object
        self.assertEqual(kwin.exported, CURSOR_PATH)  # what the script reports to
        self.assertEqual(comp.script, 7)
        self.assertTrue(comp.available)

    def test_kwin_5_runs_the_script_from_its_own_path(self):
        comp, kwin = self.start(run_on=("/7",))
        self.assertEqual(kwin.calls[-1], ("/7", "run", ()))
        self.assertEqual(comp.script, 7)

    def test_unload_at_exit(self):
        comp, kwin = self.start()
        comp.unload_script()  # what atexit runs
        self.assertEqual(kwin.calls[-1], ("/Scripting", "unloadScript", (SCRIPT_PLUGIN,)))

    def test_a_script_that_would_not_run_is_unloaded(self):
        comp, kwin = self.start(run_on=())
        self.assertIsNone(comp.script)
        self.assertFalse(comp.available)
        self.assertEqual(kwin.calls[-1], ("/Scripting", "unloadScript", (SCRIPT_PLUGIN,)))

    def test_no_kwin_is_no_script(self):
        comp, kwin = self.start(running=False)
        self.assertIsNone(comp.script)
        self.assertFalse(comp.available)
        self.assertIsNone(comp.cursor())  # nothing was ever reported

    def test_reports_reach_the_cursor(self):
        comp, _kwin = self.start()
        comp.reports.Report("10", "20")  # the script's call, as the receiver gets it
        self.assertEqual(comp.cursor(), (10, 20))


if __name__ == "__main__":
    unittest.main()
