# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths, defaults and the settings file; Launch at Login."""

import json
import os
import re
import shutil
import sys

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
            "run_in_background": True, "visible": False, "device_name": None, "adapter": None,
            "hid_banner_dismissed": False}
# The edge indicator's styles: name -> (shape, thickness in px, opacity in %).
INDICATOR_STYLES = {"Subtle": ("solid", 2, 60), "Standard": ("solid", 4, 100), "Bold": ("solid", 8, 100),
                    "Glow": ("glow", 6, 90)}
DEVICE_DEFAULTS = {"enabled": True, "edge": None, "x": None, "y": None}
BACKEND_OPTIONS = ("share_cursor", "switch_delay_ms", "three_finger_switch", "shake_to_locate", "tap_to_click",
                   "mouse_natural_scroll", "hide_cursor")



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


def desktop_exec(*argv):
    """An Exec= value, quoted as the desktop entry spec says: arguments with reserved characters go
    in double quotes, with quotes, backticks, dollar signs and backslashes escaped, and % doubled."""
    def quote(value):
        value = value.replace("%", "%%")
        if not set(value) & set(" \t\n\"'\\><~|&;$*?#()`"):
            return value
        value = re.sub(r'(["`$\\])', r"\\\1", value)  # the Exec key's own quoting...
        return '"' + value.replace("\\", "\\\\") + '"'  # ...then the string escaping every value gets
    return " ".join(quote(arg) for arg in argv)


def install_icon(data_dir=None):
    """Copy the app icon where icon themes find it: an AppImage's files are gone once it quits."""
    icon = os.path.join(data_dir or GLib.get_user_data_dir(), "icons", "hicolor", "scalable", "apps",
                        APP_ICON + ".svg")
    os.makedirs(os.path.dirname(icon), exist_ok=True)
    shutil.copyfile(os.path.join(ICONS_DIR, APP_ICON + ".svg"), icon)
    return icon


def set_autostart(on, path=None, command=None, icon=None):
    """Write (or remove) the autostart entry. `--install` passes the installed copy as command and
    icon, so that Launch at Login follows it."""
    path = path or AUTOSTART_PATH
    if not on:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    if command is None:
        if APPIMAGE:  # its files are gone once it quits: copy the icon out
            command, icon = desktop_exec(APPIMAGE), install_icon()
        else:
            command, icon = desktop_exec(sys.executable, LAUNCHER), os.path.join(ICONS_DIR, APP_ICON + ".svg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Side Control\n"
                f"Exec={command} --hidden\nIcon={icon}\nX-GNOME-Autostart-enabled=true\n")
