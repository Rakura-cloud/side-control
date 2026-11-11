# SPDX-License-Identifier: GPL-3.0-or-later
"""The unix socket the app talks to."""

import atexit
import json
import os
import socket
import time

from evdev import ecodes as e
import dbus
from gi.repository import GLib

from . import config
from .bluez import nearby_devices, paired_devices
from .config import (
    ACTIONS,
    CALIBRATE_MAX_S,
    CALIBRATE_RATE,
    CALIBRATE_REACTION_S,
    OPTIONS,
    PIN_EXTRA,
    SOCK_PATH,
)
from .events import EVENTS, log
from .proximity import zone_for


class Control:
    """Newline-delimited JSON over a unix socket in the user's runtime directory, so the GUI can
    watch and drive this process.

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
                           {"type": "paste"}  Ctrl+V / Super+V on a device (Share Clipboard): read the
                            clipboard and answer with type_text
    client -> server also: {"cmd": "type_text", "text": ...}  type this text on the device in control;
                            no text: the paste chord goes to the device instead
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
        os.chmod(SOCK_PATH, 0o600)  # only the user we run as may talk to us
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
            phone = self.fleet.phones.get(msg["address"])
            if phone is None:  # not a paired phone or tablet (an app that still lists a mouse)
                return
            self.bridge.handoff.configure(phone, msg.get("zone"), msg.get("require_near", True),
                                          msg.get("phone_w"), msg.get("phone_h"),
                                          msg.get("speed"), msg.get("span"))
        elif cmd == "forget":
            self.forget(msg["address"])
        elif cmd == "set_options":
            for key, value in msg.items():
                if key in OPTIONS:
                    name, convert = OPTIONS[key]
                    setattr(config, name, convert(value))
            if msg.get("device_name"):
                self.set_adapter("Alias", dbus.String(str(msg["device_name"])[:64]))
            if "discoverable" in msg:
                self.set_adapter("Discoverable", dbus.Boolean(bool(msg["discoverable"])))
            if "locked" in msg:
                self.bridge.locked = bool(msg["locked"])
            if "hide_cursor" in msg:
                self.bridge.handoff.sync_cursor()
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
        elif cmd == "type_text":
            self.bridge.type_text(msg.get("text"))
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

    def request_paste(self):
        """A paste chord on a device (Share Clipboard): ask the GUI to read the computer's clipboard
        and answer with type_text. False: no GUI is attached to read it, so the chord must go to the
        device unchanged instead."""
        if not self.clients:
            return False
        EVENTS.append({"type": "paste"})
        return True

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
                "sharing": config.SHARE_CURSOR, "locked": self.bridge.locked,
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
