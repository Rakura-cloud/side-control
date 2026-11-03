# SPDX-License-Identifier: GPL-3.0-or-later
"""Starts everything and runs the event loop."""

import os
import select
import socket
import sys
import time

import dbus

from . import config
from .bluez import paired_devices, setup_bluez
from .bridge import Bridge
from .compositor import Hyprland
from .config import BATTERY_EVERY, INPUT_RESCAN_S, MOUSE_INTERVAL, NAME, RECONNECT_EVERY, SOCK_PATH
from .control import Control
from .devices import Fleet
from .events import log
from .handoff import Handoff
from .hid import PSM_CTRL, PSM_INTR, listen
from .proximity import Proximity


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(SOCK_PATH)
        sys.exit("the Side Control backend is already running (its control socket answers) - stop it first")
    except OSError:
        pass  # nobody listening: good
    finally:
        probe.close()
    try:
        adapter = sys.argv[sys.argv.index("--adapter") + 1] if "--adapter" in sys.argv[:-1] else None
        addr, bus, adapter_path = setup_bluez(adapter)
    except dbus.DBusException as err:
        sys.exit(f"BlueZ setup failed: {err.get_dbus_message()}\n"
                 "Is another copy of the backend still running? Check: pgrep -af side-control-backend")
    try:
        ctrl_l, intr_l = listen(addr, PSM_CTRL), listen(addr, PSM_INTR)
    except OSError as err:
        # bluetoothd's input plugin holds them, so Bluetooth mice and keyboards can connect to this PC:
        # devices cannot connect to us then, but we still connect to them
        config.DIAL_ONLY = True
        ctrl_l = intr_l = None
        log(f"HID channels (PSM {PSM_CTRL}/{PSM_INTR}) are taken ({err.strerror}): probably bluetoothd's input "
            "plugin. Connecting to devices from here only; to let them connect too, start bluetoothd with "
            "-P input (side_control/data/bluetooth-no-input.conf)")
    fleet = Fleet(int(adapter_path.rsplit("hci", 1)[1]))
    fleet.sync_paired(paired_devices(bus, adapter_path))
    bridge = Bridge(fleet)
    sys.setswitchinterval(0.001)  # the GLib thread (scan, panel) must not hold up pointer reports
    proximity = Proximity(bus, adapter_path)
    proximity.start()
    bridge.proximity = proximity
    bridge.handoff = Handoff(bridge, fleet, proximity, Hyprland())
    control = Control(bridge, fleet, proximity, bus, adapter_path)
    control.start()
    log(f'"{NAME}" is discoverable - pair each iPhone / iPad from its Bluetooth settings')
    last_dial = last_scan = 0.0

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
        if now - last_scan > INPUT_RESCAN_S:
            last_scan = now
            bridge.rescan()
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
