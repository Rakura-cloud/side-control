# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing the AppImage: a copy under ~/.local/bin, a desktop entry, its icon, and Launch at
Login. Also `side-control --install` / `--uninstall`. The backend's service follows by itself: the app
writes the command it runs (BACKEND_COMMAND, the running AppImage) each time it starts it.

No GTK here, and paths are read from the environment when a function is called rather than when this
module is imported, so that the tests can run it against a temporary home.
"""

import os
import shutil
import stat
import sys

from .config import APP_ICON, desktop_exec, install_icon, set_autostart


APPIMAGE_NAME = "side-control.AppImage"
# ~/.local/bin is the XDG user executable directory and already on PATH on most distributions, so the
# installed AppImage can also be started by name; ~/Applications (AppImageLauncher's choice) is not.
# packaging/io.github.sidecontrol.SideControl.desktop, with Exec= pointing at the installed copy: the
# AppImage does not bundle packaging/, so it is written out here.
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Side Control
Comment=Use this computer's keyboard and touchpad on your iPhone and iPad
Exec={exec}
Icon={icon}
Terminal=false
Categories=Utility;Settings;HardwareSettings;
Keywords=iPhone;iPad;Bluetooth;keyboard;mouse;touchpad;Universal Control;
StartupWMClass={app_id}
"""


def home():
    return os.path.expanduser("~")


def data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(home(), ".local", "share")


def config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")


def appimage():
    """$APPIMAGE with symlinks resolved: the copy is made from the real file, wherever it points."""
    path = os.environ.get("APPIMAGE")
    return os.path.realpath(path) if path else None


def install_dir():
    return os.path.join(home(), ".local", "bin")


def install_path():
    return os.path.join(install_dir(), APPIMAGE_NAME)


def installed():
    return os.path.exists(install_path())


def installed_from(source=None):
    """True if `source` is the installed copy itself (so there is nothing to install or relaunch)."""
    return bool(source) and os.path.realpath(source) == os.path.realpath(install_path())


def desktop_path():
    return os.path.join(data_home(), "applications", APP_ICON + ".desktop")


def icon_path():
    return os.path.join(data_home(), "icons", "hicolor", "scalable", "apps", APP_ICON + ".svg")


def autostart_path():
    return os.path.join(config_home(), "autostart", APP_ICON + ".desktop")


def settings_path():
    return os.path.join(config_home(), "side-control", "settings.json")


def noexec(path):
    """True if `path` is on a mount that forbids executing files, so nothing there can run."""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_NOEXEC)
    except (AttributeError, OSError):
        return False


def install():
    """Copy the running AppImage to ~/.local/bin, add it to the menu and point Launch at Login at the
    copy. Idempotent; returns the lines to print.

    Raises ValueError outside an AppImage, and OSError if a file cannot be written."""
    source = appimage()
    if not source:
        raise ValueError("not running from an AppImage: start the AppImage with --install")
    if not os.path.isfile(source):
        raise ValueError(f"{source} is not a file")
    target = install_path()
    lines = []
    if noexec(source):
        lines.append(f"Warning: {source} is on a noexec mount; the installed copy avoids that")
    if os.path.exists(target) and os.path.samefile(source, target):
        lines.append(f"Already installed: {target}")
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        os.chmod(target, stat.S_IMODE(os.stat(source).st_mode) | 0o111)  # keep it executable
        lines.append(f"Installed {target}")
    if noexec(target):
        lines.append(f"Warning: {target} is on a noexec mount; move your home, or mount it without noexec")

    icon = install_icon(data_home())
    lines.append(f"Copied the icon to {icon}")
    desktop = desktop_path()
    os.makedirs(os.path.dirname(desktop), exist_ok=True)
    with open(desktop, "w") as f:
        f.write(DESKTOP_ENTRY.format(exec=desktop_exec(target), icon=APP_ICON, app_id=APP_ICON))
    os.chmod(desktop, 0o644)
    lines.append(f"Wrote {desktop}")
    if os.path.exists(autostart_path()):
        set_autostart(True, path=autostart_path(), command=desktop_exec(target), icon=icon)
        lines.append(f"Launch at Login now runs {target}")
    return lines


def uninstall():
    """Remove the copy, the menu entry, the icon and Launch at Login. The settings, and with them
    paired devices, stay. Idempotent; returns the lines to print."""
    lines = []
    for path in (install_path(), desktop_path(), icon_path()):
        try:
            os.unlink(path)
            lines.append(f"Removed {path}")
        except FileNotFoundError:
            lines.append(f"Not installed: {path}")
    autostart = autostart_path()
    try:
        with open(autostart) as f:
            ours = install_path() in f.read()
    except OSError:
        ours = False
    if ours:
        os.unlink(autostart)
        lines.append(f"Removed {autostart}")
    elif os.path.exists(autostart):
        lines.append(f"Kept {autostart}: it runs another copy")
    lines.append(f"Kept settings in {settings_path()}")
    return lines


def cli(argv):
    """`side-control --install` / `--uninstall`: print what was done, and return an exit status."""
    uninstall_requested = "--uninstall" in argv
    if uninstall_requested and "--install" in argv:
        print("Use either --install or --uninstall", file=sys.stderr)
        return 2
    try:
        lines = uninstall() if uninstall_requested else install()
    except (OSError, ValueError) as err:
        print(f"side-control: {err}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0
