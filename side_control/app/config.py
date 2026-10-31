# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths, defaults and the settings file; Launch at Login."""

import json
import os
import shlex
import shutil
import sys

from gi.repository import GLib


APP_ID = APP_ICON = "io.github.sidecontrol.SideControl"  # data/icons/<APP_ICON>.svg, -symbolic for the tray
ISSUES_URL = "https://github.com/Rakura-cloud/side-control/issues"  # where "Report an Issue" leads
PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # side_control/
ROOT = os.path.dirname(PACKAGE)  # where the side-control and side-control-backend launchers are
LAUNCHER = os.path.join(ROOT, "side-control")
BACKEND = os.path.join(ROOT, "side-control-backend")
# Running from the AppImage: root cannot read inside the user's mount of it, so root runs the AppImage
# itself, which starts the backend when given this name
APPIMAGE = os.environ.get("APPIMAGE") if os.path.isfile(os.environ.get("APPIMAGE", "")) else None
BACKEND_COMMAND = [APPIMAGE, "side-control-backend"] if APPIMAGE else [sys.executable, BACKEND]
SOCK_PATH = os.environ.get("SIDE_CONTROL_SOCK", "/run/side-control.sock")
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
            "indicator": "near", "indicator_within": 48, "indicator_names": True,
            "run_in_background": True, "visible": False, "device_name": None, "adapter": None,
            "hid_banner_dismissed": False}
DEVICE_DEFAULTS = {"enabled": True, "edge": None, "x": None, "y": None}
BACKEND_OPTIONS = ("share_cursor", "switch_delay_ms", "three_finger_switch", "shake_to_locate", "tap_to_click")



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
