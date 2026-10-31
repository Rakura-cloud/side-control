#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make this PC a Bluetooth keyboard + mouse for iPhones and iPads (pointer via AssistiveTouch).

Normally started by Side Control (side_control.py) through pkexec; can also run on its own with sudo.
Ctrl+Alt+P switches the keyboard and touchpad between the PC and the connected devices (PC -> first
device -> next device ... -> PC). With bluetoothd started with `-P input` (bluetooth-no-input.conf)
devices can also connect to it; otherwise it connects to them.
"""
import atexit
import glob
import json
import math
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque

import fcntl

import dbus
import dbus.mainloop.glib
import dbus.service
import evdev
from evdev import ecodes as e
from gi.repository import GLib

NAME = "Side Control"  # Bluetooth name until one is set in the app (Settings > Device Name)
TOGGLE = {e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_P}
# Ctrl+Alt+<key> while controlling the phone presses an extra mouse button; assign each one
# in AssistiveTouch > Devices > (this PC) > Customize Additional Buttons.
ACTION_MODS = {e.KEY_LEFTCTRL, e.KEY_LEFTALT}
ACTIONS = {e.KEY_H: 0x08, e.KEY_C: 0x10, e.KEY_N: 0x20, e.KEY_S: 0x40}  # buttons 4-7
# (modifiers held, key, action) - Side Control's shortcuts. The PC's keyboard is not grabbed while the
# PC has control, so the compositor sees these too.
HOTKEYS = [({"super", "shift"}, e.KEY_S, "toggle_sharing"),
           ({"super"}, e.KEY_RIGHT, "next_device"),
           ({"super"}, e.KEY_ESC, "return_to_pc"),
           (set(), e.KEY_SCROLLLOCK, "lock"),
           ({"super", "alt"}, e.KEY_D, "open_gui")]
MOD_KEYS = {e.KEY_LEFTMETA: "super", e.KEY_RIGHTMETA: "super", e.KEY_LEFTSHIFT: "shift",
            e.KEY_RIGHTSHIFT: "shift", e.KEY_LEFTALT: "alt", e.KEY_RIGHTALT: "alt",
            e.KEY_LEFTCTRL: "ctrl", e.KEY_RIGHTCTRL: "ctrl"}
SHARE_CURSOR = True   # edge hand-off on (Super+Shift+S / the GUI toggles it)
THREE_FINGER_SWITCH = True  # three-finger swipe left/right: previous / next device (the PC included)
SWITCH_SWIPE_MM = 25.0
SHAKE_TO_LOCATE = False  # shaking the PC pointer asks the GUI to enlarge it for a moment
SHAKE_TURNS = 4          # direction reversals...
SHAKE_WINDOW_S = 0.8     # ...within this long...
SHAKE_MIN_MM = 3.0       # ...each after at least this much travel
BATTERY_EVERY = 300      # seconds between battery readings of a connected device
NATURAL_SCROLL = True  # content follows the fingers, like on iOS itself
TAP_TO_CLICK = True   # a quick touchpad tap clicks (two fingers: secondary click)
TOUCHPAD_SPEED = 18.0 # mouse counts per mm of finger travel
SCROLL_STEP_MM = 2.5  # finger travel per wheel step (two-finger scroll, up/down and left/right)
GESTURE_LOCK_MM = 2.0  # two-finger travel before it is decided: scroll (and on which axis) or pinch
# Two-finger left/right swipe turns one page (Photos, the Home Screen, ...). A mouse cannot swipe on iOS,
# so it flicks: press, a short fast drag, let go - iOS pages on the speed - then the pointer goes back by
# the same reports, which pointer acceleration stretches exactly as much as the way out
SWIPE_FLICK = True    # False: two-finger left/right sends horizontal scroll instead
FLICK_MM = 4.0        # sideways finger travel that sets off the flick (once per swipe)
FLICK_STEP = 22       # counts per report of the flick...
FLICK_REPORTS = 7     # ...over this many reports
FLICK_PAUSE_S = 0.04  # after letting go, before the pointer goes back
FLICK_ROOM = 1.3      # flick lengths of room needed towards the screen edge (acceleration stretches the
                      # flick; at the edge it would be cut short): the pointer is first moved away if not
PINCH_STEP_MM = 8.0  # change in finger spread per zoom step (two-finger pinch)
# Pinch out / in presses these. A mouse cannot pinch on iOS, so the default drives the Zoom magnifier
# (Accessibility > Zoom, with Keyboard Shortcuts on), which works in every app, Photos included.
ZOOM_IN = (0x0C, 46)  # Option+Cmd+=
ZOOM_OUT = (0x0C, 45)  # Option+Cmd+-
# Ctrl+pinch (Ctrl held on the keyboard) presses these instead: the app's own zoom (Safari, ...)
APP_ZOOM_IN = (0x08, 46)  # Cmd+=
APP_ZOOM_OUT = (0x08, 45)  # Cmd+-
CTRL = 0x11  # left or right Ctrl in the modifier byte
SWIPE_UP_HOME = True  # three-finger swipe up presses SWIPE_UP_BUTTON
SWIPE_UP_MM = 15.0  # three-finger swipe up this far presses SWIPE_UP_BUTTON (Home)
SWIPE_UP_BUTTON = 0x08  # mouse button 4, same as Ctrl+Alt+H
NEAR_RSSI = -45   # dBm: the phone counts as "very near" at or above this (smoothed)...
AWAY_RSSI = -55   # ...and as moved away again below this. Tune with the live panel.
GAUGE_MIN, GAUGE_MAX = -100, -25  # dBm range of the panel's gauge
REF_RSSI = -60.0  # dBm at 1 m, for the rough metre estimate: put the phone 1 m away and set this
PATH_LOSS = 2.2   # 2 = free space, 2.5-3.5 indoors
STALE_S = 4.0     # no advertisement for this long -> "phone asleep?"
HISTORY_S = 60
PANEL_ROWS = 3    # terminal rows pinned at the bottom for the proximity panel
PANEL_FPS = 5
ZONES = [(NEAR_RSSI, "VERY NEAR", 46), (-65, "NEAR", 42), (-78, "IN THE ROOM", 43), (-200, "FAR", 41)]
SPARK = " ▁▂▃▄▅▆▇█"
TAP_TIME = 0.2
TAP_TRAVEL_MM = 2.0
RECONNECT_EVERY = 10  # seconds between attempts to dial a paired device (doubling per failure, max 120)
DIAL_ONLY = False     # set when bluetoothd's input plugin holds the HID channels: devices cannot connect
DIAL_ONLY_MAX_S = 30  # to us then, so keep dialing them more often
MOUSE_INTERVAL = 0.008  # min seconds between motion reports; motion in between is merged

# Continuity-style hand-off between the PC pointer and the phone's pointer
HANDOFF_ZONES = ("top-left", "top", "top-right", "left", "right", "bottom-left", "bottom", "bottom-right")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
EDGE_MARGIN_PX = 1      # the pointer counts as "at the edge" within this many px of the last pixel
EDGE_PUSH_MM = 4.0      # finger travel against the edge before the pointer jumps to the phone
SWITCH_DELAY_S = 0.0    # ...and for at least this long
CORNER_FRAC = 0.3       # a corner zone covers this fraction of each edge next to the corner
HANDOFF_RSSI = -65      # with "only when near": the phone must be at least this strong (NEAR zone)
HANDOFF_STALE_S = 15    # ...and have been heard within this many seconds
DEFAULT_SCREEN = {"iPhone": (400, 860), "iPad": (1180, 820)}  # in mouse counts, for unknown models: phones
                                                                # upright, tablets on their side
COUNTS_PER_POINT = 1.0  # mouse counts that move the iOS pointer one point (at the default tracking speed)
GLIDE_STEP = 8          # counts per report when sliding the pointer into place after entering a device...
GLIDE_PAUSE_S = 0.08    # ...starting this long after the pin, so iOS doesn't count that burst as speed
PIN_EXTRA = 1200        # extra counts sent to pin the phone pointer against the screen edge
RETURN_PUSH = 200       # counts pushed past the phone's edge to bring the pointer back to the PC
RETURN_INSET_PX = 40    # where the pointer reappears, away from the PC screen edge
MOUSE_COUNTS_PER_MM = 40.0
CALIBRATE_RATE = 250    # counts per second while measuring a screen edge to edge
CALIBRATE_REACTION_S = 0.15  # subtract this much sweep: the time you need to press Stop
CALIBRATE_MAX_S = 25
SCAN_WHILE_CONTROLLING = False  # keep the LE proximity scan on while a device has control? It shares the
                                # radio with the HID link, so it is paused for smoother pointer movement

SOCK_PATH = os.environ.get("SIDE_CONTROL_SOCK", "/run/side-control.sock")  # GUI <-> this process
# set_options from the GUI: option -> (global it sets, type)
OPTIONS = {"natural_scroll": ("NATURAL_SCROLL", bool), "tap_to_click": ("TAP_TO_CLICK", bool),
           "share_cursor": ("SHARE_CURSOR", bool), "three_finger_switch": ("THREE_FINGER_SWITCH", bool),
           "shake_to_locate": ("SHAKE_TO_LOCATE", bool),
           "swipe_flick": ("SWIPE_FLICK", bool), "swipe_up_home": ("SWIPE_UP_HOME", bool),
           "switch_delay_ms": ("SWITCH_DELAY_S", lambda ms: max(0.0, min(2.0, float(ms) / 1000)))}

HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL, PSM_INTR = 0x11, 0x13
SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_MEDIUM = 274, 4, 2
TIOCOUTQ = 0x5411
HCIGETCONNINFO = 0x800448D5
BACKLOG_BYTES = 4096  # queued bytes (incl. kernel overhead) before motion is held back

REPORT_MAP = bytes.fromhex(
    # keyboard, report id 1: modifiers, reserved, 6 keys (+ LED output)
    "05 01 09 06 a1 01 85 01 05 07 19 e0 29 e7 15 00 25 01 75 01 95 08 81 02"
    "95 01 75 08 81 01 95 05 75 01 05 08 19 01 29 05 91 02 95 01 75 03 91 01"
    "95 06 75 08 15 00 25 65 05 07 19 00 29 65 81 00 c0"
    # mouse, report id 2: 8 buttons, x, y, wheel, AC Pan (horizontal scroll)
    "05 01 09 02 a1 01 85 02 09 01 a1 00 05 09 19 01 29 08 15 00 25 01 95 08"
    "75 01 81 02 05 01 09 30 09 31 09 38 15 81 25 7f 75 08"
    "95 03 81 06 05 0c 0a 38 02 95 01 81 06 c0 c0"
)

SDP_RECORD = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006"><sequence>
    <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
  </sequence></attribute>
  <attribute id="0x0009"><sequence>
    <sequence><uuid value="0x1124" /><uint16 value="0x0101" /></sequence>
  </sequence></attribute>
  <attribute id="0x000d"><sequence><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></sequence></attribute>
  <attribute id="0x0100"><text value="{NAME}" /></attribute>
  <attribute id="0x0101"><text value="Keyboard and Mouse" /></attribute>
  <attribute id="0x0102"><text value="Linux" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0xc0" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206"><sequence><sequence>
    <uint8 value="0x22" /><text encoding="hex" value="{REPORT_MAP.hex()}" />
  </sequence></sequence></attribute>
  <attribute id="0x0207"><sequence><sequence>
    <uint16 value="0x0409" /><uint16 value="0x0100" />
  </sequence></sequence></attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
  <attribute id="0x020f"><uint16 value="0x0010" /></attribute>
  <attribute id="0x0210"><uint16 value="0x0320" /></attribute>
</record>
"""

# evdev keycode -> HID usage
KEYMAP = {getattr(e, "KEY_" + c): 4 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
KEYMAP.update({getattr(e, "KEY_" + c): 30 + i for i, c in enumerate("1234567890")})
KEYMAP.update({getattr(e, f"KEY_F{i + 1}"): 58 + i for i in range(12)})
KEYMAP.update({
    e.KEY_ENTER: 40, e.KEY_ESC: 41, e.KEY_BACKSPACE: 42, e.KEY_TAB: 43, e.KEY_SPACE: 44,
    e.KEY_MINUS: 45, e.KEY_EQUAL: 46, e.KEY_LEFTBRACE: 47, e.KEY_RIGHTBRACE: 48,
    e.KEY_BACKSLASH: 49, e.KEY_SEMICOLON: 51, e.KEY_APOSTROPHE: 52, e.KEY_GRAVE: 53,
    e.KEY_COMMA: 54, e.KEY_DOT: 55, e.KEY_SLASH: 56, e.KEY_CAPSLOCK: 57,
    e.KEY_SYSRQ: 70, e.KEY_SCROLLLOCK: 71, e.KEY_PAUSE: 72, e.KEY_INSERT: 73,
    e.KEY_HOME: 74, e.KEY_PAGEUP: 75, e.KEY_DELETE: 76, e.KEY_END: 77, e.KEY_PAGEDOWN: 78,
    e.KEY_RIGHT: 79, e.KEY_LEFT: 80, e.KEY_DOWN: 81, e.KEY_UP: 82, e.KEY_102ND: 100,
    e.KEY_KPENTER: 88,
})
MODS = {
    e.KEY_LEFTCTRL: 0x01, e.KEY_LEFTSHIFT: 0x02, e.KEY_LEFTALT: 0x04, e.KEY_LEFTMETA: 0x08,
    e.KEY_RIGHTCTRL: 0x10, e.KEY_RIGHTSHIFT: 0x20, e.KEY_RIGHTALT: 0x40, e.KEY_RIGHTMETA: 0x80,
}
BUTTONS = {e.BTN_LEFT: 1, e.BTN_RIGHT: 2, e.BTN_MIDDLE: 4}
FINGERS = {e.BTN_TOOL_FINGER: 1, e.BTN_TOOL_DOUBLETAP: 2, e.BTN_TOOL_TRIPLETAP: 3,
           e.BTN_TOOL_QUADTAP: 4}


TERM_LOCK = threading.RLock()  # log lines and the proximity panel share the terminal
EVENTS = deque(maxlen=2000)    # log lines and RSSI readings for GUI clients; any thread may append


DEVICE_MODELS = {  # Model Number String -> marketing name (unknown ones are shown as the raw id)
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13", "iPhone14,6": "iPhone SE (3rd gen)", "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
    "iPad13,16": "iPad Air (5th gen)", "iPad13,17": "iPad Air (5th gen)",
    "iPad13,18": "iPad (10th gen)", "iPad13,19": "iPad (10th gen)",
    "iPad14,1": "iPad mini (6th gen)", "iPad14,2": "iPad mini (6th gen)",
    "iPad14,8": "iPad Air 11-inch (M2)", "iPad14,9": "iPad Air 11-inch (M2)",
    "iPad14,10": "iPad Air 13-inch (M2)", "iPad14,11": "iPad Air 13-inch (M2)",
    "iPad16,1": "iPad mini (A17 Pro)", "iPad16,2": "iPad mini (A17 Pro)",
    "iPad16,3": "iPad Pro 11-inch (M4)", "iPad16,4": "iPad Pro 11-inch (M4)",
    "iPad16,5": "iPad Pro 13-inch (M4)", "iPad16,6": "iPad Pro 13-inch (M4)",
}
SCREEN_POINTS = {  # portrait screen size in points -> the Model Number Strings that have it
    (375, 667): "iPhone9,1 iPhone9,3 iPhone10,1 iPhone10,4 iPhone12,8 iPhone14,6",
    (414, 736): "iPhone9,2 iPhone9,4 iPhone10,2 iPhone10,5",
    (375, 812): "iPhone10,3 iPhone10,6 iPhone11,2 iPhone12,3 iPhone13,1 iPhone14,4",
    (414, 896): "iPhone11,4 iPhone11,6 iPhone11,8 iPhone12,1 iPhone12,5",
    (390, 844): "iPhone13,2 iPhone13,3 iPhone14,2 iPhone14,5 iPhone14,7 iPhone17,5",
    (428, 926): "iPhone13,4 iPhone14,3 iPhone14,8",
    (393, 852): "iPhone15,2 iPhone15,4 iPhone16,1 iPhone17,3",
    (430, 932): "iPhone15,3 iPhone15,5 iPhone16,2 iPhone17,4",
    (402, 874): "iPhone17,1 iPhone18,1 iPhone18,3",
    (440, 956): "iPhone17,2 iPhone18,2",
    (420, 912): "iPhone18,4",
    (768, 1024): "iPad11,1 iPad11,2",
    (810, 1080): "iPad7,11 iPad7,12 iPad11,6 iPad11,7 iPad12,1 iPad12,2",
    (834, 1112): "iPad11,3 iPad11,4",
    (744, 1133): "iPad14,1 iPad14,2 iPad16,1 iPad16,2",
    (820, 1180): "iPad13,1 iPad13,2 iPad13,16 iPad13,17 iPad13,18 iPad13,19 iPad14,8 iPad14,9 "
                 "iPad15,3 iPad15,4 iPad15,7 iPad15,8",
    (834, 1194): "iPad8,1 iPad8,2 iPad8,3 iPad8,4 iPad8,9 iPad8,10 iPad13,4 iPad13,5 iPad13,6 iPad13,7 "
                 "iPad14,3 iPad14,4",
    (1024, 1366): "iPad8,5 iPad8,6 iPad8,7 iPad8,8 iPad8,11 iPad8,12 iPad13,8 iPad13,9 iPad13,10 iPad13,11 "
                  "iPad14,5 iPad14,6 iPad14,10 iPad14,11 iPad15,5 iPad15,6",
    (834, 1210): "iPad16,3 iPad16,4",
    (1032, 1376): "iPad16,5 iPad16,6",
}
SCREEN_OF = {model: size for size, models in SCREEN_POINTS.items() for model in models.split()}
ATT_REQUESTS = {0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E, 0x10, 0x12, 0x16, 0x18, 0x20}


GATT_MODEL, GATT_MANUFACTURER, GATT_SOFTWARE, GATT_BATTERY = 0x2A24, 0x2A29, 0x2A28, 0x2A19


def read_gatt(addr, uuids):
    """{uuid: value bytes} of the characteristics found among `uuids` (Model Number String "iPhone16,1",
    Battery Level, ...), read over ATT on BR/EDR (L2CAP PSM 31) - no LE connection or pairing change."""
    found = {}
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP) as sock:
            sock.settimeout(5)
            sock.connect((addr, 31))
            for uuid in uuids:
                sock.send(struct.pack("<BHHH", 0x08, 1, 0xFFFF, uuid))  # Read By Type
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pkt = sock.recv(512)
                    op = pkt[0]
                    if op == 0x09:  # Read By Type response: len, then handle(2) + value
                        found[uuid] = pkt[4:2 + pkt[1]]
                        break
                    if op == 0x01 and pkt[1] == 0x08:  # our request failed: not there
                        break
                    if op == 0x1D:  # the phone's own indications (Service Changed) want a confirmation
                        sock.send(b"\x1e")
                    elif op in ATT_REQUESTS:  # ... and its requests an answer, even if "not supported"
                        sock.send(struct.pack("<BBHB", 0x01, op, 0, 0x06))
    except (OSError, IndexError):
        pass
    return found


def read_phone_model(addr):
    value = read_gatt(addr, [GATT_MODEL]).get(GATT_MODEL)
    return value.decode(errors="replace") if value else None


def log(msg):
    stamp = time.strftime("%H:%M:%S")
    EVENTS.append({"type": "log", "time": stamp, "msg": msg})
    with TERM_LOCK:
        print(stamp, msg, flush=True)


# --- BlueZ -----------------------------------------------------------------

class Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, props):
        os.close(fd.take())

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, path):
        pass


class Agent(dbus.service.Object):
    """Accepts every pairing request (Just Works)."""

    def trust(self, path):
        props = dbus.Interface(self.connection.get_object("org.bluez", path),
                               "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Device1", "Trusted", True)
        log(f"paired/trusted {props.Get('org.bluez.Device1', 'Alias')}")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        self.trust(device)
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        self.trust(device)
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        log(f"PIN: {pincode}")

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        log(f"passkey: {passkey:06d}")

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        self.trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        self.trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        pass


def setup_bluez(adapter_name=None):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    objects = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
    adapters = sorted((str(p), i["org.bluez.Adapter1"]) for p, i in objects.items()
                      if "org.bluez.Adapter1" in i)
    path, info = next(((p, i) for p, i in adapters if p.endswith("/" + (adapter_name or ""))), adapters[0])

    profile_path, agent_path = "/io/github/sidecontrol/profile", "/io/github/sidecontrol/agent"
    Profile(bus, profile_path)
    Agent(bus, agent_path)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.ProfileManager1")
    manager.RegisterProfile(profile_path, HID_UUID, {
        "ServiceRecord": SDP_RECORD, "Role": "server",
        "RequireAuthentication": False, "RequireAuthorization": False,
    })
    agents = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    agents.RegisterAgent(agent_path, "NoInputNoOutput")
    agents.RequestDefaultAgent(agent_path)

    adapter = dbus.Interface(bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
    adapter.Set("org.bluez.Adapter1", "Powered", True)
    adapter.Set("org.bluez.Adapter1", "Alias", NAME)
    adapter.Set("org.bluez.Adapter1", "Pairable", True)
    adapter.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
    adapter.Set("org.bluez.Adapter1", "Discoverable", True)
    # Peripheral / keyboard+pointing device, so iOS lists it under Bluetooth settings
    index = path.rsplit("hci", 1)[1]
    subprocess.run(["btmgmt", "--index", index, "class", "5", "192"],
                   stdout=subprocess.DEVNULL, check=False)

    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
    return str(info["Address"]), bus, path


def paired_devices(bus, adapter_path):
    """Addresses of devices paired with this adapter; marks them trusted so they may reconnect."""
    objects = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
    found = []
    for path, info in objects.items():
        dev = info.get("org.bluez.Device1")
        if not dev or dev.get("Adapter") != adapter_path or not dev.get("Paired"):
            continue
        if not dev.get("Trusted"):
            dbus.Interface(bus.get_object("org.bluez", path),
                           "org.freedesktop.DBus.Properties").Set("org.bluez.Device1", "Trusted", True)
        found.append((str(dev["Address"]), str(dev.get("Alias", dev["Address"])),
                      str(dev.get("Icon", "")), vendor_of(dev.get("Modalias", ""))))
    return found


def vendor_of(modalias):
    """Bluetooth SIG company id from a BlueZ Modalias like "bluetooth:v004Cp7213d1100" (0x004C: Apple)."""
    try:
        return int(str(modalias).split(":v", 1)[1][:4], 16)
    except (IndexError, ValueError):
        return None


def nearby_devices(bus, adapter_path):
    """Unpaired phones and tablets found by discovery: [{address, name, os, form}]"""
    objects = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
    found = []
    for info in objects.values():
        dev = info.get("org.bluez.Device1")
        if not dev or dev.get("Adapter") != adapter_path or dev.get("Paired") or "Name" not in dev:
            continue
        cls = int(dev.get("Class", 0))
        major, minor = (cls >> 8) & 0x1F, (cls >> 2) & 0x3F
        icon, name = str(dev.get("Icon", "")), str(dev["Name"])
        apple = vendor_of(dev.get("Modalias", "")) == 0x004C or name.lower().startswith(("iphone", "ipad"))
        if major == 2 or icon == "phone":
            form = "phone"
        elif (major == 1 and minor in (4, 5, 7)) or (apple and "ipad" in name.lower()):
            form = "tablet"
        else:
            continue
        found.append({"address": str(dev["Address"]), "name": name, "form": form,
                      "os": ("iPadOS" if form == "tablet" else "iOS") if apple else "Android"})
    return sorted(found, key=lambda d: d["name"].lower())


def listen(addr, psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    s.bind((addr, psm))
    s.listen(4)
    return s


def dial(peer, psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    s.settimeout(5)
    try:
        s.connect((peer, psm))
    except OSError:
        s.close()
        raise
    s.settimeout(None)
    return s


def disable_sniff(dev_id, peer, quiet=False):
    """Stop the link to `peer` from entering sniff (power-save) mode, which delays every report."""
    hci = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        hci.bind((dev_id,))
        # struct hci_conn_info_req (bdaddr, type=ACL, pad) followed by struct hci_conn_info
        req = bytearray(bytes.fromhex(peer.replace(":", ""))[::-1] + b"\x01" + bytes(17))
        fcntl.ioctl(hci, HCIGETCONNINFO, req)
        handle = struct.unpack_from("<H", req, 8)[0]

        def command(ocf, params):  # OGF 0x02: link policy commands
            hci.send(b"\x01" + struct.pack("<HB", (0x02 << 10) | ocf, len(params)) + params)

        command(0x000D, struct.pack("<HH", handle, 0x0001))  # policy: role switch only, no sniff
        command(0x0004, struct.pack("<H", handle))  # exit sniff if already in it
        if not quiet:
            log("power-save (sniff) disabled for this link")
    except OSError as err:
        log(f"could not disable sniff mode: {err}")
    finally:
        hci.close()


# --- devices ----------------------------------------------------------------

class NoPhone:
    """Stand-in output device while nothing has been connected yet."""
    connected = False
    peer = address = None

    def send(self, *args):
        pass

    def backlogged(self):
        return False


NO_PHONE = NoPhone()


class Phone:
    """One paired iPhone or iPad: its HID channels plus its own hand-off settings."""

    def __init__(self, dev_id, address, alias):
        self.dev_id, self.address, self.alias = dev_id, address, alias
        self.ctrl = self.intr = None
        self.peer = None   # == address while connected
        self.model = None  # e.g. "iPhone16,1", read once per connection
        self.manufacturer = self.software = None
        self.battery = None  # percent, read every BATTERY_EVERY s while connected
        self.battery_t = 0.0
        self.icon, self.vendor = "", None  # from BlueZ
        self.dialing = False
        self.dial_fails = 0
        self.next_dial = 0.0
        # hand-off settings, pushed by the GUI
        self.zone = None
        self.span = None  # (lo, hi) fraction of a side edge that leads here; None: all of it
        self.require_near = True
        self.screen_w = self.screen_h = None  # None: default for the device kind
        self.speed = 1.0                      # pointer speed multiplier

    @property
    def apple(self):
        if self.vendor is not None:
            return self.vendor == 0x004C
        if self.manufacturer:
            return self.manufacturer.lower().startswith("apple")
        return True  # until we know better: iPhones and iPads were all this supported

    @property
    def form(self):
        if self.apple:
            return "tablet" if "ipad" in (self.model or self.alias).lower() else "phone"
        return "phone" if self.icon in ("phone", "") else "tablet"

    @property
    def os(self):
        return ("iPadOS" if self.form == "tablet" else "iOS") if self.apple else "Android"

    @property
    def kind(self):  # sizes are keyed by these
        return "iPad" if self.form == "tablet" else "iPhone"

    def size(self):
        """Screen size in mouse counts, held the way the arrangement shows it: phones upright, tablets
        on their side. Known models use their real screen; Bluetooth itself doesn't tell."""
        points = SCREEN_OF.get(self.model)
        if points:
            short, long = (round(v * COUNTS_PER_POINT) for v in points)
            w, h = (long, short) if self.form == "tablet" else (short, long)
        else:
            w, h = DEFAULT_SCREEN[self.kind]
        return self.screen_w or w, self.screen_h or h

    @property
    def model_name(self):
        return DEVICE_MODELS.get(self.model, self.model)

    def _read_model(self):
        info = read_gatt(self.address, [GATT_MODEL, GATT_MANUFACTURER, GATT_SOFTWARE, GATT_BATTERY])
        if self.peer != self.address:
            return
        text = lambda uuid: info[uuid].decode(errors="replace").strip("\0 ") if uuid in info else None  # noqa: E731
        self.manufacturer, self.software = text(GATT_MANUFACTURER), text(GATT_SOFTWARE)
        self._set_battery(info.get(GATT_BATTERY))
        model = text(GATT_MODEL)
        if model:
            self.model = model
            log(f"{self.alias}: {self.model_name}"
                + (f" ({model})" if self.model_name != model else ""))

    def _set_battery(self, value):
        self.battery_t = time.monotonic()
        if value:
            self.battery = min(100, value[0])

    def _read_battery(self):
        self._set_battery(read_gatt(self.address, [GATT_BATTERY]).get(GATT_BATTERY))

    def refresh_battery(self):
        self.battery_t = time.monotonic()
        threading.Thread(target=self._read_battery, daemon=True).start()

    def connected_to(self):
        self.peer = self.address
        self.model = None
        disable_sniff(self.dev_id, self.address)
        threading.Thread(target=self._read_model, daemon=True).start()

    @property
    def connected(self):
        return self.ctrl is not None and self.intr is not None

    def send(self, report_id, payload):
        if not self.connected:
            return
        try:
            self.intr.send(bytes([0xA1, report_id]) + bytes(payload))
        except OSError as err:
            log(f"send failed: {err}")
            self.close()

    def backlogged(self):
        """True while earlier reports pile up in the kernel's send queue."""
        try:
            # On Bluetooth sockets TIOCOUTQ is the *free* send-buffer space
            free = struct.unpack("i", fcntl.ioctl(self.intr, TIOCOUTQ, b"\0" * 4))[0]
            sndbuf = self.intr.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        except OSError:
            return False
        return sndbuf - free > BACKLOG_BYTES

    def handle_ctrl(self):
        try:
            msg = self.ctrl.recv(64)
        except OSError:
            msg = b""
        if not msg:
            self.close()
            return
        kind = msg[0] >> 4
        if kind == 0x4:  # GET_REPORT
            rid = msg[1] if len(msg) > 1 else 1
            self.ctrl.send(bytes([0xA1, rid]) + bytes(8 if rid == 1 else 5))
        elif kind == 0x6:  # GET_PROTOCOL -> report protocol
            self.ctrl.send(b"\xa0\x01")
        elif kind == 0x1 and msg[0] & 0x0F == 0x5:  # VIRTUAL_CABLE_UNPLUG
            self.close()
        elif kind in (0x5, 0x7, 0x9):  # SET_REPORT / SET_PROTOCOL / SET_IDLE
            self.ctrl.send(b"\x00")

    def dial_hid(self, out):
        """Thread body: connect to the device ourselves, like a real keyboard does after power-on.
        Paging an absent device blocks for seconds, so this must not run on the input loop."""
        try:
            ctrl = dial(self.address, PSM_CTRL)
            try:
                intr = dial(self.address, PSM_INTR)
            except OSError as err:
                ctrl.close()
                log(f"{self.alias}: interrupt channel failed: {err}")
                raise
            self.dial_fails = 0
            out.append((self, ctrl, intr))
        except OSError:
            self.dial_fails += 1
            self.next_dial = time.monotonic() + min(RECONNECT_EVERY * 2 ** self.dial_fails,
                                                    DIAL_ONLY_MAX_S if DIAL_ONLY else 120)
        finally:
            self.dialing = False

    def close(self):
        if self.ctrl or self.intr:
            log(f"{self.alias} disconnected")
        for s in (self.ctrl, self.intr):
            if s:
                s.close()
        self.ctrl = self.intr = self.peer = self.model = None
        self.battery = None


class Fleet:
    """Every paired iPhone / iPad, connected or not, keyed by Bluetooth address."""

    def __init__(self, dev_id):
        self.dev_id = dev_id
        self.phones = {}
        self.dialed = deque()  # (phone, ctrl, intr) handed over by dial threads
        self.dial_now = False   # set after pairing: dial without waiting for RECONNECT_EVERY

    def get(self, address, alias=None):
        phone = self.phones.get(address)
        if phone is None:
            phone = self.phones[address] = Phone(self.dev_id, address, alias or address)
        elif alias:
            phone.alias = alias
        return phone

    def sync_paired(self, paired):
        for address, alias, icon, vendor in paired:
            phone = self.get(address, alias)
            phone.icon, phone.vendor = icon, vendor

    def connected(self):
        return [p for p in self.phones.values() if p.connected]

    def dial_missing(self):
        now = time.monotonic()
        for phone in self.phones.values():
            if not (phone.connected or phone.dialing or now < phone.next_dial):
                phone.dialing = True
                threading.Thread(target=phone.dial_hid, args=(self.dialed,), daemon=True).start()

    def accept_dialed(self):
        while self.dialed:
            phone, ctrl, intr = self.dialed.popleft()
            # it connected to us in the meantime, or was forgotten
            if phone.connected or self.phones.get(phone.address) is not phone:
                ctrl.close()
                intr.close()
                continue
            phone.close()
            phone.ctrl, phone.intr = ctrl, intr
            phone.connected_to()
            log(f"connected to {phone.alias} - press Ctrl+Alt+P to control it")


# --- proximity -------------------------------------------------------------

def signal_color(rssi):
    """256-colour red -> yellow -> green along the gauge."""
    f = max(0.0, min(1.0, (rssi - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN)))
    return 196 if f < 0.2 else 208 if f < 0.4 else 220 if f < 0.6 else 154 if f < 0.8 else 46


def zone_for(value):
    return next((name, code) for limit, name, code in ZONES if value >= limit)


def distance_for(avg):
    return 10 ** ((REF_RSSI - avg) / (10 * PATH_LOSS))


def gauge_text(value, width):
    span = GAUGE_MAX - GAUGE_MIN
    filled = int(max(0.0, min(1.0, (value - GAUGE_MIN) / span)) * width)
    return "".join(
        f"\x1b[38;5;{signal_color(GAUGE_MIN + (i + 0.5) / width * span)}m█\x1b[0m" if i < filled
        else "\x1b[2m░\x1b[0m" for i in range(width))


class Signal:
    """Smoothed signal strength of one device."""

    def __init__(self, alias):
        self.alias = alias
        self.target = self.shown = self.last = None  # smoothed, displayed (eased), latest raw
        self.last_t = 0.0
        self.times = deque()  # (monotonic time, rssi)
        self.lo = self.hi = None
        self.near = False

    def rate(self):
        now = time.monotonic()
        recent = sum(1 for t, _ in self.times if now - t <= 10)
        span = min(10.0, now - self.times[0][0]) if self.times else 0
        return recent / span if span > 1 else 0.0

    def history(self, width):
        now = time.monotonic()
        buckets = [[] for _ in range(width)]
        for t, v in self.times:
            idx = width - 1 - int((now - t) * width / HISTORY_S)
            if 0 <= idx < width:
                buckets[idx].append(v)
        out, level = [], None
        for b in buckets:
            if b:
                level = sum(b) / len(b)
            if level is None:
                out.append(" ")
                continue
            f = (level - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN)
            out.append(f"\x1b[38;5;{signal_color(level)}m{SPARK[max(1, min(8, round(f * 8)))]}\x1b[0m")
        return "".join(out)


class Proximity:
    """How close each paired device is, from the RSSI of its Bluetooth LE advertisements.

    Classic Bluetooth only reports RSSI relative to an "ideal range" (0 almost everywhere),
    but BlueZ resolves a bonded device's LE advertisements to the same address and exposes
    their absolute signal strength in dBm: roughly -30 with the device touching the laptop,
    -50 at arm's length, below -70 across a room (varies with the device and its position).
    Devices advertise about every 0.5 s while awake; each one updates the panel pinned to
    the bottom of the terminal (one device: gauge, zone, rough distance, 60 s history; several:
    one line each).
    """

    def __init__(self, bus, adapter_path):
        self.bus, self.adapter_path = bus, adapter_path
        self.paired = {}   # device path -> alias
        self.signals = {}  # address -> Signal
        self.size = None
        self.ticks = 0
        self.panel = sys.stdout.isatty()
        self.scanning = False

    def start(self):
        if not self._scan(True):
            self.panel = False
            return
        self.refresh_paired()
        self.bus.add_signal_receiver(self.on_props, "PropertiesChanged",
                                     "org.freedesktop.DBus.Properties", path_keyword="path")
        GLib.timeout_add(1000 // PANEL_FPS, self.tick)  # runs in setup_bluez's GLib thread
        atexit.register(self.stop)

    def _scan(self, on):
        adapter = dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                                 "org.bluez.Adapter1")
        try:
            if on:
                # LE only: a classic inquiry would disturb the HID link; duplicates make BlueZ
                # report the RSSI of every advertisement, not just the first
                adapter.SetDiscoveryFilter({"Transport": "le", "DuplicateData": True})
                adapter.StartDiscovery()
            else:
                adapter.StopDiscovery()
        except dbus.DBusException as err:
            if err.get_dbus_name() not in ("org.bluez.Error.InProgress", "org.bluez.Error.Failed"):
                log(f"proximity scan {'start' if on else 'stop'} failed: {err.get_dbus_message()}")
                return False
        self.scanning = on
        return True

    def set_transport(self, transport):
        """"le" for proximity only, "auto" to also find unpaired classic devices for pairing."""
        def apply():
            adapter = dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path), "org.bluez.Adapter1")
            try:
                if self.scanning:
                    adapter.StopDiscovery()
                adapter.SetDiscoveryFilter({"Transport": transport, "DuplicateData": transport == "le"})
                adapter.StartDiscovery()
                self.scanning = True
            except dbus.DBusException as err:
                log(f"discovery ({transport}): {err.get_dbus_message()}")
            return False
        GLib.idle_add(apply)

    def set_scanning(self, on):
        """Pause / resume the LE scan. Scanning shares the radio with the HID link, so it is paused
        while a device has control. Runs on the GLib thread, where the other D-Bus work happens."""
        if on == self.scanning:
            return
        if not on:
            log("proximity scan paused while a device has control (keeps the radio free for the pointer)")
        GLib.idle_add(lambda: (self._scan(on), False)[1])

    def refresh_paired(self):
        objects = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
        self.paired = {str(p): str(i["org.bluez.Device1"].get("Alias", "device"))
                       for p, i in objects.items()
                       if "org.bluez.Device1" in i
                       and i["org.bluez.Device1"].get("Adapter") == self.adapter_path
                       and i["org.bluez.Device1"].get("Paired")}

    def on_props(self, iface, changed, invalidated, path=None):
        if iface == "org.bluez.Device1" and "RSSI" in changed and str(path) in self.paired:
            address = str(path).rsplit("dev_", 1)[1].replace("_", ":")
            self.reading(address, int(changed["RSSI"]), self.paired[str(path)])

    def reading(self, address, rssi, alias):
        now = time.monotonic()
        with TERM_LOCK:
            sig = self.signals.setdefault(address, Signal(alias))
            sig.alias, sig.last, sig.last_t = alias, rssi, now
            sig.target = rssi if sig.target is None else 0.5 * sig.target + 0.5 * rssi
            if sig.shown is None:
                sig.shown = sig.target
            sig.times.append((now, rssi))
            while now - sig.times[0][0] > HISTORY_S:
                sig.times.popleft()
            sig.lo = rssi if sig.lo is None else min(sig.lo, rssi)
            sig.hi = rssi if sig.hi is None else max(sig.hi, rssi)
            EVENTS.append({"type": "rssi", "address": address, "alias": alias, "rssi": rssi,
                           "avg": round(sig.target, 1), "zone": zone_for(sig.target)[0],
                           "distance": round(distance_for(sig.target), 2)})
            if not sig.near and sig.target >= NEAR_RSSI:
                sig.near = True
                log(f"*** {alias} is VERY NEAR ({sig.target:.0f} dBm) ***")
            elif sig.near and sig.target < AWAY_RSSI:
                sig.near = False
                log(f"{alias} moved away ({sig.target:.0f} dBm)")

    # --- panel ---

    def full_lines(self, sig, cols):
        """Three rows for a single device: header, gauge, 60 s history."""
        width = max(20, min(cols - 4, 100))
        age = time.monotonic() - sig.last_t
        sig.shown += (sig.target - sig.shown) * 0.4
        name, code = zone_for(sig.shown)
        asleep = "  \x1b[2;33mno recent signal - asleep?\x1b[0m" if age > STALE_S else ""
        head = (f" \x1b[1m{sig.alias}\x1b[0m {sig.last:4d} dBm \x1b[30;{code}m {name} \x1b[0m"
                f" ≈ {distance_for(sig.target):.1f} m \x1b[2m(rough)  min {sig.lo} max {sig.hi}  "
                f"{sig.rate():.1f}/s  {age:.1f}s ago\x1b[0m{asleep}")
        return [head, " " + gauge_text(sig.shown, width), " " + sig.history(width)]

    def compact_line(self, sig, cols):
        age = time.monotonic() - sig.last_t
        sig.shown += (sig.target - sig.shown) * 0.4
        name, code = zone_for(sig.shown)
        width = max(10, min(cols - 58, 40))
        asleep = " \x1b[2;33mzzz\x1b[0m" if age > STALE_S else ""
        return (f" \x1b[1m{sig.alias[:16]:<16}\x1b[0m {sig.last:4d} dBm \x1b[30;{code}m {name:^11} \x1b[0m"
                f" ≈{distance_for(sig.target):4.1f} m {gauge_text(sig.shown, width)}{asleep}")

    def lines(self, cols):
        sigs = sorted(self.signals.values(), key=lambda s: s.alias)
        if not sigs:
            return [" \x1b[2mproximity: waiting for the Bluetooth LE advertisements...\x1b[0m", "", ""]
        if len(sigs) == 1:
            return self.full_lines(sigs[0], cols)
        rows = [self.compact_line(s, cols) for s in sigs[:PANEL_ROWS]]
        return rows + [""] * (PANEL_ROWS - len(rows))

    def tick(self):
        if not self.panel:
            return True
        self.ticks += 1
        if self.ticks % (PANEL_FPS * 10) == 0:  # pick up a device paired after startup
            self.refresh_paired()
        cols, rows = shutil.get_terminal_size((80, 24))
        if rows < PANEL_ROWS + 8:
            return True
        with TERM_LOCK:
            out = ["\x1b7"]  # save cursor
            if self.size != (cols, rows):
                if self.size is None:
                    out.append("\n" * PANEL_ROWS + f"\x1b[{PANEL_ROWS}A")  # make room, keep position
                out.append(f"\x1b[1;{rows - PANEL_ROWS}r")  # log lines scroll above the panel
                self.size = (cols, rows)
            for k, line in enumerate(self.lines(cols)):
                out.append(f"\x1b[{rows - PANEL_ROWS + 1 + k};1H{line}\x1b[K")
            out.append("\x1b8")  # restore cursor
            sys.stdout.write("".join(out))
            sys.stdout.flush()
        return True

    def stop(self):
        with TERM_LOCK:
            if self.panel and self.size:
                cols, rows = self.size
                sys.stdout.write("\x1b[r" + "".join(f"\x1b[{rows - PANEL_ROWS + 1 + k};1H\x1b[K"
                                                     for k in range(PANEL_ROWS))
                                 + f"\x1b[{rows};1H\n")
                sys.stdout.flush()
            self.panel = False
        try:
            dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                           "org.bluez.Adapter1").StopDiscovery()
        except dbus.DBusException:
            pass


# --- hand-off --------------------------------------------------------------

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


def handoff_exits(zone, span=None):
    """[(screen edge, predicate on the position along that edge, 0..1)] that lead to the phone.

    The fraction runs top -> bottom along the left/right edges and left -> right along top/bottom.
    span (lo, hi) limits a side zone to that part of its edge, so several devices can share one.
    """
    c = CORNER_FRAC
    near_start, near_end = (lambda f: f <= c), (lambda f: f >= 1 - c)
    lo, hi = span or (0.0, 1.0)
    anywhere = lambda f: lo <= f <= hi  # noqa: E731
    return {
        "left": [("left", anywhere)], "right": [("right", anywhere)],
        "top": [("top", anywhere)], "bottom": [("bottom", anywhere)],
        "top-left": [("left", near_start), ("top", near_start)],
        "top-right": [("right", near_start), ("top", near_end)],
        "bottom-left": [("left", near_end), ("bottom", near_start)],
        "bottom-right": [("right", near_end), ("bottom", near_end)],
    }[zone]


class Handoff:
    """Like Apple's Continuity: push the pointer off the screen edge towards a device and it
    appears on that device; push it off the device's opposite edge and it comes back.

    Each device has its own zone (where it sits around the screen), "only when near" flag and
    screen size, so an iPhone on the left and an iPad on the right can both be reached.

    PC -> device: Bridge feeds finger motion in here while the PC has control. When the pointer sits
    on a configured edge (found via Hyprland) and the finger keeps pushing, and - if asked - the
    device is near enough (Proximity), control switches and the device's pointer is pinned against
    the side it enters from, at the same relative height.

    device -> PC: the device never reports its pointer position, so we keep a model of it: it is known
    right after the pin, and every report we send moves it (clamped to the screen size). Pushing past
    the entry edge brings control back and warps the PC pointer to the matching spot.
    """

    def __init__(self, bridge, fleet, proximity, compositor):
        self.bridge, self.fleet, self.proximity, self.comp = bridge, fleet, proximity, compositor
        self.push = {}    # (address, edge) -> mm of finger travel against that edge
        self.push_t = {}  # (address, edge) -> when that push started
        self.edge = None  # PC edge the pointer left through, while a device has control
        self.entry_frac = 0.5
        self.vx = self.vy = 0.0  # modelled device pointer, counts from the top-left
        self.return_push = 0.0
        self.blocked_logged = 0.0

    def configure(self, phone, zone, require_near, screen_w, screen_h, speed=None, span=None):
        phone.zone = zone if zone in HANDOFF_ZONES else None
        phone.span = (max(0.0, min(1.0, float(span[0]))), max(0.0, min(1.0, float(span[1])))) \
            if span else None
        phone.require_near = bool(require_near)
        # 0 / None: use the default for this kind of device
        phone.screen_w = max(100, min(3000, int(screen_w))) if screen_w else None
        phone.screen_h = max(100, min(3000, int(screen_h))) if screen_h else None
        phone.speed = max(0.3, min(4.0, float(speed))) if speed else 1.0
        self.push.clear()
        self.push_t.clear()
        log(f"hand-off {phone.alias}: at the {phone.zone} of the screen"
            + (" (only when near)" if phone.require_near else "") if phone.zone
            else f"hand-off {phone.alias}: off")
        if phone.zone:
            lo, hi = phone.span or (0.0, 1.0)
            clash = next((o.alias for o in self.fleet.phones.values()
                          if o is not phone and o.zone == phone.zone
                          and lo < (o.span or (0.0, 1.0))[1] and (o.span or (0.0, 1.0))[0] < hi), None)
            if clash:
                log(f"hand-off: {clash} is also set to the {phone.zone}; the first one found wins")
            if not self.comp.available:
                log("hand-off needs Hyprland: no IPC socket found, the PC pointer cannot be located")

    def status(self):
        return {"available": self.comp.available,
                "edge": self.edge if self.bridge.on_phone else None}

    # --- PC -> device ---

    def near_ok(self, phone):
        if not phone.require_near:
            return True
        sig = self.proximity.signals.get(phone.address)
        now = time.monotonic()
        near = (sig is not None and now - sig.last_t < HANDOFF_STALE_S
                and sig.target >= HANDOFF_RSSI)
        if not near and now - self.blocked_logged > 5:
            self.blocked_logged = now
            seen = "no signal" if sig is None else f"{sig.target:.0f} dBm"
            log(f"hand-off to {phone.alias} blocked: not near ({seen}, need {HANDOFF_RSSI} dBm or stronger)")
        return near

    def pc_motion(self, mx, my):
        """Finger / mouse motion in mm (x right, y down) while the PC has control."""
        if self.bridge.on_phone or not SHARE_CURSOR or self.bridge.locked:
            return
        phones = [p for p in self.fleet.connected() if p.zone]
        if not phones:
            return
        pos, box = self.comp.cursor(), self.comp.layout()
        if not pos or not box:
            return
        (x, y), (left, top, right, bottom) = pos, box
        m = EDGE_MARGIN_PX
        at = {"left": x <= left + m, "right": x >= right - 1 - m,
              "top": y <= top + m, "bottom": y >= bottom - 1 - m}
        toward = {"left": -mx, "right": mx, "top": -my, "bottom": my}
        along = {"left": (y - top) / max(1, bottom - top), "right": (y - top) / max(1, bottom - top),
                 "top": (x - left) / max(1, right - left), "bottom": (x - left) / max(1, right - left)}
        for phone in phones:
            for edge, in_zone in handoff_exits(phone.zone, phone.span):
                key = (phone.address, edge)
                if at[edge] and in_zone(along[edge]):
                    if not self.push.get(key):
                        self.push_t[key] = time.monotonic()
                    self.push[key] = max(0.0, self.push.get(key, 0.0) + toward[edge])
                    if (self.push[key] >= EDGE_PUSH_MM and self.near_ok(phone)
                            and time.monotonic() - self.push_t[key] >= SWITCH_DELAY_S):
                        self.enter(phone, edge, along[edge])
                        return
                else:
                    self.push[key] = 0.0

    def enter(self, phone, edge, frac):
        self.edge, self.entry_frac = edge, frac
        self.bridge.switch_to(phone)
        if not self.bridge.on_phone:  # device vanished meanwhile
            self.edge = None

    def on_enter(self):
        """Called by Bridge.switch_to when a device takes over: pin its pointer where it comes in."""
        phone = self.bridge.phone
        if not phone.zone:
            return
        if self.edge is None:  # manual switch (hotkey / GUI): enter as if through the zone's first edge
            self.edge = handoff_exits(phone.zone)[0][0]
            self.entry_frac = sum(phone.span) / 2 if phone.span and phone.zone in OPPOSITE else 0.5
        w, h = phone.size()
        frac = self.entry_frac
        # the pointer enters through the device side opposite to the PC edge it left by; pin it into
        # the nearer corner of that side first (the device clamps at its screen edges), then glide
        # along the side. Sent in one burst, iOS pointer acceleration would carry the pointer much
        # further than the counts say, and it would come in at the wrong height
        sx, sy = {"left": (1, 0), "right": (-1, 0), "top": (0, 1), "bottom": (0, -1)}[self.edge]
        end = 1 if frac > 0.5 else -1
        sx, sy = (sx, end) if sx else (end, sy)
        self.bridge.send_mouse(sx * (w + PIN_EXTRA), sy * (h + PIN_EXTRA))
        self.vx, self.vy = (w if sx > 0 else 0.0), (h if sy > 0 else 0.0)
        if self.edge in ("left", "right"):
            move = round(frac * h - self.vy)
            self.bridge.glide(0, move)
            self.vy += move
        else:
            move = round(frac * w - self.vx)
            self.bridge.glide(move, 0)
            self.vx += move
        self.return_push = 0.0
        log(f"pointer entered {phone.alias} from its {OPPOSITE[self.edge]} side "
            f"(push past that side to come back)")

    # --- device -> PC ---

    def phone_motion(self, ix, iy):
        """Counts just sent to the device while it has control."""
        if self.edge is None or not self.bridge.on_phone or self.bridge.locked:
            return
        w, h = self.bridge.phone.size()
        self.vx = max(0.0, min(w, self.vx + ix))
        self.vy = max(0.0, min(h, self.vy + iy))
        back = OPPOSITE[self.edge]  # device side that leads back to the PC
        at = {"left": self.vx <= 0, "right": self.vx >= w, "top": self.vy <= 0, "bottom": self.vy >= h}[back]
        toward = {"left": -ix, "right": ix, "top": -iy, "bottom": iy}[back]
        if at and toward > 0:
            self.return_push += toward
            if self.return_push >= RETURN_PUSH:
                self.leave()
        elif not at or toward < 0:
            self.return_push = 0.0

    def leave(self):
        edge, back = self.edge, OPPOSITE[self.edge]
        w, h = self.bridge.phone.size()
        frac = (self.vy / h) if back in ("left", "right") else (self.vx / w)
        self.bridge.switch_to(None)  # -> PC; on_leave() clears the state
        box = self.comp.layout()
        if not box:
            return
        left, top, right, bottom = box
        i = RETURN_INSET_PX
        x = {"left": left + i, "right": right - 1 - i}.get(edge, left + frac * (right - left))
        y = {"top": top + i, "bottom": bottom - 1 - i}.get(edge, top + frac * (bottom - top))
        self.comp.warp(x, y)
        log(f"pointer returned to the PC at its {edge} edge")

    def on_leave(self):
        self.edge = None
        self.push.clear()


# --- GUI control -----------------------------------------------------------

class Control:
    """Newline-delimited JSON over a unix socket, so a GUI running as the normal user can
    watch and drive this root process.

    server -> client: {"type": "state", ...}  every device with its settings and signal, who has
                                              control, captured inputs; about once a second or on change
                      {"type": "rssi", ...}   every proximity reading (with the device's address)
                      {"type": "log", ...}    every log line
    client -> server: {"cmd": "set_target", "address": "AA:..." | null}   give control to a device / the PC
                      {"cmd": "set_handoff", "address": ..., "zone": ..., "require_near": ...,
                       "phone_w": ..., "phone_h": ..., "span": [lo, hi] | null}
                      {"cmd": "action", "address": ..., "key": "h"}   press the Ctrl+Alt+<key> button
                      {"cmd": "forget", "address": ...}   unpair a device
                      {"cmd": "set_options", "natural_scroll": true, ...}   see OPTIONS, plus
                       "device_name" (adapter alias), "discoverable" and "locked" (Scroll Lock)
                      {"cmd": "discover", "on": true}   look for unpaired phones and tablets
                      {"cmd": "pair", "address": ...}   pair one of them
                      {"cmd": "quit"}
    server -> client also: {"type": "paired", "address": ..., "ok": ..., "error": ...}
                           {"type": "shake"}  the PC pointer was shaken
                           {"type": "open"}   Super+Alt+D: show the GUI
    """

    def __init__(self, bridge, fleet, proximity, bus=None, adapter_path=None):
        self.bridge, self.fleet, self.proximity = bridge, fleet, proximity
        self.bus, self.adapter_path = bus, adapter_path
        self.discovering = False
        self.nearby, self.nearby_t = [], 0.0
        self.pairing = None
        self.quit = False
        self.listener = None
        self.clients = {}  # socket -> unparsed input
        self.last_state = None
        self.last_state_t = 0.0
        self.calib = None  # {"phone", "axis", "t0", "sent"} while measuring a screen edge to edge

    def start(self):
        try:
            os.unlink(SOCK_PATH)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(SOCK_PATH)
        uid = int(os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or -1)
        if uid >= 0:  # only the user who started us may talk to us
            os.chown(SOCK_PATH, uid, -1)
        os.chmod(SOCK_PATH, 0o600)
        listener.listen(4)
        listener.setblocking(False)
        self.listener = listener
        atexit.register(lambda: os.path.exists(SOCK_PATH) and os.unlink(SOCK_PATH))

    def owns(self, sock):
        return sock is self.listener or sock in self.clients

    def sockets(self):
        return [self.listener, *self.clients]

    def handle(self, sock):
        if sock is self.listener:
            conn, _ = sock.accept()
            conn.setblocking(False)
            self.clients[conn] = b""
            self.send(conn, self.state())
            return
        try:
            data = sock.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.drop(sock)
            return
        *lines, self.clients[sock] = (self.clients[sock] + data).split(b"\n")
        for line in lines:
            try:
                self.command(json.loads(line))
            except (ValueError, KeyError, TypeError) as err:
                log(f"bad GUI command {line[:60]!r}: {err!r}")
        self.last_state = None  # report the effect right away

    def command(self, msg):
        cmd = msg["cmd"]
        if cmd == "set_target":
            address = msg.get("address")
            target = self.fleet.phones.get(address) if address else None
            if address and target is None:
                log(f"GUI asked for unknown device {address}")
            else:
                self.bridge.switch_to(target)
        elif cmd == "set_handoff":
            # no phone_w / phone_h: the screen of the device's model, once it is known
            phone = self.fleet.get(msg["address"])
            self.bridge.handoff.configure(phone, msg.get("zone"), msg.get("require_near", True),
                                          msg.get("phone_w"), msg.get("phone_h"),
                                          msg.get("speed"), msg.get("span"))
        elif cmd == "forget":
            self.forget(msg["address"])
        elif cmd == "set_options":
            for key, value in msg.items():
                if key in OPTIONS:
                    name, convert = OPTIONS[key]
                    globals()[name] = convert(value)
            if msg.get("device_name"):
                self.set_adapter("Alias", dbus.String(str(msg["device_name"])[:64]))
            if "discoverable" in msg:
                self.set_adapter("Discoverable", dbus.Boolean(bool(msg["discoverable"])))
            if "locked" in msg:
                self.bridge.locked = bool(msg["locked"])
            log("options: " + ", ".join(f"{k}={v}" for k, v in msg.items() if k != "cmd"))
        elif cmd == "discover":
            self.discovering = bool(msg.get("on"))
            self.nearby, self.nearby_t = [], 0.0
            self.proximity.set_transport("auto" if self.discovering else "le")
        elif cmd == "pair":
            self.pair(msg["address"])
        elif cmd == "quit":
            self.quit = True
        elif cmd == "calibrate":
            self.calibrate(msg)
        elif cmd == "action":
            phone = self.fleet.phones.get(msg.get("address")) or self.bridge.phone
            bit = ACTIONS.get(getattr(e, "KEY_" + str(msg["key"]).upper(), None))
            if bit and phone.connected:
                self.bridge.click_on(phone, bit)
                log(f"GUI -> {phone.alias}: mouse button {bit.bit_length()}")

    def set_adapter(self, prop, value):
        try:
            dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                           "org.freedesktop.DBus.Properties").Set("org.bluez.Adapter1", prop, value)
        except dbus.DBusException as err:
            log(f"adapter {prop}: {err.get_dbus_message()}")

    def pair(self, address):
        """Pair from this side (the device asks its user to confirm), then trust it and connect HID."""
        path = f"{self.adapter_path}/dev_{address.replace(':', '_')}"
        self.pairing = address

        def done(error=None):
            self.pairing = None
            if error is None:
                dbus.Interface(self.bus.get_object("org.bluez", path),
                               "org.freedesktop.DBus.Properties").Set("org.bluez.Device1", "Trusted", True)
                self.fleet.sync_paired(paired_devices(self.bus, self.adapter_path))
                phone = self.fleet.phones.get(address)
                if phone:
                    phone.next_dial = 0.0
                    self.fleet.dial_now = True
                log(f"paired with {phone.alias if phone else address}")
            else:
                log(f"pairing {address} failed: {error.get_dbus_message()}")
            EVENTS.append({"type": "paired", "address": address, "ok": error is None,
                           "error": None if error is None else error.get_dbus_message()})

        def start():
            dbus.Interface(self.bus.get_object("org.bluez", path), "org.bluez.Device1").Pair(
                reply_handler=done, error_handler=done, timeout=60)
            return False
        GLib.idle_add(start)  # on the GLib thread, which delivers the reply

    def forget(self, address):
        phone = self.fleet.phones.get(address)
        if phone is None:
            log(f"GUI asked to forget unknown device {address}")
            return
        if self.bridge.phone is phone:
            self.bridge.switch_to(None)
        phone.close()
        del self.fleet.phones[address]
        if self.bus is not None:
            path = f"{self.adapter_path}/dev_{address.replace(':', '_')}"
            try:
                dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                               "org.bluez.Adapter1").RemoveDevice(path)
            except dbus.DBusException as err:
                log(f"forget {phone.alias}: {err.get_dbus_message()}")
                return
        log(f"forgot {phone.alias}")

    def calibrate(self, msg):
        """Measure a device's screen in mouse counts: pin its pointer to the top-left corner, sweep it
        along one axis at a constant speed, and count what was sent until the user presses Stop at the
        far edge."""
        if msg.get("action") == "stop":
            c, self.calib = self.calib, None
            if c:
                counts = max(100, round(c["sent"] - CALIBRATE_REACTION_S * CALIBRATE_RATE))
                log(f"calibration {c['phone'].alias} {c['axis']}: {counts} counts")
                EVENTS.append({"type": "calibration", "address": c["phone"].address,
                               "axis": c["axis"], "counts": counts})
            return
        phone = self.fleet.phones.get(msg.get("address"))
        if phone is None or not phone.connected:
            log("calibration: that device is not connected")
            return
        self.bridge.send_on(phone, -(PIN_EXTRA + 3000), -(PIN_EXTRA + 3000))  # top-left corner
        self.calib = {"phone": phone, "axis": "y" if msg.get("axis") == "y" else "x",
                      "t0": time.monotonic(), "sent": 0}
        log(f"calibration {phone.alias}: sweeping the pointer from the top-left - press Stop "
            "the moment it touches the far edge")

    def step_calibration(self):
        c = self.calib
        if not c:
            return
        now = time.monotonic()
        if not c["phone"].connected or now - c["t0"] > CALIBRATE_MAX_S:
            log("calibration cancelled")
            self.calib = None
            return
        due = int((now - c["t0"]) * CALIBRATE_RATE) - c["sent"]
        if due > 0:
            self.bridge.send_on(c["phone"], *((due, 0) if c["axis"] == "x" else (0, due)))
            c["sent"] += due

    def state(self):
        devices = []
        for phone in self.fleet.phones.values():
            sig = self.proximity.signals.get(phone.address)
            w, h = phone.size()
            devices.append({
                "address": phone.address, "name": phone.alias, "model": phone.model_name,
                "kind": phone.kind, "connected": phone.connected,
                "os": phone.os, "form": phone.form, "software": phone.software, "battery": phone.battery,
                "handoff": {"zone": phone.zone, "span": phone.span, "require_near": phone.require_near,
                            "phone_w": w, "phone_h": h, "speed": phone.speed},
                "signal": None if sig is None else {"rssi": sig.last, "avg": round(sig.target, 1),
                                                    "zone": zone_for(sig.target)[0]}})
        return {"type": "state", "devices": devices,
                "target": self.bridge.phone.address if self.bridge.on_phone else None,
                "inputs": [d.name for d in self.bridge.devices.values()],
                "scanning": self.proximity.scanning,
                "sharing": SHARE_CURSOR, "locked": self.bridge.locked,
                "nearby": self.nearby_list() if self.discovering else None, "pairing": self.pairing,
                "handoff": self.bridge.handoff.status()}

    def nearby_list(self):
        now = time.monotonic()
        if now - self.nearby_t > 1:
            self.nearby_t = now
            try:
                self.nearby = nearby_devices(self.bus, self.adapter_path)
            except dbus.DBusException:
                pass
        return self.nearby

    def send(self, sock, msg):
        try:
            sock.send((json.dumps(msg) + "\n").encode())
        except OSError:  # includes a client that stopped reading
            self.drop(sock)

    def drop(self, sock):
        self.clients.pop(sock, None)
        sock.close()

    def pump(self):
        """Forward queued events and, when something changed, the state."""
        self.step_calibration()
        if not self.clients:
            EVENTS.clear()
            return
        while EVENTS:
            msg = EVENTS.popleft()
            for sock in list(self.clients):
                self.send(sock, msg)
        now = time.monotonic()
        state = self.state()
        if state != self.last_state or now - self.last_state_t > 1:
            self.last_state, self.last_state_t = state, now
            for sock in list(self.clients):
                self.send(sock, state)


# --- input -----------------------------------------------------------------

class Touchpad:
    def __init__(self, dev):
        self.resx = dev.absinfo(e.ABS_X).resolution or 12
        self.resy = dev.absinfo(e.ABS_Y).resolution or 12
        mt = e.ABS_MT_SLOT in dev.capabilities(absinfo=False).get(e.EV_ABS, [])
        self.slot = dev.absinfo(e.ABS_MT_SLOT).value if mt else 0
        self.contacts = {}  # multitouch slot -> [x, y] of each finger on the pad
        self.three = None  # centroid x (mm) where a three-finger touch started, for switching devices
        self.three_done = False
        self.reset()

    def reset(self):
        self.touching = False
        self.x = self.y = self.last = None
        self.fingers = self.max_fingers = 0
        self.t0 = self.travel = self.scroll = self.swipe = 0.0
        self.swiped = False
        self.clicked = False
        self.click_bit = 0
        self.reset_gesture()

    def reset_gesture(self):
        self.gesture = None  # two-finger gesture once decided: "v" or "h" scroll, or "pinch"
        self.move = [0.0, 0.0]  # two-finger travel (mm) while still undecided
        self.zoom = self.scroll = 0.0
        self.pair = None  # centroid x, y and spread of two fingers (mm) at the last SYN
        self.flicked = False  # SWIPE_FLICK: this swipe has turned its page

    def track(self, ev):
        """Follow every finger through the multitouch slots, whoever has control."""
        if ev.type != e.EV_ABS:
            return
        if ev.code == e.ABS_MT_SLOT:
            self.slot = ev.value
        elif ev.code == e.ABS_MT_TRACKING_ID:
            if ev.value < 0:
                self.contacts.pop(self.slot, None)
            else:
                self.contacts[self.slot] = [None, None]
        elif ev.code in (e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y) and self.slot in self.contacts:
            self.contacts[self.slot][ev.code == e.ABS_MT_POSITION_Y] = ev.value

    def centroid_x(self, count):
        """Mean x in mm of the fingers on the pad if exactly `count` are down, else None."""
        xs = [p[0] for p in self.contacts.values() if p[0] is not None]
        return sum(xs) / len(xs) / self.resx if len(self.contacts) == count == len(xs) else None

    def two_fingers(self):
        """Centroid x, y and distance between two fingers in mm, or None."""
        pts = [p for p in self.contacts.values() if None not in p]
        if len(pts) != 2:
            return None
        (x1, y1), (x2, y2) = pts
        return ((x1 + x2) / 2 / self.resx, (y1 + y2) / 2 / self.resy,
                math.hypot((x1 - x2) / self.resx, (y1 - y2) / self.resy))


class Shake:
    """Notices the pointer being shaken: quick left-right reversals."""

    def __init__(self):
        self.dir = 0
        self.run = 0.0  # mm travelled in the current direction
        self.turns = deque()
        self.fired = 0.0

    def feed(self, mx):
        if not mx:
            return False
        d = 1 if mx > 0 else -1
        if d == self.dir:
            self.run += abs(mx)
            return False
        now = time.monotonic()
        if self.run >= SHAKE_MIN_MM:
            self.turns.append(now)
        self.dir, self.run = d, abs(mx)
        while self.turns and now - self.turns[0] > SHAKE_WINDOW_S:
            self.turns.popleft()
        if len(self.turns) >= SHAKE_TURNS and now - self.fired > 1.5:
            self.fired = now
            self.turns.clear()
            return True
        return False


class Bridge:
    def __init__(self, fleet, devices):
        self.fleet = fleet
        self.phone = NO_PHONE  # output device: the one in control, or the last one that was
        self.devices = devices  # fd -> InputDevice
        self.touchpads = {fd: Touchpad(d) for fd, d in devices.items() if is_touchpad(d)}
        self.on_phone = False
        self.toggle_pending = False
        self.action_pending = None  # a HOTKEYS action, run once every key is up again
        self.held = set()
        self.locked = False  # Scroll Lock: the pointer stays on the screen it is on
        self.shake = Shake()
        self.handoff = None  # set by main() once Proximity exists
        self.proximity = None
        self.rel = [0, 0]    # mouse motion collected until the next SYN while the PC has control
        self.reset_reports()

    def reset_reports(self):
        self.mods, self.keys, self.buttons = 0, [], 0
        self.dx = self.dy = 0.0
        self.wheel = self.pan = 0
        self.last_mouse = 0.0
        # pointer moves of our own, one per report (the glide on entry, swipe flicks): (x, y, button 1
        # pressed: True / False / None unchanged), or a float: pause that many seconds...
        self.steps = deque()
        self.steps_at = 0.0  # ...not before this time
        for tp in self.touchpads.values():
            tp.reset()

    def toggle(self):
        """PC -> the device used last -> the next device ... -> PC."""
        phones = self.fleet.connected()
        if self.on_phone:
            later = phones[phones.index(self.phone) + 1:] if self.phone in phones else []
            self.switch_to(later[0] if later else None)
        elif phones:
            self.switch_to(self.phone if self.phone in phones else phones[0])
        else:
            log("no iPhone/iPad connected - pair/connect one first")

    def switch_to(self, device):
        """Hand keyboard and touchpad to a connected Phone, or back to the PC with None."""
        if device is not None and not device.connected:
            log(f"{device.alias} is not connected")
            return
        if (device is None and not self.on_phone) or (device is self.phone and self.on_phone):
            return
        was_on = self.on_phone
        if was_on:
            self.release_all()  # let go of every key and button on the device being left
        self.on_phone = device is not None
        if device is not None:
            self.phone = device
            # a device may have asked the link back into power-save since we first connected
            disable_sniff(device.dev_id, device.address, quiet=True)
        if self.proximity and not SCAN_WHILE_CONTROLLING:
            self.proximity.set_scanning(not self.on_phone)
        if self.on_phone != was_on:
            for d in self.devices.values():
                try:
                    d.grab() if self.on_phone else d.ungrab()
                except OSError:
                    pass
        held = {fd: (tp.touching, tp.fingers, tp.x, tp.y) for fd, tp in self.touchpads.items()}
        self.reset_reports()
        for fd, (touching, fingers, x, y) in held.items():
            if touching:  # hand-off mid-swipe: keep tracking the finger, but it is not a tap
                tp = self.touchpads[fd]
                tp.touching, tp.fingers, tp.x, tp.y = True, fingers, x, y
                tp.max_fingers, tp.travel, tp.t0 = fingers, 99.0, time.monotonic()
        if self.handoff:
            if was_on:
                self.handoff.on_leave()
            if self.on_phone:
                self.handoff.on_enter()
        log(f"input -> {device.alias} (Ctrl+Alt+P for the next one)" if device else "input -> PC")

    def release_all(self):
        self.phone.send(1, bytes(8))
        self.phone.send(2, bytes(5))

    def drop_phone(self):
        if self.on_phone:
            self.switch_to(None)

    def handle(self, fd):
        dev = self.devices[fd]
        try:
            events = list(dev.read())
        except BlockingIOError:
            return
        except OSError:
            log(f"lost input device {dev.name}")
            del self.devices[fd]
            self.touchpads.pop(fd, None)
            return
        tp = self.touchpads.get(fd)
        for ev in events:
            if tp:
                tp.track(ev)
                if ev.type == e.EV_SYN:
                    self.three_finger_switch(tp)
            if ev.type == e.EV_KEY and ev.value != 2:
                if self.track_toggle(ev):
                    continue
            if not self.on_phone:
                self.pc_event(tp, ev)
                continue
            if tp:
                self.touchpad_event(tp, ev)
            elif ev.type == e.EV_KEY and ev.code in BUTTONS:
                self.set_button(BUTTONS[ev.code], ev.value)
            elif ev.type == e.EV_KEY:
                self.key_event(ev)
            elif ev.type == e.EV_REL:
                if ev.code == e.REL_X:
                    self.dx += ev.value * self.phone.speed
                elif ev.code == e.REL_Y:
                    self.dy += ev.value * self.phone.speed
                elif ev.code == e.REL_WHEEL:
                    self.wheel += ev.value
            elif ev.type == e.EV_SYN:
                self.flush_mouse()
        if self.toggle_pending and not self.held:
            self.toggle_pending = False
            self.toggle()
        if self.action_pending and not self.held:
            action, self.action_pending = self.action_pending, None
            self.run_action(action)

    def run_action(self, action):
        global SHARE_CURSOR
        if action == "toggle_sharing":
            SHARE_CURSOR = not SHARE_CURSOR
            log(f"cursor sharing {'on' if SHARE_CURSOR else 'off'}")
        elif action == "next_device":
            phones = self.fleet.connected()
            if not phones:
                log("no device connected")
            elif self.on_phone and self.phone in phones:
                self.switch_to(phones[(phones.index(self.phone) + 1) % len(phones)])
            else:
                self.switch_to(phones[0])
        elif action == "return_to_pc" and self.on_phone:
            if self.handoff and self.handoff.edge:
                self.handoff.leave()  # puts the PC pointer back at the edge it left by
            else:
                self.switch_to(None)
        elif action == "lock":
            self.locked = not self.locked
            log("pointer locked to the current screen" if self.locked else "pointer unlocked")
        elif action == "open_gui":
            EVENTS.append({"type": "open"})

    def cycle(self, step):
        """PC, then each connected device, in a ring: step +1 next, -1 previous."""
        ring = [None, *self.fleet.connected()]
        here = self.phone if self.on_phone and self.phone in ring else None
        target = ring[(ring.index(here) + step) % len(ring)]
        if target is None and self.handoff and self.handoff.edge:
            self.handoff.leave()
        else:
            self.switch_to(target)

    def three_finger_switch(self, tp):
        x = tp.centroid_x(3)
        if x is None:
            tp.three, tp.three_done = None, False
            return
        if tp.three is None:
            tp.three = x
        elif not tp.three_done and abs(x - tp.three) >= SWITCH_SWIPE_MM and THREE_FINGER_SWITCH:
            tp.three_done = True
            self.cycle(1 if x > tp.three else -1)

    def pc_event(self, tp, ev):
        """While the PC has control: feed pointer motion to the hand-off edge detector and the
        shake detector."""
        h = self.handoff
        motion = None
        if tp:
            if ev.type == e.EV_ABS:
                if ev.code == e.ABS_X:
                    tp.x = ev.value
                elif ev.code == e.ABS_Y:
                    tp.y = ev.value
            elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                tp.touching, tp.last = bool(ev.value), None
                if not ev.value and h:
                    h.push.clear()
            elif ev.type == e.EV_KEY and ev.code in FINGERS and ev.value:
                tp.fingers, tp.last = FINGERS[ev.code], None
            elif ev.type == e.EV_SYN and tp.touching and tp.fingers == 1 and None not in (tp.x, tp.y):
                if tp.last:
                    motion = ((tp.x - tp.last[0]) / tp.resx, (tp.y - tp.last[1]) / tp.resy)
                tp.last = (tp.x, tp.y)
        elif ev.type == e.EV_REL:
            if ev.code in (e.REL_X, e.REL_Y):
                self.rel[ev.code == e.REL_Y] += ev.value
        elif ev.type == e.EV_SYN and any(self.rel):
            rx, ry, self.rel = self.rel[0], self.rel[1], [0, 0]
            motion = (rx / MOUSE_COUNTS_PER_MM, ry / MOUSE_COUNTS_PER_MM)
        if motion is None:
            return
        if SHAKE_TO_LOCATE and self.shake.feed(motion[0]):
            EVENTS.append({"type": "shake"})
        if h and any(p.zone for p in self.fleet.connected()):
            h.pc_motion(*motion)

    def track_toggle(self, ev):
        """Track held keys; returns True if the event triggered a hotkey and must not be forwarded."""
        if ev.code >= e.BTN_MISC:  # mouse/touchpad buttons never count as held keys
            return False
        if ev.value:
            self.held.add(ev.code)
            if TOGGLE <= self.held:
                self.toggle_pending = True
                return True
            mods = {MOD_KEYS[k] for k in self.held if k in MOD_KEYS}
            for need, key, action in HOTKEYS:
                if ev.code == key and mods == need:
                    if self.on_phone and mods:
                        self.phone.send(1, bytes(8))  # let go of the modifiers on the device first
                    self.action_pending = action
                    return True
            if self.on_phone and ev.code in ACTIONS and ACTION_MODS <= self.held:
                self.phone.send(1, bytes(8))  # let go of Ctrl+Alt on the phone first
                self.click(ACTIONS[ev.code])
                log(f"hotkey -> mouse button {ACTIONS[ev.code].bit_length()}")
                return True
        else:
            self.held.discard(ev.code)
        return False

    def key_event(self, ev):
        if ev.code in MODS:
            if ev.value:
                self.mods |= MODS[ev.code]
            else:
                self.mods &= ~MODS[ev.code]
        elif ev.code in KEYMAP:
            usage = KEYMAP[ev.code]
            if ev.value and usage not in self.keys:
                self.keys.append(usage)
            elif not ev.value and usage in self.keys:
                self.keys.remove(usage)
        else:
            return
        keys = (self.keys + [0] * 6)[:6]
        self.phone.send(1, [self.mods, 0] + keys)

    def set_button(self, bit, pressed):
        self.buttons = self.buttons | bit if pressed else self.buttons & ~bit
        self.flush_mouse(force=True)

    def click(self, bit):
        self.set_button(bit, True)
        self.set_button(bit, False)

    def send_on(self, phone, ix, iy):
        """Move a specific device's pointer, whoever has control."""
        keep, self.phone = self.phone, phone
        try:
            self.send_mouse(ix, iy)
        finally:
            self.phone = keep

    def click_on(self, phone, bit):
        """Click on a specific device, whoever has control."""
        keep, self.phone = self.phone, phone
        try:
            self.click(bit)
        finally:
            self.phone = keep

    def touchpad_event(self, tp, ev):
        if ev.type == e.EV_ABS:
            if ev.code == e.ABS_X:
                tp.x = ev.value
            elif ev.code == e.ABS_Y:
                tp.y = ev.value
        elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
            if ev.value:
                tp.touching, tp.t0, tp.travel, tp.last = True, time.monotonic(), 0.0, None
                tp.max_fingers, tp.clicked = 0, False
                tp.swipe, tp.swiped = 0.0, False
                self.end_gesture(tp)
            else:
                tp.touching = False
                self.end_gesture(tp)
                quick = time.monotonic() - tp.t0 < TAP_TIME and tp.travel < TAP_TRAVEL_MM
                if quick and not tp.clicked and TAP_TO_CLICK:
                    self.click(2 if tp.max_fingers >= 2 else 1)
        elif ev.type == e.EV_KEY and ev.code in FINGERS:
            if ev.value:
                tp.fingers = FINGERS[ev.code]
                tp.max_fingers = max(tp.max_fingers, tp.fingers)
                tp.last = None  # avoid a jump when the tracked finger changes
                tp.swipe = 0.0
                self.end_gesture(tp)
        elif ev.type == e.EV_KEY and ev.code in BUTTONS:  # physical clickpad press
            if ev.value:
                tp.clicked = True
                self.end_gesture(tp)
                tp.click_bit = 2 if tp.fingers >= 2 else BUTTONS[ev.code]
                self.set_button(tp.click_bit, True)
            elif tp.click_bit:
                self.set_button(tp.click_bit, False)
                tp.click_bit = 0
        elif ev.type == e.EV_SYN:
            if tp.touching and tp.x is not None and tp.y is not None:
                if tp.last:
                    mx = (tp.x - tp.last[0]) / tp.resx
                    my = (tp.y - tp.last[1]) / tp.resy
                    tp.travel += math.hypot(mx, my)
                    if tp.fingers >= 3 and not tp.click_bit:
                        tp.swipe += my
                        if tp.swipe < -SWIPE_UP_MM and not tp.swiped and SWIPE_UP_HOME:
                            tp.swiped = True
                            self.click(SWIPE_UP_BUTTON)
                            log("three-finger swipe up -> Home")
                    elif tp.fingers >= 2 and not tp.click_bit:
                        self.two_finger_gesture(tp, mx, my)
                    else:
                        self.dx += mx * TOUCHPAD_SPEED * self.phone.speed
                        self.dy += my * TOUCHPAD_SPEED * self.phone.speed
                tp.last = (tp.x, tp.y)
            self.flush_mouse()

    def two_finger_gesture(self, tp, mx, my):
        """Scroll up/down or left/right (locked to the first direction), or pinch to zoom."""
        pair, pinch = tp.two_fingers(), 0.0
        if pair and tp.pair:  # the midpoint of both fingers, so a pinch does not read as a scroll
            mx, my, pinch = (a - b for a, b in zip(pair, tp.pair))
        tp.pair = pair
        if tp.gesture is None:
            tp.move[0] += mx
            tp.move[1] += my
            tp.zoom += pinch
            travel = math.hypot(*tp.move)
            if max(travel, abs(tp.zoom)) < GESTURE_LOCK_MM:
                return
            if abs(tp.zoom) > travel:
                tp.gesture = "pinch"
            else:
                tp.gesture = "h" if abs(tp.move[0]) > abs(tp.move[1]) else "v"
            (mx, my), pinch, tp.zoom = tp.move, tp.zoom, 0.0  # the deciding travel counts too
        if tp.gesture == "pinch":
            tp.zoom += pinch
            steps = int(tp.zoom / PINCH_STEP_MM)
            tp.zoom -= steps * PINCH_STEP_MM
            zoom_in, zoom_out = (APP_ZOOM_IN, APP_ZOOM_OUT) if self.mods & CTRL else (ZOOM_IN, ZOOM_OUT)
            for _ in range(abs(steps)):
                self.tap_key(*(zoom_in if steps > 0 else zoom_out))
            return
        if tp.gesture == "h" and SWIPE_FLICK:
            tp.scroll += mx
            if not tp.flicked and abs(tp.scroll) >= FLICK_MM:
                tp.flicked = True
                self.flick((1 if tp.scroll > 0 else -1) * (1 if NATURAL_SCROLL else -1))
            return
        tp.scroll += mx if tp.gesture == "h" else my
        steps = int(tp.scroll / SCROLL_STEP_MM)
        tp.scroll -= steps * SCROLL_STEP_MM
        if tp.gesture == "h":  # AC Pan counts right as positive, the wheel counts up as positive
            self.pan += steps if NATURAL_SCROLL else -steps
        else:
            self.wheel += -steps if NATURAL_SCROLL else steps

    def flick(self, direction):
        """Turn one page: drag the content fast towards `direction` (-1 left, +1 right) and let go, then
        bring the pointer back the same way, report by report."""
        h = self.handoff
        if h and h.edge is not None:  # where the pointer is known: make room before the edge first
            w, _ = self.phone.size()
            need = FLICK_ROOM * FLICK_STEP * FLICK_REPORTS
            room = h.vx if direction < 0 else w - h.vx
            if room < need:
                away = -direction * round(need - room)
                self.glide(away, 0, pause=0.0)
                h.vx += away
        out = (direction * FLICK_STEP, 0, None)
        self.steps.extend([(0, 0, True), *[out] * (FLICK_REPORTS - 1), (out[0], 0, False), FLICK_PAUSE_S,
                           *[(-out[0], 0, None)] * FLICK_REPORTS])

    def end_gesture(self, tp):
        tp.reset_gesture()

    def tap_key(self, mods, usage):
        """Press and release one shortcut on the phone, then restore the keys really held."""
        self.phone.send(1, [mods, 0, usage, 0, 0, 0, 0, 0])
        self.phone.send(1, [self.mods, 0] + (self.keys + [0] * 6)[:6])

    def mouse_pending(self):
        return self.on_phone and (abs(self.dx) >= 1 or abs(self.dy) >= 1 or self.wheel or self.pan
                                  or bool(self.steps))

    def glide(self, dx, dy, pause=GLIDE_PAUSE_S):
        """Move the device pointer (dx, dy) counts at an even pace, GLIDE_STEP counts per report, after
        `pause` seconds."""
        n = math.ceil(max(abs(dx), abs(dy)) / GLIDE_STEP)
        step = lambda v, i: round(v * i / n) - round(v * (i - 1) / n)
        if n:
            self.steps.extend([pause, *((step(dx, i), step(dy, i), None) for i in range(1, n + 1))])

    def flush_mouse(self, force=False):
        now = time.monotonic()
        due = now - self.last_mouse >= MOUSE_INTERVAL
        sx = sy = 0  # one of our own steps: already accounted for in the hand-off model
        press = None
        if self.steps and not force and due and now >= self.steps_at and not self.phone.backlogged():
            step = self.steps.popleft()
            if isinstance(step, float):
                self.steps_at = now + step
            else:
                sx, sy, press = step
                if press is not None:
                    self.buttons = self.buttons | 1 if press else self.buttons & ~1
        ix, iy, w, p = int(self.dx), int(self.dy), self.wheel, self.pan
        if not (force or ix or iy or sx or sy or w or p or press is not None):
            return
        # Only motion is held back; button changes always go out immediately
        if not force and (not due or self.phone.backlogged()):
            return
        self.dx -= ix
        self.dy -= iy
        self.wheel = self.pan = 0
        self.last_mouse = now
        self.send_mouse(ix + sx, iy + sy, w, p)
        if self.handoff and (ix or iy):
            self.handoff.phone_motion(ix, iy)

    def send_mouse(self, ix, iy, w=0, p=0):
        """Send a relative move, split into reports of at most 127 counts per axis."""
        while True:
            cx, cy, cw, cp = (max(-127, min(127, v)) for v in (ix, iy, w, p))
            self.phone.send(2, [self.buttons, cx & 0xFF, cy & 0xFF, cw & 0xFF, cp & 0xFF])
            ix, iy, w, p = ix - cx, iy - cy, w - cw, p - cp
            if not (ix or iy or w or p):
                break


def is_touchpad(dev):
    caps = dev.capabilities(absinfo=False)
    return e.BTN_TOOL_FINGER in caps.get(e.EV_KEY, []) and e.ABS_X in caps.get(e.EV_ABS, [])


def find_inputs():
    found = {}
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        caps = dev.capabilities(absinfo=False)
        keys = caps.get(e.EV_KEY, [])
        if is_touchpad(dev) or e.REL_X in caps.get(e.EV_REL, []) or e.KEY_A in keys:
            found[dev.fd] = dev
            log(f"input: {dev.name}")
        else:
            dev.close()
    return found


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(SOCK_PATH)
        sys.exit("side_control_backend.py is already running (its control socket answers) - stop it first")
    except OSError:
        pass  # nobody listening: good
    finally:
        probe.close()
    try:
        adapter = sys.argv[sys.argv.index("--adapter") + 1] if "--adapter" in sys.argv[:-1] else None
        addr, bus, adapter_path = setup_bluez(adapter)
    except dbus.DBusException as err:
        sys.exit(f"BlueZ setup failed: {err.get_dbus_message()}\n"
                 "Is another copy of side_control_backend.py still running? Check: pgrep -af side_control_backend")
    global DIAL_ONLY
    try:
        ctrl_l, intr_l = listen(addr, PSM_CTRL), listen(addr, PSM_INTR)
    except OSError as err:
        # bluetoothd's input plugin holds them, so Bluetooth mice and keyboards can connect to this PC:
        # devices cannot connect to us then, but we still connect to them
        DIAL_ONLY = True
        ctrl_l = intr_l = None
        log(f"HID channels (PSM {PSM_CTRL}/{PSM_INTR}) are taken ({err.strerror}): probably bluetoothd's input "
            "plugin. Connecting to devices from here only; to let them connect too, start bluetoothd with "
            "-P input (bluetooth-no-input.conf)")
    fleet = Fleet(int(adapter_path.rsplit("hci", 1)[1]))
    fleet.sync_paired(paired_devices(bus, adapter_path))
    bridge = Bridge(fleet, find_inputs())
    sys.setswitchinterval(0.001)  # the GLib thread (scan, panel) must not hold up pointer reports
    proximity = Proximity(bus, adapter_path)
    proximity.start()
    bridge.proximity = proximity
    bridge.handoff = Handoff(bridge, fleet, proximity, Hyprland())
    control = Control(bridge, fleet, proximity, bus, adapter_path)
    control.start()
    log(f'"{NAME}" is discoverable - pair each iPhone / iPad from its Bluetooth settings')
    last_dial = 0.0

    while True:
        now = time.monotonic()
        if control.quit:
            log("quitting (asked by the GUI)")
            break
        if now - last_dial > RECONNECT_EVERY or fleet.dial_now:
            last_dial, fleet.dial_now = now, False
            fleet.sync_paired(paired_devices(bus, adapter_path))
            if not bridge.on_phone:  # paging an absent device would disturb the active link
                fleet.dial_missing()
        fleet.accept_dialed()
        owners = {}
        for phone in fleet.phones.values():
            if phone.ctrl:
                owners[phone.ctrl] = (phone, "ctrl")
            if phone.intr:
                owners[phone.intr] = (phone, "intr")
        ready, _, _ = select.select([*(s for s in (ctrl_l, intr_l) if s), *owners, *bridge.devices, *control.sockets()],
                                    [], [], MOUSE_INTERVAL if bridge.mouse_pending()
                                    else 0.02 if control.calib else 0.1 if control.clients else 1)
        for r in ready:
            if r is ctrl_l:
                conn, peer = ctrl_l.accept()
                phone = fleet.get(peer[0])
                if phone.ctrl:
                    phone.close()
                phone.ctrl = conn
                log(f"control channel from {phone.alias}")
            elif r is intr_l:
                conn, peer = intr_l.accept()
                phone = fleet.get(peer[0])
                if phone.intr:
                    phone.intr.close()
                phone.intr = conn
                if phone.peer is None:
                    phone.connected_to()
                log(f"{phone.alias} connected - press Ctrl+Alt+P to control it")
            elif r in owners:
                phone, kind = owners[r]
                if r is not phone.ctrl and r is not phone.intr:  # replaced during this pass
                    continue
                if kind == "ctrl":
                    phone.handle_ctrl()
                else:
                    try:
                        if not r.recv(64):  # output reports (LEDs) are ignored
                            phone.close()
                    except OSError:
                        phone.close()
            elif control.owns(r):
                control.handle(r)
            elif r in bridge.devices:
                bridge.handle(r)
        if not bridge.on_phone:  # reading the battery uses the radio: not while a device has control
            for phone in fleet.connected():
                if phone.battery_t and now - phone.battery_t > BATTERY_EVERY:
                    phone.refresh_battery()
        control.pump()
        if bridge.on_phone:
            bridge.flush_mouse()  # send motion that was held back
        if not bridge.phone.connected:
            bridge.drop_phone()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
