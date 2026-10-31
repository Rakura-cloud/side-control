# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts about this computer: bluetoothd's plugins, Bluetooth adapters, Hyprland."""

import os
import socket

from .config import DROPIN_DIR


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
