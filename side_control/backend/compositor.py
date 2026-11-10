# SPDX-License-Identifier: GPL-3.0-or-later
"""The desktop compositor: the monitor layout, where the PC pointer is, moving it and hiding it.

detect() picks the implementation for the session from HYPRLAND_INSTANCE_SIGNATURE, NIRI_SOCKET and
XDG_CURRENT_DESKTOP, which the app exports into the command the backend's service runs."""

import atexit
import glob
import json
import os
import socket
import subprocess
import time

import dbus
import dbus.service

from .config import CURSOR_MARK, RUNTIME_DIR


CURSOR_SERVICE = "io.github.sidecontrol.Cursor"  # the KWin script's D-Bus target, served here
CURSOR_PATH = "/io/github/sidecontrol/Cursor"
CURSOR_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "kwin", "side-control-cursor.js")  # shipped with the backend
SCRIPT_PLUGIN = "side-control-cursor"  # the script's plugin name for KWin's loadScript/unloadScript
SHELL_SERVICE = "io.github.sidecontrol.Shell"  # the GNOME Shell extension's D-Bus service
SHELL_PATH = "/io/github/sidecontrol/Shell"


class Compositor:
    """What Handoff needs from the desktop compositor. The contract: everything is best-effort and
    says what it cannot do, instead of failing.

    - layout(): [(left, top, right, bottom)] of every monitor in layout pixels (logical pixels, the
      coordinates the compositor lays the screens out in), or None when the layout is unknown.
    - cursor(): (x, y) of the PC pointer in layout pixels, or None where the compositor cannot say:
      Wayland leaves the pointer to the compositor, and only a few expose it. Hand-off by pushing
      the cursor off an edge is off then; the hotkey and the app still switch.
    - warp(x, y): move the PC pointer to (x, y); True when the compositor took it, False where it
      cannot be told to move the pointer.
    - hide_cursor(hide): hide the pointer on every monitor (hide=True), or show the one that was
      hidden here again. A no-op where it cannot be done.
    - start(): set up what this compositor needs before the first cursor() (KWin loads its cursor
      script); undone again at exit.
    - available: whether the PC pointer can be located right now - edge hand-off needs no more,
      moving it back is warp()'s job.

    detect() below returns the subclass for the running desktop."""

    name = "an unknown compositor"

    def start(self):
        pass

    @property
    def available(self):
        return False

    def cursor(self):
        return None

    def layout(self):
        return None

    def warp(self, x, y):
        return False

    def hide_cursor(self, hide):
        pass


class Hyprland(Compositor):
    """Hyprland, over its IPC socket: the only compositor that says where the PC pointer is, moves
    it and hides it."""

    name = "Hyprland"

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
            reply = self.request(f"dispatch movecursor {x:.0f} {y:.0f}")  # Hyprland before the Lua API
        return (reply or "").startswith("ok")

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


class Niri(Compositor):
    """niri, over its IPC socket: one JSON request line in, one JSON reply line out, on $NIRI_SOCKET
    (the niri wiki, "IPC").

    The IPC has no pointer position and nothing to move the pointer with (checked against niri-ipc
    26.04: Request has no such variant, Action no such action), so cursor() stays None and warp()
    does not move anything: edge hand-off is off, the hotkey and the app still switch."""

    name = "niri"

    def __init__(self):
        self.path = os.environ.get("NIRI_SOCKET") or None
        self.rects = None
        self.rects_t = 0.0

    def request(self, request):
        """One request (it goes on the wire as one JSON line), the reply line as text; None if niri
        is not there."""
        if not self.path:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                sock.connect(self.path)
                sock.sendall(json.dumps(request).encode() + b"\n")
                chunks = []
                while chunk := sock.recv(65536):
                    chunks.append(chunk)
                    if chunk.endswith(b"\n"):
                        break
            return b"".join(chunks).decode()
        except OSError:
            return None

    def layout(self):
        """[(left, top, right, bottom)] of every monitor in layout pixels, cached for a few seconds.
        Outputs: {"Ok": {"Outputs": [{"logical": {"x", "y", "width", "height"}, ...}, ...]}}, with
        "logical": null for a monitor that is not part of the layout."""
        now = time.monotonic()
        if now - self.rects_t > 3:
            self.rects_t = now
            try:
                rects = []
                for output in json.loads(self.request("Outputs"))["Ok"]["Outputs"]:
                    logical = output.get("logical")
                    if logical:
                        x, y = logical["x"], logical["y"]
                        rects.append((x, y, x + logical["width"], y + logical["height"]))
                self.rects = rects or None
            except (TypeError, ValueError, KeyError):
                self.rects = None
        return self.rects


class Mutter(Compositor):
    """GNOME's Mutter, over D-Bus.

    GetCurrentState (org.gnome.Mutter.DisplayConfig) reports the logical monitors, which is the
    layout. The pointer position and moving it have no public API - Wayland leaves both to the
    shell - so both go through the Side Control GNOME Shell extension
    (data/gnome-shell/side-control@sidecontrol.github.io/), which serves io.github.sidecontrol.Shell
    on the session bus: GetCursor (global.get_pointer) and Warp (Clutter's seat warp, which injects
    absolute motion and so works on Wayland). Installing it is the user's choice (the app's
    settings); without it cursor() stays None and warp() does not move anything."""

    name = "GNOME (Mutter)"

    def __init__(self):
        self.rects = None
        self.rects_t = 0.0

    def shell(self, method):
        """One method of the GNOME Shell extension's D-Bus object. get_object is lazy, so the
        absence of the extension only shows when the method is called: the callers catch that."""
        try:
            bus = dbus.SessionBus()
            return bus.get_object(SHELL_SERVICE, SHELL_PATH).get_dbus_method(method, SHELL_SERVICE)
        except dbus.DBusException:
            return None

    def cursor(self):
        """(x, y) of the PC pointer from the extension; None when it is not running."""
        try:
            x, y = self.shell("GetCursor")()
            return int(x), int(y)
        except (dbus.DBusException, TypeError, ValueError):
            return None

    def warp(self, x, y):
        """Move the PC pointer through the extension; False when it is not running."""
        try:
            self.shell("Warp")(int(round(x)), int(round(y)))
            return True
        except (dbus.DBusException, TypeError):
            return False

    @property
    def available(self):
        return self.cursor() is not None  # the extension answers

    def state(self):
        """Mutter's GetCurrentState reply, or None if its D-Bus is not there."""
        try:
            bus = dbus.SessionBus()
            config = bus.get_object("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig")
            return config.get_dbus_method("GetCurrentState", "org.gnome.Mutter.DisplayConfig")()
        except dbus.DBusException:
            return None

    def layout(self):
        """[(left, top, right, bottom)] of every logical monitor in layout pixels, cached for a few
        seconds. GetCurrentState is (serial, monitors, logical monitors, properties): a monitor is
        ((connector, ...), modes, properties), a mode (id, width, height, ...), and a logical
        monitor (x, y, scale, transform, primary, monitors, properties)."""
        now = time.monotonic()
        if now - self.rects_t > 3:
            self.rects_t = now
            self.rects = self._rects(self.state())
        return self.rects

    def _rects(self, state):
        try:
            _serial, monitors, logical, props = state
            scaled = props.get("layout-mode", 1) != 2  # 2 = physical: the modes as they are
            sizes = {}
            for (connector, _vendor, _product, _serial), modes, _props in monitors:
                current = next((m for m in modes if m[6].get("is-current")), modes[0])
                sizes[connector] = (current[1], current[2])
            rects = []
            for x, y, scale, transform, _primary, on, _props in logical:
                w, h = sizes.get(on[0][0], (0, 0))
                if transform % 2:  # 90 / 270 degrees, also flipped
                    w, h = h, w
                if scaled and scale:
                    w, h = w / scale, h / scale
                if w and h:
                    rects.append((x, y, x + round(w), y + round(h)))
            return rects or None
        except (TypeError, ValueError, KeyError, IndexError):
            return None


class CursorReceiver(dbus.service.Object):
    """The session-bus service the KWin script reports the pointer position to. Wayland gives
    clients no pointer position, so the script pushes every change here."""

    def __init__(self, comp, bus):
        super().__init__(bus, CURSOR_PATH)
        self.comp = comp

    @dbus.service.method(CURSOR_SERVICE, in_signature="ss", out_signature="")
    def Report(self, x, y):
        try:
            self.comp.report(float(x), float(y))
        except ValueError:
            pass


class KWin(Compositor):
    """KDE's KWin.

    layout(): `kscreen-doctor -j` prints libkscreen's view of the output configuration: every
    output's position and size.

    cursor(): a KWin script (data/kwin/side-control-cursor.js), loaded over D-Bus at start(),
    reports workspace.cursorPos to this backend's session-bus service whenever it changes;
    cursor() is the last report. Wayland gives clients no pointer position, and KWin shows it to
    scripts only.

    warp(): KWin cannot move the pointer. workspace.cursorPos is read-only (KWin 5 and 6 alike,
    scripting/workspace_wrapper.h), KWin's D-Bus (org.kde.KWin at /KWin, src/dbusinterface.h) has
    no pointer method, and Cursor::setPos is internal C++; only the RemoteDesktop portal moves the
    pointer, relatively and behind a permission dialog. So warp() does not move anything and
    hand-off's leave() keeps the "control returned" log."""

    name = "KDE (KWin)"

    def __init__(self):
        self.rects = None
        self.rects_t = 0.0
        self.pos = None  # the last position the script reported
        self.script = None  # the loaded script's id, None while it is not loaded

    def start(self):
        """Serve the script's reports and load the cursor script into KWin; unloaded at exit."""
        try:
            self.bus = dbus.SessionBus()
            self.bus_name = dbus.service.BusName(CURSOR_SERVICE, self.bus)  # what the script reports to
            self.reports = CursorReceiver(self, self.bus)
            self.script = self.load_script()
        except dbus.DBusException:
            self.script = None
        if self.script is not None:
            atexit.register(self.unload_script)

    def load_script(self):
        """Load the cursor script into KWin and start it, as its script id; None when KWin is not
        there or would not take it. KWin 6 runs the script object at /Scripting/Script<id>, KWin 5
        at /<id> (scripting/scripting.cpp)."""
        try:
            scripting = self.bus.get_object("org.kde.KWin", "/Scripting")
            load = scripting.get_dbus_method("loadScript", "org.kde.kwin.Scripting")
            script = load(CURSOR_SCRIPT, SCRIPT_PLUGIN)
        except dbus.DBusException:
            return None
        for path in (f"/Scripting/Script{script}", f"/{script}"):
            try:
                self.bus.get_object("org.kde.KWin", path).get_dbus_method("run", "org.kde.kwin.Script")()
                return script
            except dbus.DBusException:
                pass
        self.unload_script()  # loaded, but it would not run
        return None

    def unload_script(self):
        """Take the cursor script out of KWin again."""
        try:
            scripting = self.bus.get_object("org.kde.KWin", "/Scripting")
            scripting.get_dbus_method("unloadScript", "org.kde.kwin.Scripting")(SCRIPT_PLUGIN)
        except dbus.DBusException:
            pass

    def report(self, x, y):
        """Where the script says the pointer is (strings in, as the script sends them: callDBus
        marshals JS numbers as int or double depending on the KWin version)."""
        self.pos = (round(x), round(y))

    def cursor(self):
        return self.pos

    @property
    def available(self):
        return self.script is not None  # the reports flow once the script is loaded

    def doctor(self):
        """kscreen-doctor -j's configuration as a dict, or None if the tool is not there."""
        try:
            run = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=1)
            return json.loads(run.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def layout(self):
        """[(left, top, right, bottom)] of every enabled output in layout pixels, cached for a few
        seconds. kscreen-doctor -j: {"outputs": [{"pos": {"x", "y"}, "size": {"width", "height"},
        "scale", "rotation", "connected", "enabled"}, ...]}."""
        now = time.monotonic()
        if now - self.rects_t > 3:
            self.rects_t = now
            self.rects = self._rects(self.doctor())
        return self.rects

    def _rects(self, config):
        try:
            rects = []
            for output in config.get("outputs", []):
                if not (output.get("connected") and output.get("enabled", True)):
                    continue
                x, y = output["pos"]["x"], output["pos"]["y"]
                w, h = output["size"]["width"], output["size"]["height"]
                if output.get("scale", 1) > 0:
                    w, h = w / output["scale"], h / output["scale"]
                if output.get("rotation", 1) in (2, 8, 32, 128):  # 90 / 270 degrees: Left, Right, flipped
                    w, h = h, w
                rects.append((x, y, x + round(w), y + round(h)))
            return rects or None
        except (AttributeError, TypeError, ValueError, KeyError):
            return None


def detect():
    """The compositor this session runs, from the environment the app exports into the command the
    backend's service runs. An unknown desktop falls back to finding Hyprland's socket, as this did
    before there was an interface to pick."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return Hyprland()
    if os.environ.get("NIRI_SOCKET"):
        return Niri()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop:  # "KDE", "ubuntu:KDE", ...
        return KWin()
    if "gnome" in desktop:
        return Mutter()
    hyprland = Hyprland()
    return hyprland if hyprland.available else Compositor()
