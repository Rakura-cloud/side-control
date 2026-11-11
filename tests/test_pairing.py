# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing without Bluetooth: error codes and BlueZ's Paired signals, with fakes.

python -m unittest discover tests"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dbus.exceptions  # noqa: E402
from side_control.backend.bluez import PairWatch, address_of, pair_error_code  # noqa: E402
from side_control.backend.control import Control  # noqa: E402
from side_control.backend.events import EVENTS  # noqa: E402


ADAPTER = "/org/bluez/hci0"
OTHER_ADAPTER = "/org/bluez/hci1"
ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE = f"{ADAPTER}/dev_AA_BB_CC_DD_EE_FF"

IPHONE = {"Address": ADDRESS, "Adapter": ADAPTER, "Paired": True, "Trusted": True,
          "Class": 0x7A020C, "Icon": "phone", "Modalias": "bluetooth:v004Cp761Dd1B00",
          "Name": "Someone's iPhone", "Alias": "Someone's iPhone", "AddressType": "public"}
MOUSE = {"Address": "CC:CC:CC:CC:CC:CC", "Adapter": ADAPTER, "Paired": True,
         "Icon": "input-mouse", "Modalias": "usb:v046DpB023d0015",
         "Name": "MX Master 3", "Alias": "MX Master 3", "AddressType": "random"}


def device(**changes):
    return {**IPHONE, **changes}


def path_of(dev):
    return f"{dev['Adapter']}/dev_{dev['Address'].replace(':', '_')}"


class FakeObject:
    """A BlueZ object: the calls the pairing code makes, answered from FakeBus's tree."""

    def __init__(self, bus, path):
        self.bus, self.path = bus, path

    def get_dbus_method(self, name, _interface):
        if name == "GetManagedObjects":
            return lambda: self.bus.tree
        if name == "GetAll":
            return lambda _iface: self.bus.tree.get(self.path, {})
        if name == "Set":
            return lambda iface, prop, value: self.bus.sets.append((str(self.path), str(prop), value))
        raise AssertionError(f"unexpected D-Bus call {name}")


class FakeBus:
    """Signal receivers and the BlueZ object tree PairWatch and Control read."""

    def __init__(self, *devices):
        self.tree = {path_of(dev): {"org.bluez.Device1": dev} for dev in devices}
        self.handlers, self.sets = {}, []

    def add_signal_receiver(self, handler, member, *_args, **_kwargs):
        self.handlers[member] = handler

    def get_object(self, _service, path):
        return FakeObject(self, path)


class FakeFleet:
    """Stands in for Fleet: records what it was synced with and keeps a phone per address."""

    def __init__(self):
        self.phones, self.synced = {}, []
        self.dial_now = False

    def sync_paired(self, paired):
        self.synced.append(list(paired))
        for address, alias, _icon, _vendor in paired:
            self.phones.setdefault(address, types.SimpleNamespace(alias=alias, next_dial=1.0))


class PairErrors(unittest.TestCase):
    """Every BlueZ failure becomes one small error code; the app turns it into plain English."""

    @staticmethod
    def code(name):
        return pair_error_code(dbus.exceptions.DBusException("boom", name=name))

    def test_out_of_range_or_not_found(self):
        for name in ("org.bluez.Error.DoesNotExist", "org.bluez.Error.NotAvailable"):
            self.assertEqual(self.code(name), "not_found")

    def test_rejected_or_cancelled(self):
        for name in ("org.bluez.Error.AuthenticationRejected", "org.bluez.Error.AuthenticationCanceled",
                     "org.bluez.Error.AuthenticationFailed", "org.bluez.Error.Canceled",
                     "org.bluez.Error.Rejected"):
            self.assertEqual(self.code(name), "rejected")

    def test_timeout(self):
        for name in ("org.bluez.Error.Timeout", "org.freedesktop.DBus.Error.Timeout",
                     "org.freedesktop.DBus.Error.NoReply"):
            self.assertEqual(self.code(name), "timeout")

    def test_already_paired(self):
        for name in ("org.bluez.Error.AlreadyExists", "org.bluez.Error.AlreadyPaired"):
            self.assertEqual(self.code(name), "exists")

    def test_anything_else_is_a_plain_failure(self):
        self.assertEqual(self.code("org.bluez.Error.Failed"), "failed")
        self.assertEqual(self.code(None), "failed")
        self.assertEqual(pair_error_code(dbus.exceptions.DBusException("boom")), "failed")


class PairedSignals(unittest.TestCase):
    """PairWatch reports a phone or tablet that becomes paired - from either side - once per pairing."""

    def setUp(self):
        self.reported = []
        self.bus = FakeBus()
        self.watch = PairWatch(self.bus, ADAPTER, self.reported.append)

    def becomes(self, **changes):
        """BlueZ's object tree as it is once the device has `changes` (Paired flips and the like)."""
        self.bus.tree[DEVICE] = {"org.bluez.Device1": device(**changes)}
        return self.bus.tree[DEVICE]

    def test_a_pairing_from_the_phone_is_reported(self):
        """Paired flips on its own: the device is reported at once, not on the next reconnect pass."""
        self.becomes(Paired=False)  # seen, but not paired
        self.becomes()              # paired from the phone's own Bluetooth settings
        self.bus.handlers["PropertiesChanged"]("org.bluez.Device1", {"Paired": True}, [], path=DEVICE)
        self.assertEqual(self.reported, [ADDRESS])

    def test_interfaces_added_reports_it_too(self):
        """A device BlueZ only learns about while pairing (it was never discovered here)."""
        self.bus.handlers["InterfacesAdded"](DEVICE, self.becomes())
        self.assertEqual(self.reported, [ADDRESS])

    def test_one_report_per_pairing(self):
        added, changed = self.bus.handlers["InterfacesAdded"], self.bus.handlers["PropertiesChanged"]
        added(DEVICE, self.becomes())
        changed("org.bluez.Device1", {"Paired": True}, [], path=DEVICE)  # the Pair reply lands too
        self.assertEqual(self.reported, [ADDRESS])
        self.becomes(Paired=False)  # unpaired from the phone
        changed("org.bluez.Device1", {"Paired": False}, [], path=DEVICE)
        self.becomes()              # and paired again
        changed("org.bluez.Device1", {"Paired": True}, [], path=DEVICE)
        self.assertEqual(self.reported, [ADDRESS, ADDRESS])

    def test_already_paired_devices_are_not_reported(self):
        """What was paired before the backend started is not announced again (bluetoothd restarting
        re-adds every device)."""
        self.becomes()
        PairWatch(self.bus, ADAPTER, self.reported.append)  # starts out knowing it
        self.bus.handlers["InterfacesAdded"](DEVICE, self.bus.tree[DEVICE])
        self.assertEqual(self.reported, [])

    def test_forgotten_devices_are_reported_again(self):
        self.becomes()  # paired before the backend started: PairWatch knows it
        PairWatch(self.bus, ADAPTER, self.reported.append)
        removed, added = self.bus.handlers["InterfacesRemoved"], self.bus.handlers["InterfacesAdded"]
        removed(DEVICE, ["org.bluez.Device1"])  # forget in Side Control: RemoveDevice
        added(DEVICE, self.becomes())
        self.assertEqual(self.reported, [ADDRESS])

    def test_mice_and_other_adapters_are_ignored(self):
        """Only phones and tablets on this adapter become devices to control."""
        self.bus.handlers["InterfacesAdded"](path_of(MOUSE), {"org.bluez.Device1": MOUSE})
        self.bus.tree[DEVICE] = {"org.bluez.Device1": device(Adapter=OTHER_ADAPTER)}
        self.bus.handlers["PropertiesChanged"]("org.bluez.Device1", {"Paired": True}, [], path=DEVICE)
        self.assertEqual(self.reported, [])

    def test_address_of(self):
        self.assertEqual(address_of(DEVICE), ADDRESS)


class NotePaired(unittest.TestCase):
    """What the fleet and the app see the moment a device becomes paired."""

    def control(self, *devices):
        bus = FakeBus(*devices)
        control = Control.__new__(Control)  # no bridge, proximity or socket: just the pairing part
        control.bus, control.adapter_path = bus, ADAPTER
        control.fleet = FakeFleet()
        control.paired_seen = set()
        return control

    def test_the_fleet_and_the_app_learn_at_once(self):
        control = self.control(device())
        EVENTS.clear()
        with mock.patch("side_control.backend.control.log"):
            control.note_paired(ADDRESS)
        self.assertEqual(control.fleet.synced[-1], [(ADDRESS, "Someone's iPhone", "phone", 0x4C)])
        self.assertEqual(control.fleet.phones[ADDRESS].next_dial, 0.0)  # dial it right away
        self.assertTrue(control.fleet.dial_now)
        self.assertIn((DEVICE, "Trusted", True), control.bus.sets)
        self.assertIn({"type": "pairing", "address": ADDRESS, "step": "paired"}, list(EVENTS))
        self.assertIn({"type": "paired", "address": ADDRESS, "ok": True}, list(EVENTS))

    def test_one_paired_event_per_pairing(self):
        """The Paired signal and the Pair reply both land here: the app hears 'paired' once."""
        control = self.control(device())
        EVENTS.clear()
        with mock.patch("side_control.backend.control.log"):
            control.note_paired(ADDRESS)  # from BlueZ's Paired signal
            control.note_paired(ADDRESS)  # from our own Pair call
            self.assertEqual([e for e in EVENTS if e["type"] == "paired"],
                             [{"type": "paired", "address": ADDRESS, "ok": True}])
            control.paired_seen.discard(ADDRESS)  # forgotten: pairing again is announced again
            control.note_paired(ADDRESS)
        self.assertEqual(len([e for e in EVENTS if e["type"] == "paired"]), 2)
