# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts about this computer: bluetoothd's plugins, the backend's service, Bluetooth adapters, and
the session's compositor (Hyprland's IPC socket, the compositor's environment)."""

import os
import shlex
import shutil
import socket
import subprocess
import sys

from .config import DROPIN_DIR, GNOME_EXTENSION, RULES_PATH, SERVICE_RULES, SERVICE_UNIT, UNIT_PATH


def hid_owned():
    """True if bluetoothd runs without its input plugin (so the backend can own HID), None if
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


def install_file(text, path):
    """A shell command that writes `text` to `path`, for root to run: root cannot read inside the
    AppImage, so files are written from their text."""
    path = shlex.quote(path)
    return f"mkdir -p \"$(dirname {path})\" && printf %s {shlex.quote(text)} > {path} && chmod 644 {path}"


def service_installed():
    """True if the backend's service is installed as this version of Side Control wants it."""
    try:
        with open(SERVICE_UNIT) as ours, open(UNIT_PATH) as installed:
            return ours.read() == installed.read()
    except OSError:
        return False


def setup_script():
    """The shell command, for root, that installs the backend's service and its polkit rule."""
    with open(SERVICE_UNIT) as unit, open(SERVICE_RULES) as rules:
        return " && ".join([install_file(unit.read(), UNIT_PATH), install_file(rules.read(), RULES_PATH),
                            "systemctl daemon-reload", "sleep 1"])  # polkitd picks up its new rule meanwhile


def install_gnome_extension():
    """Copy the GNOME Shell extension into the user's extensions directory and enable it: on
    GNOME it is what says where the pointer is and moves it (backend/compositor.py, Mutter), the
    shell keeping both to itself on Wayland. Raises OSError with what went wrong. On Wayland the
    extension only loads after the next login."""
    uuid = os.path.basename(GNOME_EXTENSION)
    dest = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "gnome-shell", "extensions", uuid)
    shutil.copytree(GNOME_EXTENSION, dest, dirs_exist_ok=True)
    try:
        run = subprocess.run(["gnome-extensions", "enable", uuid],
                             capture_output=True, text=True, timeout=10)
    except subprocess.SubprocessError as err:
        raise OSError(None, str(err)) from err
    if run.returncode:
        raise OSError(None, run.stderr.strip() or f"gnome-extensions exited with {run.returncode}")


def last_log_line(unit):
    """The last line `unit` logged, if this user may read the journal."""
    try:
        return subprocess.run(["journalctl", f"_SYSTEMD_UNIT={unit}", "-n", "1", "-o", "cat", "-q", "--no-pager"],
                              capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


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


_missing_logged = False


def hyprland(command):
    """Send one command to Hyprland's IPC socket; None if Hyprland is not there. The edge indicator
    polls this, so the absence is logged once, not once per poll."""
    global _missing_logged
    sig, runtime = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"), os.environ.get("XDG_RUNTIME_DIR")
    if sig and runtime:
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
            pass
    if not _missing_logged:
        _missing_logged = True
        why = "HYPRLAND_INSTANCE_SIGNATURE is not set" if not sig else "its IPC socket does not answer"
        print(f"side-control: no Hyprland ({why}): the edge indicator cannot follow the cursor and "
              "Shake to Locate does nothing", file=sys.stderr, flush=True)
    return None


COMPOSITOR_ENV = ("HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET", "XDG_CURRENT_DESKTOP")


def compositor_exports():
    """The session's compositor variables, as shell export lines for the command the backend's
    service runs (bluetooth.py): the service starts without the session's environment, and the
    backend picks its compositor from these (backend/compositor.py, detect())."""
    return "\n".join(f"export {var}={shlex.quote(os.environ[var])}" for var in COMPOSITOR_ENV
                     if os.environ.get(var))
