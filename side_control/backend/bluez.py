# SPDX-License-Identifier: GPL-3.0-or-later
"""BlueZ over D-Bus: the HID profile, the pairing agent, paired and nearby devices."""

import os
import subprocess
import threading

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from . import config
from .config import NAME
from .events import log
from .hid import HID_UUID, SDP_RECORD


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


def watch_returns(bus, fleet):
    """A sign a paired device is back - BlueZ reporting an RSSI (every LE advertisement it resolves
    to a bonded device), or the link coming up - makes the fleet dial it at once instead of waiting
    for RECONNECT_EVERY. The link going down is no such sign, and the link coming up counts only when
    dial-only: otherwise it is most likely the device opening the HID channels itself, which a dial
    would race. Runs on the GLib thread, where the other D-Bus work happens."""

    def on_props(_iface, changed, _invalidated, path=None):
        if "RSSI" in changed or (changed.get("Connected") and config.DIAL_ONLY):
            fleet.back_in_range(str(path).rsplit("dev_", 1)[1].replace("_", ":"))

    bus.add_signal_receiver(on_props, "PropertiesChanged", "org.freedesktop.DBus.Properties",
                            arg0="org.bluez.Device1", path_keyword="path")


def paired_devices(bus, adapter_path):
    """Phones and tablets paired with this adapter; marks them trusted so they may reconnect. Other
    paired devices (a Bluetooth mouse, headphones) are left out: they would be dialed as if they were
    one, and paging them takes the radio away from them for seconds."""
    objects = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
    found = []
    for path, info in objects.items():
        dev = info.get("org.bluez.Device1")
        if not dev or dev.get("Adapter") != adapter_path or not dev.get("Paired") or not form_of(dev):
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


def form_of(dev):
    """A BlueZ Device1's form from its class and icon: phone, tablet, or None for anything else. LE-only
    devices (most recent mice and keyboards) have no class: HID here runs over BR/EDR anyway."""
    cls = int(dev.get("Class", 0))
    major, minor = (cls >> 8) & 0x1F, (cls >> 2) & 0x3F
    icon, name = str(dev.get("Icon", "")), str(dev.get("Name", dev.get("Alias", "")))
    apple = vendor_of(dev.get("Modalias", "")) == 0x004C or name.lower().startswith(("iphone", "ipad"))
    if major == 2 or icon == "phone":
        return "phone"
    if (major == 1 and minor in (4, 5, 7)) or (apple and "ipad" in name.lower()):
        return "tablet"
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
        form, name = form_of(dev), str(dev["Name"])
        if not form:
            continue
        apple = vendor_of(dev.get("Modalias", "")) == 0x004C or name.lower().startswith(("iphone", "ipad"))
        found.append({"address": str(dev["Address"]), "name": name, "form": form,
                      "os": ("iPadOS" if form == "tablet" else "iOS") if apple else "Android"})
    return sorted(found, key=lambda d: d["name"].lower())
