# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths, defaults and the settings file; Launch at Login."""

import json
import os
import shlex
import shutil
import sys

from evdev import ecodes as e
from gi.repository import GLib


APP_ID = APP_ICON = "io.github.sidecontrol.SideControl"  # data/icons/<APP_ICON>.svg, -symbolic for the tray
ISSUES_URL = "https://github.com/Rakura-cloud/side-control/issues"  # where "Report an Issue" leads
PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # side_control/
ROOT = os.path.dirname(PACKAGE)  # where the side-control and side-control-backend launchers are
LAUNCHER = os.path.join(ROOT, "side-control")
BACKEND = os.path.join(ROOT, "side-control-backend")
# Running from the AppImage: the backend outlives this process and its mount of the AppImage, so it runs
# the AppImage, which starts the backend when given this name. Ours only if this code runs from its mount: an
# AppImage started us from a terminal (or app) of its own leaves its APPIMAGE and APPDIR to us
APPDIR = os.environ.get("APPDIR")
APPIMAGE = os.environ.get("APPIMAGE") if APPDIR and PACKAGE.startswith(os.path.join(APPDIR, "")) else None
BACKEND_COMMAND = [APPIMAGE, "side-control-backend"] if APPIMAGE else [sys.executable, BACKEND]
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
SOCK_PATH = os.environ.get("SIDE_CONTROL_SOCK", os.path.join(RUNTIME_DIR, "side-control.sock"))
# The backend runs as SERVICE, a system service that runs it as this user with the input group and a few
# capabilities (data/side-control@.service). Installing it and its polkit rule, which lets this user start
# it without a password, is the one step that needs root.
SERVICE = f"side-control@{os.getuid()}.service"
SERVICE_UNIT = os.path.join(PACKAGE, "data", "side-control@.service")
SERVICE_RULES = os.path.join(PACKAGE, "data", "side-control.rules")
UNIT_PATH = "/etc/systemd/system/side-control@.service"
RULES_PATH = "/etc/polkit-1/rules.d/50-side-control.rules"
BACKEND_SCRIPT = f"/run/user/{os.getuid()}/side-control/backend"  # what SERVICE runs: written before each start
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "side-control")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
AUTOSTART_PATH = os.path.join(GLib.get_user_config_dir(), "autostart", APP_ID + ".desktop")
NO_INPUT_CONF = os.path.join(PACKAGE, "data", "bluetooth-no-input.conf")
ICONS_DIR = os.path.join(PACKAGE, "data", "icons")  # the app icons, and the design's Material Symbols as sc-*
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
DROPIN_DIR = "/etc/systemd/system/bluetooth.service.d"
HID_DROPIN = os.path.join(DROPIN_DIR, "side-control-no-input.conf")

DEFAULTS = {"share_cursor": True, "share_clipboard": True, "switch_delay_ms": 150, "corner_dead_zones": True,
            "match_speed": True, "three_finger_switch": True, "shake_to_locate": False, "tap_to_click": True,
            "mouse_natural_scroll": False,
            "indicator": "near", "indicator_within": 48, "indicator_names": True,
            "indicator_style": "Standard", "indicator_color": None, "hide_cursor": True,
            "shortcuts": {}, "gesture_actions": {}, "swipe_length": "medium",
            "run_in_background": True, "visible": False, "device_name": None, "adapter": None,
            "hid_banner_dismissed": False}
# Side Control's shortcuts (the Gestures & Keybindings page offers every one, clickable): action ->
# (title, subtitle, default accelerator, default modifiers, default evdev key). The defaults mirror
# HOTKEYS, TOGGLE and ACTIONS in backend/config.py; what the user changed lives in the "shortcuts"
# setting as {action: {"accel": ..., "mods": [...], "key": ...}}, the key as its evdev code.
SHORTCUTS = {
    "toggle_sharing": ("Toggle Cursor Sharing", "", "<Super><Shift>s", ("super", "shift"), e.KEY_S),
    "next_device": ("Send Cursor to Next Device", "", "<Super>Right", ("super",), e.KEY_RIGHT),
    "return_to_pc": ("Return Cursor to Computer", "", "<Super>Escape", ("super",), e.KEY_ESC),
    "lock": ("Lock Cursor to Current Screen", "", "Scroll_Lock", (), e.KEY_SCROLLLOCK),
    "open_gui": ("Open Side Control", "", "<Super><Alt>d", ("super", "alt"), e.KEY_D),
    "cycle": ("Cycle Devices", "Computer, then each connected device, and back", "<Ctrl><Alt>p",
              ("ctrl", "alt"), e.KEY_P),
    "button4": ("Mouse Button 4 on the Device", "Assign it in AssistiveTouch (e.g. Home)",
                "<Ctrl><Alt>h", ("ctrl", "alt"), e.KEY_H),
    "button5": ("Mouse Button 5 on the Device", "Assign it in AssistiveTouch", "<Ctrl><Alt>c",
                ("ctrl", "alt"), e.KEY_C),
    "button6": ("Mouse Button 6 on the Device", "Assign it in AssistiveTouch", "<Ctrl><Alt>n",
                ("ctrl", "alt"), e.KEY_N),
    "button7": ("Mouse Button 7 on the Device", "Assign it in AssistiveTouch", "<Ctrl><Alt>s",
                ("ctrl", "alt"), e.KEY_S),
}
BUTTON_ACTIONS = tuple(f"button{n}" for n in range(4, 8))
# The gestures the Gestures page sets an action for, and the actions each may take: their values
# mirror GESTURE_ACTIONS and GESTURE_CHOICES in backend/config.py, the buttons press that mouse
# button on the device (what it does there is assigned in AssistiveTouch, like the buttons above).
GESTURE_DEFAULTS = {"swipe_up": "button4", "swipe_side": "button8", "swipe_lr": "flick", "pinch": "zoom"}
GESTURE_CHOICES = {
    "swipe_up": ("none", "button4", "button5", "button6", "button7", "button8"),
    "swipe_side": ("none", "button4", "button5", "button6", "button7", "button8"),
    "swipe_lr": ("flick", "scroll", "none", "button4", "button5", "button6", "button7", "button8"),
    "pinch": ("zoom", "none", "button4", "button5", "button6", "button7", "button8"),
}
GESTURE_ACTION_LABELS = {"none": "Nothing", "flick": "Turn One Page", "scroll": "Scroll", "zoom": "Zoom",
                         **{f"button{n}": f"Mouse Button {n}" for n in range(4, 9)}}
GESTURES = [("swipe_up", "sc-swipe-symbolic", "Three-Finger Swipe Up"),
            ("swipe_side", "sc-swipe-symbolic", "Three-Finger Swipe Left/Right"),
            ("swipe_lr", "sc-swipe-symbolic", "Two-Finger Swipe Left/Right"),
            ("pinch", "sc-gesture-symbolic", "Pinch")]
# The Swipe Length setting: the flick's length, backend-side (see FLICK_LENGTHS there)
SWIPE_LENGTHS = (("short", "Short"), ("medium", "Medium"), ("long", "Long"))
# The edge indicator's styles: name -> (shape, thickness in px, opacity in %).
INDICATOR_STYLES = {"Subtle": ("solid", 2, 60), "Standard": ("solid", 4, 100), "Bold": ("solid", 8, 100),
                    "Glow": ("glow", 6, 90)}
DEVICE_DEFAULTS = {"enabled": True, "edge": None, "x": None, "y": None}
BACKEND_OPTIONS = ("share_cursor", "switch_delay_ms", "three_finger_switch", "shake_to_locate", "tap_to_click",
                   "mouse_natural_scroll", "hide_cursor", "swipe_length")



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
    for key in ("shortcuts", "gesture_actions"):  # only entries the app knows anything about
        if not isinstance(settings[key], dict):
            settings[key] = {}
    settings["shortcuts"] = {a: v for a, v in settings["shortcuts"].items()
                             if isinstance(v, dict) and a in SHORTCUTS}
    settings["gesture_actions"] = {g: a for g, a in settings["gesture_actions"].items()
                                   if g in GESTURE_CHOICES and a in GESTURE_CHOICES[g]}
    if settings["swipe_length"] not in dict(SWIPE_LENGTHS):
        settings["swipe_length"] = "medium"
    if not settings["three_finger_switch"] and "swipe_side" not in settings["gesture_actions"]:
        # the old Three-Finger Swipe switch, off, becomes the gesture's action
        settings["three_finger_switch"] = True
        settings["gesture_actions"]["swipe_side"] = "none"
    return settings


def shortcut_combo(action, shortcuts):
    """(modifiers, evdev key) a shortcut matches: the user's setting for it, or its default. The
    mouse button chords match on Ctrl+Alt with any extra modifiers, so those are theirs."""
    value = shortcuts.get(action) or {}
    mods = set(value["mods"]) if isinstance(value.get("mods"), list) else set(SHORTCUTS[action][3])
    key = value["key"] if isinstance(value.get("key"), int) else SHORTCUTS[action][4]
    if action in BUTTON_ACTIONS:
        mods = {"ctrl", "alt"}
    return mods, key


# Keys that type nothing, so they may be a shortcut on their own (Scroll Lock is one by default)
LONE_KEYS = {e.KEY_SCROLLLOCK, e.KEY_PAUSE, *range(e.KEY_F1, e.KEY_F10 + 1), e.KEY_F11, e.KEY_F12,
             *range(e.KEY_F13, e.KEY_F24 + 1)}


def shortcut_problem(action, mods, key):
    """Why a combination cannot be a shortcut, or None. The backend reads every key while a device has
    control, so a shortcut that types (a letter, Shift+letter) would be taken away from typing."""
    if action in BUTTON_ACTIONS and not {"ctrl", "alt"} <= set(mods):
        return "The mouse button shortcuts need Ctrl+Alt."
    if not set(mods) - {"shift"} and key not in LONE_KEYS:
        return "Add Ctrl, Alt or Super: on its own this key would no longer type on the device."
    return None


def shortcut_clash(action, mods, key, shortcuts):
    """The title of another shortcut using the same combination, or None."""
    if action in BUTTON_ACTIONS:
        mods = {"ctrl", "alt"}
    for other in SHORTCUTS:
        if other != action and shortcut_combo(other, shortcuts) == (set(mods), key):
            return SHORTCUTS[other][0]
    return None


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
    if APPIMAGE:  # its files are gone once it quits: copy the icon out
        command = shlex.quote(APPIMAGE)
        icon = os.path.join(GLib.get_user_data_dir(), "icons", "hicolor", "scalable", "apps", APP_ICON + ".svg")
        os.makedirs(os.path.dirname(icon), exist_ok=True)
        shutil.copyfile(os.path.join(ICONS_DIR, APP_ICON + ".svg"), icon)
    else:
        command = f"{shlex.quote(sys.executable)} {shlex.quote(LAUNCHER)}"
        icon = os.path.join(ICONS_DIR, APP_ICON + ".svg")
    with open(AUTOSTART_PATH, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Side Control\n"
                f"Exec={command} --hidden\nIcon={icon}\nX-GNOME-Autostart-enabled=true\n")
