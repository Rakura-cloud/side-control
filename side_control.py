#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Side Control: GTK4 / libadwaita front end for side_control_backend.py.

Run as your normal user:  python side_control.py   (--hidden: start without a window, for autostart)
side_control_backend.py needs root; when it is not running, Side Control starts it through pkexec and talks to
it over its unix socket (see Control in side_control_backend.py).
"""
import ctypes.util
import json
import math
import os
import shlex
import socket
import sys

# gtk4-layer-shell (the on-screen edge indicator) must be loaded before libwayland
LAYER_SHELL = ctypes.util.find_library("gtk4-layer-shell")
if LAYER_SHELL and os.environ.get("WAYLAND_DISPLAY") and LAYER_SHELL not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = " ".join(filter(None, [LAYER_SHELL, os.environ.get("LD_PRELOAD")]))
    os.execv(sys.executable, [sys.executable, *sys.argv])

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell  # noqa: E402
except (ValueError, ImportError):
    Gtk4LayerShell = None

APP_ID = APP_ICON = "io.github.sidecontrol.SideControl"  # icons/<APP_ICON>.svg, and -symbolic for the tray
VERSION = "0.1.0"
ISSUES_URL = "https://github.com/Rakura-cloud/side-control/issues"  # where "Report an Issue" leads
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "side_control_backend.py")
SOCK_PATH = os.environ.get("SIDE_CONTROL_SOCK", "/run/side-control.sock")
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "side-control")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
AUTOSTART_PATH = os.path.join(GLib.get_user_config_dir(), "autostart", APP_ID + ".desktop")
NO_INPUT_CONF = os.path.join(HERE, "bluetooth-no-input.conf")
ICONS_DIR = os.path.join(HERE, "icons")  # the app icons, and the design's Material Symbols Rounded as sc-*
DROPIN_DIR = "/etc/systemd/system/bluetooth.service.d"
HID_DROPIN = os.path.join(DROPIN_DIR, "side-control-no-input.conf")

DEFAULTS = {"share_cursor": True, "share_clipboard": True, "switch_delay_ms": 150, "corner_dead_zones": True,
            "match_speed": True, "three_finger_switch": True, "shake_to_locate": False, "tap_to_click": True,
            "indicator": "near", "indicator_within": 48, "indicator_names": True,
            "run_in_background": True, "visible": False, "device_name": None, "adapter": None,
            "hid_banner_dismissed": False}
DEVICE_DEFAULTS = {"enabled": True, "edge": None, "x": None, "y": None}
BACKEND_OPTIONS = ("share_cursor", "switch_delay_ms", "three_finger_switch", "shake_to_locate", "tap_to_click")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

# Arrangement canvas, in canvas pixels: this computer's screen in the middle, devices around it
CANVAS_W, CANVAS_H = 640, 340
SX, SY, SW, SH = 210, 102, 220, 136
TILE = {"phone": (46, 86), "tablet": (100, 70)}
GAP = 6
CORNER_FRAC = 0.3  # same as side_control_backend.py: a corner zone covers this fraction of each edge
DEAD_PX = 6        # "Corner Dead Zones": side zones stop this far short of the screen corners
BASE_SCREEN = 400 * 860  # iPhone screen in mouse counts: "Match Pointer Speed" scales others to it

CSS = """
.arrangement {
  border-radius: 12px;
  background-color: alpha(currentColor, 0.04);
  background-image: radial-gradient(circle, alpha(currentColor, 0.10) 1px, transparent 1.5px);
  background-size: 16px 16px;
  box-shadow: inset 0 0 0 1px alpha(currentColor, 0.08);
}
.screen-tile {
  border-radius: 8px;
  background-color: alpha(@accent_bg_color, 0.22);
  box-shadow: inset 0 0 0 2px @accent_bg_color;
}
.screen-tile image { color: @accent_bg_color; }
.device-tile {
  border-radius: 10px;
  background-color: @card_bg_color;
  box-shadow: inset 0 0 0 2px alpha(currentColor, 0.22);
}
.device-tile.inactive { box-shadow: inset 0 0 0 2px alpha(currentColor, 0.08); }
.device-tile.dragging { box-shadow: 0 0 0 2px @accent_bg_color, 0 10px 24px alpha(black, 0.4); }
.device-tile label { font-size: 11px; font-weight: bold; }
.edge-indicator { border-radius: 2px; background-color: @accent_bg_color; }
.device-row { min-height: 64px; }
.step-number {
  min-width: 24px; min-height: 24px; border-radius: 99px;
  background-color: alpha(currentColor, 0.12); font-weight: bold; font-size: 13px;
}
.crumb { padding: 3px 8px; border-radius: 6px; background-color: alpha(currentColor, 0.1);
         font-weight: bold; font-size: 13px; }
.version { padding: 2px 20px 14px; }
window.edge-overlay { background: none; }
.sc-sidebar image { -gtk-icon-size: 19px; }
.icon-20 image, image.icon-20 { -gtk-icon-size: 20px; }
"""


# --- settings ----------------------------------------------------------------

def load_settings():
    """{option: value, ..., "devices": {address: {enabled, edge, x, y}}}"""
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = None
    if not isinstance(saved, dict):
        saved = {}
    settings = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    devices = saved.get("devices") if isinstance(saved.get("devices"), dict) else {}
    settings["devices"] = {addr: {**DEVICE_DEFAULTS, **{k: v for k, v in cfg.items() if k in DEVICE_DEFAULTS}}
                           for addr, cfg in devices.items() if isinstance(cfg, dict)}
    return settings


def save_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


def set_autostart(on):
    if not on:
        try:
            os.unlink(AUTOSTART_PATH)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
    with open(AUTOSTART_PATH, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Side Control\n"
                f"Exec={shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} --hidden\n"
                f"Icon={os.path.join(ICONS_DIR, APP_ICON + '.svg')}\nX-GNOME-Autostart-enabled=true\n")


def hid_owned():
    """True if bluetoothd runs without its input plugin (so side_control_backend.py can own HID), None if
    bluetoothd is not running."""
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != "bluetoothd":
                    continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().decode(errors="replace").split("\0")
        except OSError:
            continue
        for i, arg in enumerate(args):
            if arg in ("-P", "--noplugin"):
                value = args[i + 1] if i + 1 < len(args) else ""
            elif arg.startswith("--noplugin="):
                value = arg.split("=", 1)[1]
            elif arg.startswith("-P"):
                value = arg[2:]
            else:
                continue
            if "input" in value.split(","):
                return True
        return False
    return None


def no_input_dropins():
    """bluetooth.service drop-ins that start bluetoothd with -P input."""
    found = []
    try:
        names = sorted(os.listdir(DROPIN_DIR))
    except OSError:
        return found
    for name in names:
        path = os.path.join(DROPIN_DIR, name)
        try:
            with open(path) as f:
                if name.endswith(".conf") and "-P input" in f.read():
                    found.append(path)
        except OSError:
            pass
    return found


def lookup_ids(table, vendor, product):
    """Product (or vendor) name from /usr/share/hwdata/usb.ids or pci.ids."""
    try:
        with open(f"/usr/share/hwdata/{table}", encoding="utf-8", errors="replace") as f:
            vendor_name = None
            for line in f:
                if vendor_name is None:
                    if line.startswith(vendor + "  "):
                        vendor_name = line[len(vendor):].strip()
                elif line.startswith("\t" + product + "  "):
                    return line.strip()[len(product):].strip()
                elif line[:1] not in ("\t", "#", "\n"):
                    break
            return vendor_name
    except OSError:
        return None


def adapter_label(hci):
    """e.g. "hci0 · QCA61x4 Bluetooth 4.0" """
    try:
        with open(f"/sys/class/bluetooth/{hci}/device/uevent") as f:
            uevent = dict(line.strip().split("=", 1) for line in f if "=" in line)
    except OSError:
        return hci
    name = None
    if "PRODUCT" in uevent:  # USB: PRODUCT=cf3/e300/1
        vendor, product = (v.zfill(4) for v in uevent["PRODUCT"].split("/")[:2])
        name = lookup_ids("usb.ids", vendor, product)
    elif "PCI_ID" in uevent:  # PCI_ID=8086:A0F0
        vendor, product = uevent["PCI_ID"].lower().split(":")
        name = lookup_ids("pci.ids", vendor, product)
    return f"{hci} · {name or uevent.get('DRIVER', 'Bluetooth')}"


def device_icon(form):
    return "sc-tablet-android-symbolic" if form == "tablet" else "sc-smartphone-symbolic"


def battery_icon(percent):
    return f"sc-battery-{5 if percent > 60 else 3 if percent > 30 else 1}-bar-symbolic"


def hyprland(command):
    """Send one command to Hyprland's IPC socket; None if Hyprland is not there."""
    sig, runtime = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"), os.environ.get("XDG_RUNTIME_DIR")
    if not (sig and runtime):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            sock.connect(f"{runtime}/hypr/{sig}/.socket.sock")
            sock.sendall(command.encode())
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
            return reply.decode(errors="replace")
    except OSError:
        return None


# --- connection to side_control_backend.py ----------------------------------------------

class Backend:
    """Connection to side_control_backend.py's control socket. Reconnects by itself."""

    def __init__(self, on_message, on_status):
        self.on_message, self.on_status = on_message, on_status
        self.sock = None
        self.buf = b""
        self.reason = None
        GLib.timeout_add_seconds(2, self._retry)
        self._retry()

    def _retry(self):
        if self.sock is None:
            self._connect()
        return GLib.SOURCE_CONTINUE

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(SOCK_PATH)
        except OSError as err:
            sock.close()
            reason = ("not running" if isinstance(err, (FileNotFoundError, ConnectionRefusedError))
                      else str(err))
            if reason != self.reason:
                self.reason = reason
                self.on_status(False, reason)
            return
        sock.setblocking(False)
        self.sock, self.buf, self.reason = sock, b"", None
        GLib.io_add_watch(sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._readable)
        self.on_status(True, None)

    def _readable(self, fd, condition):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            data = b""
        if not data:
            self.sock.close()
            self.sock = None
            self.reason = "not running"
            self.on_status(False, self.reason)
            return GLib.SOURCE_REMOVE
        *lines, self.buf = (self.buf + data).split(b"\n")
        for line in lines:
            try:
                self.on_message(json.loads(line))
            except ValueError:
                pass
        return GLib.SOURCE_CONTINUE

    def send(self, **msg):
        if self.sock:
            try:
                self.sock.send((json.dumps(msg) + "\n").encode())
            except OSError:
                pass


# --- arrangement geometry (ported from the design) ----------------------------

def snap(form, cx, cy):
    """Where a tile dropped with its centre at (cx, cy) settles: (edge, x, y)."""
    w, h = TILE[form]
    out_x, out_y = cx < SX or cx > SX + SW, cy < SY or cy > SY + SH
    if out_x and out_y:
        right, bottom = cx > SX + SW, cy > SY + SH
        return (("bottom" if bottom else "top") + "-" + ("right" if right else "left"),
                SX + SW + 2 if right else SX - 2 - w, SY + SH + 2 if bottom else SY - 2 - h)
    nearest = lambda v, opts: min(opts, key=lambda o: abs(o - v))  # noqa: E731
    dx, dy = (cx - (SX + SW / 2)) / SW, (cy - (SY + SH / 2)) / SH
    if abs(dx) >= abs(dy):
        edge = "left" if dx < 0 else "right"
        return (edge, SX - GAP - w if edge == "left" else SX + SW + GAP,
                nearest(cy - h / 2, [SY, SY + (SH - h) / 2, SY + SH - h]))
    edge = "top" if dy < 0 else "bottom"
    return (edge, nearest(cx - w / 2, [SX, SX + (SW - w) / 2, SX + SW - w]),
            SY - GAP - h if edge == "top" else SY + SH + GAP)


def free_spot(form, edge, others):
    """Default place for a new device on `edge`, clear of the tiles in `others` [(form, edge, x, y)]."""
    if "-" in edge:
        vertical, horizontal = edge.split("-")
        return snap(form, 0 if horizontal == "left" else CANVAS_W, 0 if vertical == "top" else CANVAS_H)
    centre = {"left": (0, CANVAS_H / 2), "right": (CANVAS_W, CANVAS_H / 2),
              "top": (CANVAS_W / 2, 0), "bottom": (CANVAS_W / 2, CANVAS_H)}[edge]
    edge, x, y = snap(form, *centre)
    vert = edge in ("left", "right")
    length = TILE[form][1] if vert else TILE[form][0]
    lo, hi = (SY, SY + SH) if vert else (SX, SX + SW)
    segs = [(oy, oy + TILE[f][1]) if vert else (ox, ox + TILE[f][0]) for f, e, ox, oy in others if e == edge]
    free = lambda v: (all(v + length + GAP <= a or v >= b + GAP for a, b in segs)  # noqa: E731
                      and min(v + length, hi) - max(v, lo) >= 28)
    v = y if vert else x
    if not free(v):
        options = [c for a, b in segs for c in (a - length - GAP, b + GAP) if free(c)]
        if options:
            v = min(options, key=lambda c: abs(c - v))
    return (edge, x, v) if vert else (edge, v, y)


def spread(tiles, edge):
    """Centre every tile on a side edge as one row, in their current order. tiles: [(form, cfg)]"""
    vert = edge in ("left", "right")
    key, i = ("y", 1) if vert else ("x", 0)
    row = sorted(tiles, key=lambda t: t[1][key] + TILE[t[0]][i] / 2)
    total = sum(TILE[f][i] for f, _ in row) + GAP * (len(row) - 1)
    v = (SY + SH / 2 if vert else SX + SW / 2) - total / 2
    for form, cfg in row:
        cfg[key] = v
        v += TILE[form][i] + GAP


def crossings(tiles, corner_dead):
    """Where the pointer crosses to each device. tiles: {address: (form, cfg)} of active devices.
    Returns {address: ([(x, y, w, h)] indicator rectangles, (lo, hi) span along a side edge or None)}"""
    corners = {cfg["edge"] for _, cfg in tiles.values() if "-" in cfg["edge"]}
    inset = DEAD_PX if corner_dead else 0
    out = {}
    for address, (form, cfg) in tiles.items():
        edge, x, y = cfg["edge"], cfg["x"], cfg["y"]
        w, h = TILE[form]
        if "-" in edge:
            vertical, horizontal = edge.split("-")
            lv, lh = CORNER_FRAC * SH, CORNER_FRAC * SW
            out[address] = ([(SX + SW - 3 if horizontal == "right" else SX - 1,
                              SY + SH - lv if vertical == "bottom" else SY, 4, lv),
                             (SX + SW - lh if horizontal == "right" else SX,
                              SY + SH - 3 if vertical == "bottom" else SY - 1, lh, 4)], None)
        elif edge in ("left", "right"):
            clear = CORNER_FRAC * SH + GAP
            t = max(y, SY + (clear if f"top-{edge}" in corners else inset))
            b = min(y + h, SY + SH - (clear if f"bottom-{edge}" in corners else inset))
            out[address] = ([(SX - 1 if edge == "left" else SX + SW - 3, t, 4, max(0, b - t))],
                            ((t - SY) / SH, (max(t, b) - SY) / SH))
        else:
            clear = CORNER_FRAC * SW + GAP
            l = max(x, SX + (clear if f"{edge}-left" in corners else inset))  # noqa: E741
            r = min(x + w, SX + SW - (clear if f"{edge}-right" in corners else inset))
            out[address] = ([(l, SY - 1 if edge == "top" else SY + SH - 3, max(0, r - l), 4)],
                            ((l - SX) / SW, (max(l, r) - SX) / SW))
    return out


def edge_segments(zone, span):
    """[(screen edge, lo, hi)] fractions along the edges where the pointer crosses for one device."""
    c = CORNER_FRAC
    if zone in OPPOSITE:
        return [(zone, *(span or (0.0, 1.0)))]
    vertical, horizontal = zone.split("-")
    along_side = (0.0, c) if vertical == "top" else (1 - c, 1.0)
    along_top = (0.0, c) if horizontal == "left" else (1 - c, 1.0)
    return [(horizontal, *along_side), (vertical, *along_top)]


# --- widgets -----------------------------------------------------------------

class DeviceTile(Gtk.Box):
    def __init__(self, address, form, cfg):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["device-tile"])
        self.address, self.form, self.cfg = address, form, cfg
        w, h = TILE[form]
        self.set_size_request(w, h)
        self.append(Gtk.Image(icon_name=device_icon(form), pixel_size=20, vexpand=True, valign=Gtk.Align.END))
        self.label = Gtk.Label(vexpand=True, valign=Gtk.Align.START, justify=Gtk.Justification.CENTER,
                               wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               margin_start=4, margin_end=4)
        self.append(self.label)
        self.set_cursor_from_name("grab")


class ArrangementView(Gtk.Fixed):
    """Drag a device to the edge of this computer's screen where the pointer should cross over."""

    def __init__(self, on_moved):
        super().__init__(css_classes=["arrangement"], halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        self.set_size_request(CANVAS_W, CANVAS_H)
        self.on_moved = on_moved
        self.tiles = {}  # address -> DeviceTile
        self.indicators = []
        self.dragging = None
        self.anim = None

        self.screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["screen-tile"])
        self.screen.set_size_request(SW, SH)
        self.screen.append(Gtk.Image(icon_name="sc-computer-symbolic", pixel_size=26, vexpand=True,
                                     valign=Gtk.Align.END))
        self.screen.append(Gtk.Label(label="This Computer", css_classes=["heading"]))
        self.resolution = Gtk.Label(css_classes=["caption", "dim-label"], vexpand=True, valign=Gtk.Align.START)
        self.screen.append(self.resolution)
        self.put(self.screen, SX, SY)
        self.empty = Gtk.Label(label="Pair a device to arrange it", css_classes=["dim-label", "caption"])
        self.empty.set_size_request(CANVAS_W, -1)
        self.put(self.empty, 0, CANVAS_H - 34)
        self.connect("realize", lambda _w: self.show_resolution())
        drag = Gtk.GestureDrag()  # on the canvas: offsets measured on a moving tile would jitter
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def show_resolution(self):
        monitors = Gdk.Display.get_default().get_monitors()
        if monitors.get_n_items():
            monitor = monitors.get_item(0)
            geo, scale = monitor.get_geometry(), monitor.get_scale()
            self.resolution.set_label(f"{round(geo.width * scale)} × {round(geo.height * scale)}")

    def add(self, address, form, cfg):
        tile = DeviceTile(address, form, cfg)
        self.put(tile, cfg["x"], cfg["y"])
        self.tiles[address] = tile
        self.empty.set_visible(False)

    def remove_device(self, address):
        self.remove(self.tiles.pop(address))
        self.empty.set_visible(not self.tiles)

    def set_state(self, address, short, name, active):
        tile = self.tiles[address]
        tile.label.set_label(short)
        tile.set_tooltip_text(name)
        if active:
            tile.remove_css_class("inactive")
        else:
            tile.add_css_class("inactive")

    def refresh(self, active, corner_dead):
        """Redraw the crossing indicators; returns {address: span} for the active devices."""
        for widget in self.indicators:
            self.remove(widget)
        self.indicators = []
        spans = crossings({a: (t.form, t.cfg) for a, t in self.tiles.items() if a in active}, corner_dead)
        if self.dragging is None:
            for rects, _span in spans.values():
                for x, y, w, h in rects:
                    if w <= 0 or h <= 0:
                        continue
                    bar = Gtk.Box(css_classes=["edge-indicator"], can_target=False)
                    bar.set_size_request(round(w), round(h))
                    self.put(bar, x, y)
                    bar.insert_after(self, self.screen)  # under the tiles
                    self.indicators.append(bar)
        return {a: span for a, (_rects, span) in spans.items()}

    def on_drag_begin(self, gesture, x, y):
        tile = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while tile is not None and not isinstance(tile, DeviceTile):
            tile = tile.get_parent() if tile is not self else None
        if tile is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.anim:
            self.anim.skip()
        self.dragging = tile
        tile.start = (tile.cfg["x"], tile.cfg["y"])
        tile.add_css_class("dragging")
        tile.set_cursor_from_name("grabbing")
        tile.insert_before(self, None)  # on top
        for widget in self.indicators:
            widget.set_visible(False)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_drag_update(self, _gesture, dx, dy):
        tile = self.dragging
        if tile is None:
            return
        w, h = TILE[tile.form]
        tile.cfg["x"] = max(0, min(CANVAS_W - w, tile.start[0] + dx))
        tile.cfg["y"] = max(0, min(CANVAS_H - h, tile.start[1] + dy))
        self.move(tile, tile.cfg["x"], tile.cfg["y"])

    def on_drag_end(self, _gesture, _dx, _dy):
        tile = self.dragging
        if tile is None:
            return
        w, h = TILE[tile.form]
        tile.remove_css_class("dragging")
        tile.set_cursor_from_name("grab")
        before = {a: (t.cfg["x"], t.cfg["y"]) for a, t in self.tiles.items()}
        tile.cfg["edge"], tile.cfg["x"], tile.cfg["y"] = snap(tile.form, tile.cfg["x"] + w / 2,
                                                              tile.cfg["y"] + h / 2)
        edge = tile.cfg["edge"]
        if "-" not in edge:
            row = [(t.form, t.cfg) for t in self.tiles.values() if t.cfg["edge"] == edge]
            if len(row) > 1:
                spread(row, edge)
        self.dragging = None
        self.animate(before)
        self.on_moved()

    def animate(self, before):
        moves = {t: (before[a], (t.cfg["x"], t.cfg["y"])) for a, t in self.tiles.items()}

        def step(value):
            for tile, ((x0, y0), (x1, y1)) in moves.items():
                self.move(tile, x0 + (x1 - x0) * value, y0 + (y1 - y0) * value)

        self.anim = Adw.TimedAnimation(widget=self, value_from=0, value_to=1, duration=180,
                                       easing=Adw.Easing.EASE_OUT_CUBIC,
                                       target=Adw.CallbackAnimationTarget.new(step))
        self.anim.play()


class DeviceRow(Adw.PreferencesRow):
    """One device on the Devices page: OS and battery, setup guide, on/off, forget."""

    def __init__(self, window, address):
        super().__init__(activatable=False, css_classes=["device-row"])
        self.window, self.address = window, address
        box = Gtk.Box(spacing=14, margin_top=8, margin_bottom=8, margin_start=14, margin_end=12)
        self.icon = Gtk.Image(icon_name="sc-smartphone-symbolic", pixel_size=22)
        box.append(self.icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(self.name)
        info = Gtk.Box(spacing=8, css_classes=["dim-label", "caption"])
        self.os = Gtk.Label(xalign=0)
        info.append(self.os)
        self.battery = Gtk.Box(spacing=2)
        self.battery.append(Gtk.Label(label="·", margin_end=6))
        self.battery_icon = Gtk.Image(pixel_size=15)
        self.battery_label = Gtk.Label()
        self.battery.append(self.battery_icon)
        self.battery.append(self.battery_label)
        info.append(self.battery)
        text.append(info)
        box.append(text)
        guide_slot = Gtk.Box(width_request=34)
        self.guide = Gtk.Button(icon_name="sc-help-symbolic", tooltip_text="Setup Guide", valign=Gtk.Align.CENTER)
        for cls in ("flat", "circular", "icon-20"):
            self.guide.add_css_class(cls)
        self.guide.connect("clicked", lambda _b: window.show_guide(address))
        guide_slot.append(self.guide)
        box.append(guide_slot)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_switch)
        box.append(self.switch)
        forget = Gtk.Button(icon_name="sc-delete-symbolic", tooltip_text="Forget Device", valign=Gtk.Align.CENTER)
        for cls in ("flat", "icon-20"):
            forget.add_css_class(cls)
        forget.connect("clicked", lambda _b: window.ask_forget(address))
        box.append(forget)
        self.set_child(box)
        self._updating = False

    def update(self, dev, enabled):
        self.icon.set_from_icon_name(device_icon(dev["form"]))
        self.name.set_label(dev["name"])
        self.set_title(dev["name"])
        version = dev.get("software") or ""
        self.os.set_label(f"{dev['os']} {version}" if version[:1].isdigit() else dev["os"])
        battery = dev.get("battery")
        self.battery.set_visible(battery is not None)
        if battery is not None:
            self.battery_icon.set_from_icon_name(battery_icon(battery))
            self.battery_label.set_label(f"{battery}%")
        self.guide.set_visible(dev["os"] in ("iOS", "iPadOS"))
        self._updating = True
        self.switch.set_active(enabled)
        self._updating = False

    def on_switch(self, switch, _pspec):
        if not self._updating:
            self.window.device_cfg(self.address)["enabled"] = switch.get_active()
            self.window.layout_changed()


class EdgeOverlay:
    """The on-screen edge indicator: thin lines (and device names) along the screen edges where the
    pointer crosses, drawn in click-through layer-shell surfaces over every monitor."""

    def __init__(self):
        self.windows = {}  # monitor -> (window, drawing area)
        self.segments = []  # [(edge, lo, hi, name)]
        self.mode, self.within, self.names = "near", 48, True
        self.cursor = None
        self.poll = None
        self.available = bool(Gtk4LayerShell and Gtk4LayerShell.is_supported())
        if self.available:
            Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: self.sync())

    def update(self, segments, mode, within, names):
        self.segments, self.mode, self.within, self.names = segments, mode, within, names
        self.sync()

    def sync(self):
        if not self.available:
            return
        show = self.mode != "never" and bool(self.segments)
        monitors = list(Gdk.Display.get_default().get_monitors()) if show else []
        for monitor in list(self.windows):
            if monitor not in monitors:
                self.windows.pop(monitor)[0].destroy()
        for monitor in monitors:
            if monitor not in self.windows:
                self.windows[monitor] = self._window(monitor)
        for _window, area in self.windows.values():
            area.queue_draw()
        polling = show and self.mode == "near"
        if polling and self.poll is None:
            self.poll = GLib.timeout_add(40, self._poll)
        elif not polling and self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll, self.cursor = None, None

    def _window(self, monitor):
        window = Gtk.Window(css_classes=["edge-overlay"], decorated=False)
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, "side-control-edges")
        Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_monitor(window, monitor)
        for edge in (Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT,
                     Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM):
            Gtk4LayerShell.set_anchor(window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(window, -1)
        Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.NONE)
        area = Gtk.DrawingArea(can_target=False)
        area.set_draw_func(self._draw, monitor)
        window.set_child(area)
        window.connect("realize", lambda w: w.get_surface().set_input_region(cairo.Region()))
        window.present()
        return window, area

    def _poll(self):
        reply = hyprland("j/cursorpos")
        try:
            pos = json.loads(reply) if reply else None
            cursor = (pos["x"], pos["y"]) if pos else None
        except (ValueError, KeyError, TypeError):
            cursor = None
        if cursor != self.cursor:
            self.cursor = cursor
            for _window, area in self.windows.values():
                area.queue_draw()
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _box():
        rects = [m.get_geometry() for m in Gdk.Display.get_default().get_monitors()]
        left, top = min(r.x for r in rects), min(r.y for r in rects)
        return left, top, max(r.x + r.width for r in rects), max(r.y + r.height for r in rects)

    def _draw(self, area, cr, w, h, monitor):
        geo = monitor.get_geometry()
        left, top, right, bottom = self._box()
        found, accent = area.get_style_context().lookup_color("accent_bg_color")
        if not found:
            accent = Gdk.RGBA(red=0.21, green=0.52, blue=0.89, alpha=1)
        for edge, lo, hi, name in self.segments:
            vert = edge in ("left", "right")
            on_edge = {"left": geo.x == left, "right": geo.x + geo.width == right,
                       "top": geo.y == top, "bottom": geo.y + geo.height == bottom}[edge]
            if not on_edge:
                continue
            a0, a1 = (top, bottom) if vert else (left, right)
            g0, g1 = a0 + lo * (a1 - a0), a0 + hi * (a1 - a0)  # the segment, in global coordinates
            m0, m1 = (geo.y, geo.y + geo.height) if vert else (geo.x, geo.x + geo.width)
            s0, s1 = max(g0, m0), min(g1, m1)
            if s1 <= s0:
                continue
            alpha = 1.0
            if self.mode == "near":
                if self.cursor is None:
                    continue
                cx, cy = self.cursor
                across = {"left": cx - left, "right": right - 1 - cx, "top": cy - top, "bottom": bottom - 1 - cy}[edge]
                along = cy if vert else cx
                dist = math.hypot(max(0.0, across), max(0.0, g0 - along, along - g1))
                if dist > self.within:
                    continue
                alpha = 1.0 - 0.7 * dist / max(1, self.within)
            p0, p1 = s0 - m0, s1 - m0  # monitor-local
            if vert:
                rect = (0 if edge == "left" else w - 4, p0, 4, p1 - p0)
            else:
                rect = (p0, 0 if edge == "top" else h - 4, p1 - p0, 4)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
            cr.rectangle(*rect)
            cr.fill()
            if self.names and name:
                self._label(area, cr, name, edge, rect, w, h, alpha, accent)

    @staticmethod
    def _label(area, cr, text, edge, rect, w, h, alpha, accent):
        layout = area.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 9"))
        tw, th = (v / Pango.SCALE for v in layout.get_size())
        pad_x, pad_y = 8, 3
        bw, bh = tw + 2 * pad_x, th + 2 * pad_y
        x, y, rw, rh = rect
        bx = {"left": 10, "right": w - 10 - bw}.get(edge, x + rw / 2 - bw / 2)
        by = {"top": 10, "bottom": h - 10 - bh}.get(edge, y + rh / 2 - bh / 2)
        bx, by = max(4, min(w - bw - 4, bx)), max(4, min(h - bh - 4, by))
        r = bh / 2
        cr.new_sub_path()
        cr.arc(bx + bw - r, by + r, r, -math.pi / 2, math.pi / 2)
        cr.arc(bx + r, by + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.92 * alpha)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.move_to(bx + pad_x, by + pad_y)
        PangoCairo.show_layout(cr, layout)


TRAY_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  </interface>
</node>
"""


def icon_pixmaps(name, color):
    """[(w, h, ARGB32 bytes)] of one of the bundled tray icons, in `color`, for a tray host to draw."""
    with open(os.path.join(ICONS_DIR, f"{name}.svg")) as f:
        svg = f.read().replace("#222222", color).encode()
    pixmaps = []
    for size in (16, 22, 24, 32, 48):
        loader = GdkPixbuf.PixbufLoader()
        loader.set_size(size, size)
        loader.write(svg)
        loader.close()
        pixbuf = loader.get_pixbuf().add_alpha(False, 0, 0, 0)
        data, stride = pixbuf.get_pixels(), pixbuf.get_rowstride()
        argb = bytearray()
        for y in range(size):
            row = data[y * stride:y * stride + size * 4]
            for x in range(0, size * 4, 4):
                r, g, b, a = row[x:x + 4]
                argb += bytes((a, r, g, b))
        pixmaps.append((size, size, bytes(argb)))
    return pixmaps


class Tray:
    """Side Control in the system tray (StatusNotifierItem + com.canonical.dbusmenu, as Waybar and
    other StatusNotifier hosts expect): where the cursor is, and a menu to move it."""

    PATH, MENU_PATH = "/StatusNotifierItem", "/MenuBar"

    def __init__(self, window):
        self.window = window
        self.icon = "side-control-off-symbolic"
        self.pixmaps = []
        self.tooltip = ("Side Control", "")
        self.items = []  # [(props, callback)] - menu item id = index + 1
        self.revision = 0
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error:
            self.bus = None
            return
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        node = Gio.DBusNodeInfo.new_for_xml(TRAY_XML)
        self.bus.register_object(self.PATH, node.lookup_interface("org.kde.StatusNotifierItem"),
                                 self.on_item_call, self.on_item_property, None)
        self.bus.register_object(self.MENU_PATH, node.lookup_interface("com.canonical.dbusmenu"),
                                 self.on_menu_call, self.on_menu_property, None)
        Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # (re-)register whenever a tray host's watcher appears, e.g. after Waybar restarts
        Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self.register(), None)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update(force_icon=True))

    def register(self):
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                      "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)), None,
                      Gio.DBusCallFlags.NONE, -1, None, None)

    def emit(self, path, interface, signal, params=None):
        self.bus.emit_signal(None, path, interface, signal, params)

    # --- what it shows ---

    def update(self, force_icon=False):
        if self.bus is None:
            return
        w = self.window
        target = w.devs.get(w.target) if w.alive and w.target else None
        connected = [d for d in w.devs.values() if d.get("connected")]
        if not w.alive:
            icon, text = "side-control-off-symbolic", "Not running"
        elif target:
            icon, text = "side-control-active-symbolic", f"Cursor on {target['name']}"
        else:
            icon = APP_ICON + "-symbolic" if w.settings["share_cursor"] else "side-control-off-symbolic"
            text = "Cursor on this computer" + ("" if w.settings["share_cursor"] else " · sharing off")
        if w.alive:
            text += f"\n{len(connected)} device{'s' if len(connected) != 1 else ''} connected"
            if w.locked:
                text += " · locked to this screen"
        if icon != self.icon or force_icon or not self.pixmaps:
            self.icon = icon
            color = "#ffffff" if Adw.StyleManager.get_default().get_dark() else "#000000"
            self.pixmaps = icon_pixmaps(icon, color)
            self.emit(self.PATH, "org.kde.StatusNotifierItem", "NewIcon")
        if ("Side Control", text) != self.tooltip:
            self.tooltip = ("Side Control", text)
            self.emit(self.PATH, "org.kde.StatusNotifierItem", "NewToolTip")
        self.build_menu(target, connected)

    def build_menu(self, target, connected):
        w = self.window
        items = []

        def add(label=None, callback=None, enabled=True, toggle=None, state=False):
            props = {"type": "separator"} if label is None else {"label": label, "enabled": enabled}
            if toggle:
                props.update({"toggle-type": toggle, "toggle-state": 1 if state else 0})
            items.append((props, callback))

        if w.alive:
            add("This Computer", lambda: w.backend.send(cmd="set_target", address=None),
                toggle="radio", state=target is None)
            for dev in sorted(w.devs.values(), key=lambda d: d["name"].lower()):
                add(dev["name"].replace("_", "__"),
                    lambda a=dev["address"]: w.backend.send(cmd="set_target", address=a),
                    enabled=bool(dev.get("connected")), toggle="radio", state=dev is target)
            add()
            add("Share Cursor", lambda: w.share_row.set_active(not w.settings["share_cursor"]),
                toggle="checkmark", state=w.settings["share_cursor"])
            add("Lock Cursor to Current Screen", lambda: w.backend.send(cmd="set_options", locked=not w.locked),
                toggle="checkmark", state=w.locked)
            add()
            add("Pair Device…", w.open_pair)
        add("Open Side Control", w.present)
        add("Quit", w.get_application().quit)
        if [p for p, _ in items] != [p for p, _ in self.items]:
            self.items = items
            self.revision += 1
            self.emit(self.MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                      GLib.Variant("(ui)", (self.revision, 0)))
        else:
            self.items = items  # same look, fresh callbacks

    # --- org.kde.StatusNotifierItem ---

    def on_item_property(self, _bus, _sender, _path, _iface, name):
        values = {"Category": GLib.Variant("s", "ApplicationStatus"), "Id": GLib.Variant("s", "side-control"),
                  "Title": GLib.Variant("s", "Side Control"), "Status": GLib.Variant("s", "Active"),
                  "WindowId": GLib.Variant("i", 0), "IconName": GLib.Variant("s", ""),
                  "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
                  "IconThemePath": GLib.Variant("s", ""),
                  "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], *self.tooltip)),
                  "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", self.MENU_PATH)}
        return values.get(name)

    def on_item_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        if method == "Activate":
            self.window.present()
        elif method == "SecondaryActivate":
            self.window.backend.send(cmd="set_target", address=None)
        invocation.return_value(None)

    # --- com.canonical.dbusmenu ---

    def on_menu_property(self, _bus, _sender, _path, _iface, name):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(name)

    def item_props(self, item_id):
        if item_id == 0:
            return {"children-display": GLib.Variant("s", "submenu")}
        props, _callback = self.items[item_id - 1]
        return {k: GLib.Variant({"label": "s", "type": "s", "toggle-type": "s"}.get(k, "b" if k == "enabled" else "i"),
                                v) for k, v in props.items()}

    def on_menu_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (i, self.item_props(i), []))
                        for i in range(1, len(self.items) + 1)]
            invocation.return_value(GLib.Variant("(u(ia{sv}av))",
                                                 (self.revision, (0, self.item_props(0), children))))
        elif method == "GetGroupProperties":
            ids = [i for i in args[0] if 0 <= i <= len(self.items)] or range(len(self.items) + 1)
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([(i, self.item_props(i)) for i in ids],)))
        elif method == "GetProperty":
            item_id, name = args
            value = self.item_props(item_id).get(name) if 0 <= item_id <= len(self.items) else None
            invocation.return_value(GLib.Variant("(v)", (value or GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event = args[0], args[1]
            if event == "clicked" and 1 <= item_id <= len(self.items) and self.items[item_id - 1][1]:
                GLib.idle_add(lambda cb=self.items[item_id - 1][1]: (cb(), False)[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event, _data, _time in args[0]:
                if event == "clicked" and 1 <= item_id <= len(self.items) and self.items[item_id - 1][1]:
                    GLib.idle_add(lambda cb=self.items[item_id - 1][1]: (cb(), False)[1])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)


def step_row(number, title, subtitle=None, extra=None):
    """A numbered instruction in the setup guide."""
    row = Gtk.Box(spacing=14, margin_top=12, margin_bottom=12, margin_start=14, margin_end=14)
    row.append(Gtk.Label(label=str(number), css_classes=["step-number"], valign=Gtk.Align.START))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    text.append(Gtk.Label(label=title, xalign=0, wrap=True))
    if subtitle:
        text.append(Gtk.Label(label=subtitle, xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
    if extra:
        text.append(extra)
    row.append(text)
    return Gtk.ListBoxRow(activatable=False, child=row)


def crumbs(*parts):
    box = Adw.WrapBox(child_spacing=4, line_spacing=4)
    for i, part in enumerate(parts):
        if i:
            box.append(Gtk.Image(icon_name="sc-chevron-right-symbolic", css_classes=["dim-label"]))
        box.append(Gtk.Label(label=part, css_classes=["crumb"]))
    return box


def dialog_shell(title, width, body):
    """Adw.Dialog with a header bar and `body` in the usual margins."""
    dialog = Adw.Dialog(title=title, content_width=width)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    for side in ("start", "end", "bottom"):
        getattr(body, f"set_margin_{side}")(22)
    body.set_margin_top(4)
    view.set_content(body)
    dialog.set_child(view)
    return dialog


# --- window ------------------------------------------------------------------

PAGES = [("devices", "Devices", "sc-devices-symbolic"),
         ("layout", "Layout", "sc-space-dashboard-symbolic"),
         ("gestures", "Gestures & Keybindings", "sc-gesture-symbolic"),
         ("appearance", "Appearance", "sc-palette-symbolic"),
         ("settings", "Settings", "sc-settings-symbolic")]
SHORTCUTS = [("Toggle Cursor Sharing", "<Super><Shift>s"), ("Send Cursor to Next Device", "<Super>Right"),
             ("Return Cursor to Computer", "<Super>Escape"), ("Lock Cursor to Current Screen", "Scroll_Lock"),
             ("Open Side Control", "<Super><Alt>d")]
INDICATORS = [("always", "Always Visible", "A thin line stays on every connected edge"),
              ("near", "When Cursor Is Near", "The edge lights up as the cursor approaches"),
              ("never", "Hidden", "No indicator; just push past the edge")]


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Side Control", default_width=1040, default_height=700)
        self.settings = load_settings()
        self.alive = False
        self.resync = True
        self.started_backend = False  # tried pkexec once already
        self.restart_backend = False  # waiting for the backend to quit, then start it on another adapter
        self.hid_change = None  # (script, files) to run once the backend has quit: see on_hid
        self.devs = {}  # address -> last device dict from the backend
        self.rows = {}
        self.target = None
        self.pair_dialog = None
        self.nearby = None
        self.pending_guide = None  # address just paired: its setup guide opens once it appears
        self.adapter = None
        self.overlay = EdgeOverlay()
        self.spans = {}

        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ICONS_DIR)
        Gtk.Window.set_default_icon_name(APP_ICON)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.split = Adw.NavigationSplitView(min_sidebar_width=248, max_sidebar_width=248)
        self.split.set_sidebar(self._sidebar())
        self.split.set_content(self._content())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.connect("close-request", self.on_close)

        self._connect_adapter()
        self.refresh_hid()
        self.locked = False
        self.tray = Tray(self)
        self.backend = Backend(self.on_message, self.on_status)
        self.tray.update()

    # --- frame ---

    def _sidebar(self):
        """The pages on top, Settings pinned to the bottom above the version."""
        self.sidebar = Adw.Sidebar(vexpand=True, css_classes=["sc-sidebar"])
        self.sidebar_bottom = Adw.Sidebar(css_classes=["sc-sidebar"])
        for sidebar, pages in ((self.sidebar, PAGES[:-1]), (self.sidebar_bottom, PAGES[-1:])):
            section = Adw.SidebarSection()
            for _name, title, icon in pages:
                section.append(Adw.SidebarItem(title=title, icon_name=icon))
            sidebar.append(section)
        self.sidebar.connect("activated", lambda _s, index: self.show_page(index))
        self.sidebar_bottom.connect("activated", lambda _s, _index: self.show_page(len(PAGES) - 1))
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(Gtk.Separator())
        bottom.append(self.sidebar_bottom)
        bottom.append(Gtk.Label(label=VERSION, xalign=0, css_classes=["dim-label", "caption", "version"]))
        view = Adw.ToolbarView(content=self.sidebar)
        view.add_top_bar(Adw.HeaderBar())
        view.add_bottom_bar(bottom)
        return Adw.NavigationPage(title="Side Control", child=view)

    def _content(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._devices_page(), "devices")
        self.stack.add_named(self._layout_page(), "layout")
        self.stack.add_named(self._gestures_page(), "gestures")
        self.stack.add_named(self._appearance_page(), "appearance")
        self.stack.add_named(self._settings_page(), "settings")

        header = Adw.HeaderBar()
        self.add_button = Gtk.Button(icon_name="sc-add-symbolic", tooltip_text="Pair Device")
        self.add_button.add_css_class("icon-20")
        self.add_button.connect("clicked", lambda _b: self.show_pair())
        header.pack_start(self.add_button)
        self.hid_banner = Adw.Banner(
            title="System Bluetooth input is disabled — mice and keyboards can’t connect to this "
                  "computer while Side Control owns HID.", button_label="Dismiss")
        self.hid_banner.connect("button-clicked", self.dismiss_hid_banner)
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(self.hid_banner)
        self.content_page = Adw.NavigationPage(title="Devices", child=view)
        self.sidebar.set_selected(0)
        return self.content_page

    def show_page(self, index):
        name, title, _icon = PAGES[index]
        self.stack.set_visible_child_name(name)
        self.content_page.set_title(title)
        self.add_button.set_visible(name == "devices")
        settings = index == len(PAGES) - 1
        for sidebar, selected in ((self.sidebar, Gtk.INVALID_LIST_POSITION if settings else index),
                                  (self.sidebar_bottom, 0 if settings else Gtk.INVALID_LIST_POSITION)):
            if sidebar.get_selected() != selected:
                sidebar.set_selected(selected)
        self.split.set_show_content(True)

    # --- pages ---

    def _devices_page(self):
        self.devices_stack = Gtk.Stack()
        pair = Gtk.Button(label="Pair Device", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        pair.connect("clicked", lambda _b: self.show_pair())
        self.devices_stack.add_named(Adw.StatusPage(
            icon_name="sc-power-symbolic", title="No Devices", child=pair,
            description="Pair an iPhone, iPad or Android device over Bluetooth to move your cursor onto it."),
            "empty")
        page = Adw.PreferencesPage()
        self.devices_group = Adw.PreferencesGroup()
        page.add(self.devices_group)
        self.devices_stack.add_named(page, "list")
        self.devices_stack.set_visible_child_name("empty")
        return self.devices_stack

    def _layout_page(self):
        page = Adw.PreferencesPage()
        arrangement = Adw.PreferencesGroup(
            title="Arrangement",
            description="Drag a device to the edge of your screen where the cursor should cross over")
        self.arrangement = ArrangementView(self.layout_changed)
        arrangement.add(self.arrangement)
        page.add(arrangement)

        edges = Adw.PreferencesGroup(title="Edge Switching")
        edges.add(self.stepper_row("switch_delay_ms", "Switch Delay",
                                   "How long to push against an edge before crossing", 0, 1000, 50, "ms",
                                   lambda: self.send_option("switch_delay_ms")))
        edges.add(self.switch_row("corner_dead_zones", "Corner Dead Zones",
                                  "Ignore screen corners so hot corners keep working", self.layout_changed))
        edges.add(self.switch_row("match_speed", "Match Pointer Speed",
                                  "Scale movement to each device’s screen density", self.layout_changed))
        page.add(edges)
        return page

    def _gestures_page(self):
        page = Adw.PreferencesPage()
        keys = Adw.PreferencesGroup(title="Keyboard Shortcuts")
        for title, accel in SHORTCUTS:
            row = Adw.ActionRow(title=title, use_markup=False)
            row.add_suffix(Adw.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
            keys.add(row)
        page.add(keys)

        gestures = Adw.PreferencesGroup(title="Gestures")
        for key, icon, title, subtitle in (
                ("three_finger_switch", "sc-swipe-symbolic", "Three-Finger Swipe",
                 "Swipe on the touchpad to jump to the next device"),
                ("shake_to_locate", "sc-ads-click-symbolic", "Shake to Locate",
                 "Briefly enlarge the cursor on whichever screen it’s on"),
                ("tap_to_click", "sc-touch-app-symbolic", "Tap to Click on Device",
                 "Touchpad taps become taps on the phone or tablet")):
            row = self.switch_row(key, title, subtitle, lambda k=key: self.send_option(k))
            row.add_prefix(Gtk.Image(icon_name=icon, pixel_size=20))
            gestures.add(row)
        page.add(gestures)
        return page

    def _appearance_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Edge Indicator",
                                     description="Show where the cursor can cross to another device")
        first = None
        for key, title, subtitle in INDICATORS:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=self.settings["indicator"] == key)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", self.on_indicator, key)
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
        page.add(group)

        options = Adw.PreferencesGroup()
        self.within_row = self.stepper_row("indicator_within", "Show When Within",
                                           "Distance from the edge before the indicator appears",
                                           8, 200, 8, "px", self.update_overlay)
        self.within_row.set_sensitive(self.settings["indicator"] == "near")
        options.add(self.within_row)
        options.add(self.switch_row("indicator_names", "Show Device Name",
                                    "Label the indicator with the device it leads to", self.update_overlay))
        page.add(options)
        return page

    def _settings_page(self):
        page = Adw.PreferencesPage()
        cursor = Adw.PreferencesGroup(title="Cursor")
        self.share_row = self.switch_row("share_cursor", "Share Cursor",
                                         "Move past a screen edge to control the device there",
                                         lambda: (self.send_option("share_cursor"), self.layout_changed()))
        cursor.add(self.share_row)
        cursor.add(self.switch_row("share_clipboard", "Share Clipboard",
                                   "Copied text follows the cursor between devices"))
        page.add(cursor)

        general = Adw.PreferencesGroup(title="General")
        self.login_row = Adw.SwitchRow(title="Launch at Login", active=os.path.exists(AUTOSTART_PATH))
        self.login_row.connect("notify::active", self.on_login)
        general.add(self.login_row)
        general.add(self.switch_row("run_in_background", "Run in Background",
                                    "Keep devices connected when the window is closed"))
        page.add(general)

        bluetooth = Adw.PreferencesGroup(title="Bluetooth")
        self.adapter_names = Gtk.StringList()
        self.adapter_row = Adw.ComboRow(title="Adapter", use_subtitle=True, model=self.adapter_names)
        self.adapter_row.connect("notify::selected", self.on_adapter)
        self.name_row = Adw.EntryRow(title="Device Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_device_name)
        self.visible_row = self.switch_row("visible", "Visible to Nearby Devices",
                                           "Required while pairing a new phone or tablet",
                                           lambda: self.send_option("visible"))
        self.hid_row = Adw.SwitchRow(
            title="Own HID Profile",
            subtitle="Disables bluetoothd’s input plugin so phones and tablets can also connect to Side "
                     "Control by themselves. Bluetooth mice and keyboards can’t connect to this computer "
                     "while on.")
        self.hid_row.connect("notify::active", self.on_hid)
        for row in (self.adapter_row, self.name_row, self.visible_row, self.hid_row):
            bluetooth.add(row)
        page.add(bluetooth)

        about = Adw.PreferencesGroup(title="About")
        version = Adw.ActionRow(title="Version")
        version.add_suffix(Gtk.Label(label=VERSION, css_classes=["dim-label"]))
        report = Adw.ActionRow(title="Report an Issue", activatable=True)
        report.add_suffix(Gtk.Image(icon_name="sc-open-in-new-symbolic"))
        report.connect("activated", self.on_report)
        about.add(version)
        about.add(report)
        page.add(about)
        return page

    def switch_row(self, key, title, subtitle=None, changed=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=self.settings[key])
        row.updating = False

        def toggled(row, _pspec):
            if row.updating:
                return
            self.settings[key] = row.get_active()
            save_settings(self.settings)
            if changed:
                changed()
        row.connect("notify::active", toggled)
        return row

    def stepper_row(self, key, title, subtitle, lower, upper, step, unit, changed):
        row = Adw.SpinRow(title=title, subtitle=subtitle, numeric=False,
                          adjustment=Gtk.Adjustment(value=self.settings[key], lower=lower, upper=upper,
                                                    step_increment=step, page_increment=step))

        def output(row):
            row.set_text(f"{int(row.get_value())} {unit}")
            return True

        def parse(row):
            try:
                return True, float(row.get_text().split()[0])
            except (ValueError, IndexError):
                return False, row.get_value()

        def value_changed(row, _pspec):
            self.settings[key] = int(row.get_value())
            save_settings(self.settings)
            changed()
        row.connect("output", output)
        row.connect("input", parse)
        row.connect("notify::value", value_changed)
        return row

    # --- devices ---

    def device_cfg(self, address):
        cfg = self.settings["devices"].get(address)
        if cfg is None:
            cfg = self.settings["devices"][address] = dict(DEVICE_DEFAULTS)
        return cfg

    @staticmethod
    def short_name(dev):
        if dev["os"] in ("iOS", "iPadOS"):
            return "iPad" if dev["form"] == "tablet" else "iPhone"
        return dev["name"]

    def add_device(self, dev):
        address, form = dev["address"], dev["form"]
        cfg = self.device_cfg(address)
        others = [(t.form, t.cfg["edge"], t.cfg["x"], t.cfg["y"]) for t in self.arrangement.tiles.values()]
        if cfg["edge"] is None or cfg["x"] is None or cfg["y"] is None:
            used = {e for _f, e, _x, _y in others}
            edge = cfg["edge"] or next((e for e in OPPOSITE if e not in used), "bottom")
            cfg["edge"], cfg["x"], cfg["y"] = free_spot(form, edge, others)
        self.arrangement.add(address, form, cfg)
        row = DeviceRow(self, address)
        self.devices_group.add(row)
        self.rows[address] = row

    def remove_device(self, address):
        self.devices_group.remove(self.rows.pop(address))
        self.arrangement.remove_device(address)
        self.devs.pop(address, None)

    def ask_forget(self, address):
        dialog = Adw.AlertDialog(heading=f"Forget {self.devs.get(address, {}).get('name', 'Device')}?",
                                 body="You’ll need to pair it again to move the cursor onto it.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget")
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "forget" and self.forget(address))
        dialog.present(self)

    def forget(self, address):
        self.backend.send(cmd="forget", address=address)
        self.settings["devices"].pop(address, None)
        if address in self.rows:
            self.remove_device(address)
        self.layout_changed()
        self.devices_stack.set_visible_child_name("list" if self.rows else "empty")

    # --- pairing ---

    def show_pair(self):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        searching = Gtk.Box(spacing=10, css_classes=["dim-label"])
        searching.append(Adw.Spinner())
        searching.append(Gtk.Label(label="Searching for nearby devices…"))
        body.append(searching)
        self.nearby_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.nearby_list.set_placeholder(Gtk.Label(label="No other devices found", margin_top=18,
                                                   margin_bottom=18, css_classes=["dim-label"]))
        body.append(self.nearby_list)
        body.append(Gtk.Label(label="Keep Bluetooth settings open on your phone or tablet so it can be found. "
                                    "iOS devices pair as a Bluetooth pointer — no app needed.",
                              xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
        self.pair_dialog = dialog_shell("Pair a Device", 420, body)
        self.nearby = None
        self.pair_dialog.connect("closed", self.on_pair_closed)
        self.backend.send(cmd="discover", on=True)
        self.pair_dialog.present(self)

    def on_pair_closed(self, _dialog):
        self.pair_dialog = None
        self.backend.send(cmd="discover", on=False)

    def open_pair(self):
        self.present()
        self.show_page(0)
        self.show_pair()

    def show_nearby(self, nearby, pairing):
        key = (json.dumps(nearby), pairing)
        if self.pair_dialog is None or key == self.nearby:
            return
        self.nearby = key
        self.nearby_list.remove_all()
        for dev in nearby:
            row = Adw.ActionRow(title=GLib.markup_escape_text(dev["name"]), subtitle=dev["os"])
            row.add_prefix(Gtk.Image(icon_name=device_icon(dev["form"]), pixel_size=22))
            if pairing == dev["address"]:
                row.add_suffix(Adw.Spinner(margin_start=12, margin_end=12))
            else:
                button = Gtk.Button(label="Pair", valign=Gtk.Align.CENTER, css_classes=["suggested-action"],
                                    sensitive=pairing is None)
                button.connect("clicked", lambda _b, a=dev["address"]: self.backend.send(cmd="pair", address=a))
                row.add_suffix(button)
            self.nearby_list.append(row)

    def on_paired(self, msg):
        if not msg["ok"]:
            self.toasts.add_toast(Adw.Toast(title=f"Pairing failed: {msg.get('error') or 'unknown error'}"))
            return
        if self.pair_dialog is not None:
            self.pair_dialog.close()
        self.pending_guide = msg["address"]

    def show_guide(self, address):
        dev = self.devs.get(address, {})
        kind = "iPad" if dev.get("form") == "tablet" else "iPhone"
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.append(Gtk.Label(label="iPhone and iPad only show a cursor when AssistiveTouch is on. "
                                    "Do this once on the device:", xalign=0, wrap=True, css_classes=["dim-label"]))
        steps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        steps.append(step_row(1, "Open Settings and go to", extra=crumbs("Accessibility", "Touch", "AssistiveTouch")))
        steps.append(step_row(2, "Turn on AssistiveTouch", "A round cursor appears on the screen"))
        steps.append(step_row(3, "Optional: turn off Always Show Menu",
                              "Hides the floating button while keeping the cursor"))
        body.append(steps)
        done = Gtk.Button(label="Done", css_classes=["suggested-action"], halign=Gtk.Align.END)
        body.append(done)
        dialog = dialog_shell(f"Set Up {kind}", 460, body)
        done.connect("clicked", lambda _b: dialog.close())
        dialog.present(self)

    # --- backend events ---

    def on_status(self, alive, reason):
        self.alive = alive
        if alive:
            self.resync = True
            self.refresh_hid()
            return
        self.target, self.locked = None, False
        for address in list(self.rows):
            self.remove_device(address)
        self.devices_stack.set_visible_child_name("empty")
        self.update_overlay()
        self.tray.update()
        if self.hid_change:
            self.run_hid_change(*self.hid_change)
        elif self.restart_backend or (reason == "not running" and not self.started_backend):
            self.restart_backend = False
            self.start_backend()

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "state":
            self.on_state(msg)
        elif kind == "paired":
            self.on_paired(msg)
        elif kind == "shake":
            self.enlarge_cursor()
        elif kind == "open":
            self.present()

    def on_state(self, st):
        self.target = st["target"]
        self.locked = bool(st.get("locked"))
        seen = set()
        changed = False
        for dev in st["devices"]:
            address = dev["address"]
            seen.add(address)
            old = self.devs.get(address)
            self.devs[address] = dev
            if address not in self.rows:
                self.add_device(dev)
                changed = True
            elif ((old["form"], old["name"], old["os"], old["handoff"]["phone_w"], old["handoff"]["phone_h"])
                  != (dev["form"], dev["name"], dev["os"], dev["handoff"]["phone_w"], dev["handoff"]["phone_h"])):
                # (a new screen size, once the model is known, changes "Match Pointer Speed")
                changed = True
                if old["form"] != dev["form"]:  # the tile's size follows the device's form
                    self.arrangement.remove_device(address)
                    self.arrangement.add(address, dev["form"], self.device_cfg(address))
            self.rows[address].update(dev, self.device_cfg(address)["enabled"])
        for address in set(self.rows) - seen:
            self.remove_device(address)
            changed = True
        self.devices_stack.set_visible_child_name("list" if self.rows else "empty")

        if st.get("sharing") is not None and st["sharing"] != self.settings["share_cursor"]:
            self.settings["share_cursor"] = st["sharing"]  # toggled with Super+Shift+S
            save_settings(self.settings)
            self.share_row.updating = True
            self.share_row.set_active(st["sharing"])
            self.share_row.updating = False
            changed = True
        if st.get("nearby") is not None:
            self.show_nearby(st["nearby"], st.get("pairing"))
        if self.pending_guide in self.devs:
            address, self.pending_guide = self.pending_guide, None
            if self.devs[address]["os"] in ("iOS", "iPadOS"):
                self.show_guide(address)
        if self.resync:
            self.resync = False
            self.send_options()
            changed = True
        if changed:
            self.layout_changed()
        else:
            self.update_overlay()
        self.tray.update()

    # --- hand-off and options ---

    def active(self):
        return {a for a in self.rows if self.device_cfg(a)["enabled"] and self.settings["share_cursor"]}

    def layout_changed(self):
        """Save the arrangement and send every device's hand-off settings to the backend."""
        active = self.active()
        for address in self.rows:
            dev = self.devs[address]
            self.arrangement.set_state(address, self.short_name(dev), dev["name"],
                                       self.device_cfg(address)["enabled"])
        self.spans = self.arrangement.refresh(active, self.settings["corner_dead_zones"])
        save_settings(self.settings)
        for address in self.rows:
            cfg, dev = self.device_cfg(address), self.devs[address]
            w, h = dev["handoff"]["phone_w"], dev["handoff"]["phone_h"]
            speed = round(math.sqrt(w * h / BASE_SCREEN), 2) if self.settings["match_speed"] else 1.0
            self.backend.send(cmd="set_handoff", address=address,
                              zone=cfg["edge"] if address in active else None, span=self.spans.get(address),
                              require_near=False, speed=speed)
        self.update_overlay()

    def update_overlay(self):
        segments = []
        if self.alive and self.target is None:
            for address in self.active():
                cfg = self.device_cfg(address)
                if cfg["edge"]:
                    name = self.devs[address]["name"]
                    segments += [(*seg, name) for seg in edge_segments(cfg["edge"], self.spans.get(address))]
        self.overlay.update(segments, self.settings["indicator"], self.settings["indicator_within"],
                            self.settings["indicator_names"])

    def send_options(self):
        options = {k: self.settings[k] for k in BACKEND_OPTIONS}
        options["discoverable"] = self.settings["visible"]
        if self.settings["device_name"]:
            options["device_name"] = self.settings["device_name"]
        self.backend.send(cmd="set_options", **options)

    def send_option(self, key):
        value = self.settings[key]
        self.backend.send(cmd="set_options", **({"discoverable": value} if key == "visible" else {key: value}))

    def on_indicator(self, check, key):
        if check.get_active():
            self.settings["indicator"] = key
            save_settings(self.settings)
            self.within_row.set_sensitive(key == "near")
            self.update_overlay()

    def on_login(self, row, _pspec):
        try:
            set_autostart(row.get_active())
        except OSError as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not change autostart: {err.strerror}"))

    def on_report(self, _row):
        if ISSUES_URL:
            Gtk.UriLauncher(uri=ISSUES_URL).launch(self, None, None, None)
        else:
            self.toasts.add_toast(Adw.Toast(title="No issue tracker set (ISSUES_URL in side_control.py)"))

    def on_close(self, _window):
        app = self.get_application()
        if self.settings["run_in_background"]:
            app.keep_running()
            self.set_visible(False)
            return True
        app.quit()
        return False

    def enlarge_cursor(self):
        """Shake to Locate: make the cursor big for a moment (Hyprland)."""
        theme = os.environ.get("XCURSOR_THEME") or Gio.Settings(schema_id="org.gnome.desktop.interface") \
            .get_string("cursor-theme")
        size = int(os.environ.get("XCURSOR_SIZE") or 24)
        if hyprland(f"setcursor {theme} {size * 3}") is None:
            return
        GLib.timeout_add(1200, lambda: (hyprland(f"setcursor {theme} {size}"), False)[1])

    # --- Bluetooth adapter ---

    def _connect_adapter(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
            objects = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                                    "GetManagedObjects", None, None, Gio.DBusCallFlags.NONE, 2000, None)
            paths = sorted(p for p, ifaces in objects.unpack()[0].items() if "org.bluez.Adapter1" in ifaces)
        except GLib.Error:
            paths = []
        self.adapters = [p.rsplit("/", 1)[-1] for p in paths]
        self._updating_adapter = True
        for hci in self.adapters:
            self.adapter_names.append(adapter_label(hci))
        chosen = self.adapters.index(self.settings["adapter"]) if self.settings["adapter"] in self.adapters else 0
        if self.adapters:
            self.adapter_row.set_selected(chosen)
        self._updating_adapter = False
        if not paths:
            return
        self.adapter = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.bluez", paths[chosen],
                                              "org.bluez.Adapter1", None)
        self.adapter.connect("g-properties-changed", lambda *_: self.show_alias())
        self.show_alias()

    def show_alias(self):
        alias = self.adapter.get_cached_property("Alias") if self.adapter else None
        if alias is not None and not self.name_row.has_focus():
            self.name_row.set_text(alias.unpack())

    def on_device_name(self, row):
        name = row.get_text().strip()
        if name:
            self.settings["device_name"] = name
            save_settings(self.settings)
            self.backend.send(cmd="set_options", device_name=name)

    def on_adapter(self, row, _pspec):
        if self._updating_adapter or row.get_selected() >= len(self.adapters):
            return
        self.settings["adapter"] = self.adapters[row.get_selected()]
        save_settings(self.settings)
        if self.alive:
            self.restart_backend = True
            self.backend.send(cmd="quit")  # on_status starts it again on the new adapter

    # --- HID profile (bluetoothd -P input) ---

    def refresh_hid(self):
        owned = hid_owned()
        self._updating_hid = True
        self.hid_row.set_active(bool(owned))
        self.hid_row.set_sensitive(owned is not None)
        self._updating_hid = False
        self.hid_banner.set_revealed(bool(owned) and not self.settings["hid_banner_dismissed"])
        return GLib.SOURCE_REMOVE

    def dismiss_hid_banner(self, _banner):
        self.settings["hid_banner_dismissed"] = True
        save_settings(self.settings)
        self.hid_banner.set_revealed(False)

    def on_hid(self, row, _pspec):
        """Owning HID means bluetoothd runs with -P input. Either way the backend must not run meanwhile
        (it holds the HID channels when it can), so: stop it, change bluetoothd, start it again - all
        in one pkexec."""
        if self._updating_hid:
            return
        if row.get_active():
            script, files = 'install -Dm644 "$1" "$2"', [NO_INPUT_CONF, HID_DROPIN]
            self.settings["hid_banner_dismissed"] = False
            save_settings(self.settings)
        else:
            files = no_input_dropins()
            if not files:
                self.toasts.add_toast(Adw.Toast(title=f"bluetoothd gets -P input from outside {DROPIN_DIR}"))
                self.refresh_hid()
                return
            script = 'rm -f -- "$@"'
        if self.alive:
            self.hid_change = (script, files)
            self.backend.send(cmd="quit")  # on_status runs it once the backend is gone
        else:
            self.run_hid_change(script, files)

    def run_hid_change(self, script, files):
        self.hid_change = None
        self.started_backend = True
        script = ('py=$1 backend=$2 adapter=$3; shift 3; ' + script
                  + ' && systemctl daemon-reload && systemctl restart bluetooth && sleep 1'
                  + ' && if [ -n "$adapter" ]; then exec "$py" "$backend" --adapter "$adapter";'
                  + ' else exec "$py" "$backend"; fi')
        argv = ["pkexec", "sh", "-c", script, "sh", sys.executable, BACKEND, self.settings["adapter"] or "", *files]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not run pkexec: {err.message}"))
            self.refresh_hid()
            return

        def done(proc, result):
            self._backend_exited(proc, result)
            self.refresh_hid()
        proc.communicate_utf8_async(None, None, done)
        GLib.timeout_add(3000, self.refresh_hid)

    # --- starting the root backend ---

    def start_backend(self):
        self.started_backend = True
        argv = ["pkexec", sys.executable, BACKEND]
        if self.settings["adapter"]:
            argv += ["--adapter", self.settings["adapter"]]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not start the backend: {err.message}"))
            return
        proc.communicate_utf8_async(None, None, self._backend_exited)

    def _backend_exited(self, proc, result):
        try:
            _ok, _out, err = proc.communicate_utf8_finish(result)
        except GLib.Error as error:
            err = error.message
        if proc.get_exit_status() != 0 and not self.alive and not self.restart_backend:
            last = (err or "").strip().splitlines()[-1:] or [f"exit status {proc.get_exit_status()}"]
            self.toasts.add_toast(Adw.Toast(title=f"Backend stopped: {last[0]}"[:160]))


class App(Adw.Application):
    def __init__(self, hidden=False):
        super().__init__(application_id=APP_ID)
        self.hidden = hidden
        self.held = False
        self.window = None
        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def keep_running(self):
        if not self.held:
            self.held = True
            self.hold()

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
        if self.hidden and self.window.settings["run_in_background"]:
            self.hidden = False
            self.keep_running()
            return
        self.hidden = False
        self.window.present()


if __name__ == "__main__":
    hidden = "--hidden" in sys.argv
    sys.exit(App(hidden).run([a for a in sys.argv if a != "--hidden"]))
