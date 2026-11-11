# SPDX-License-Identifier: GPL-3.0-or-later
"""The paired iPhones, iPads and Android devices, and their HID connections."""

import errno
import fcntl
import socket
import struct
import threading
import time
from collections import deque

from . import config
from .config import COUNTS_PER_POINT, DEFAULT_SCREEN, DIAL_ONLY_MAX_S, DIAL_TRIGGER_GAP_S, RECONNECT_EVERY
from .events import log
from .gatt import GATT_BATTERY, GATT_MANUFACTURER, GATT_MODEL, GATT_SOFTWARE, read_gatt
from .hid import BACKLOG_BYTES, PSM_CTRL, PSM_INTR, TIOCOUTQ, dial, disable_sniff


DEVICE_MODELS = {  # Model Number String -> marketing name (unknown ones are shown as the raw id)
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13", "iPhone14,6": "iPhone SE (3rd gen)", "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
    "iPad13,16": "iPad Air (5th gen)", "iPad13,17": "iPad Air (5th gen)",
    "iPad13,18": "iPad (10th gen)", "iPad13,19": "iPad (10th gen)",
    "iPad14,1": "iPad mini (6th gen)", "iPad14,2": "iPad mini (6th gen)",
    "iPad14,8": "iPad Air 11-inch (M2)", "iPad14,9": "iPad Air 11-inch (M2)",
    "iPad14,10": "iPad Air 13-inch (M2)", "iPad14,11": "iPad Air 13-inch (M2)",
    "iPad16,1": "iPad mini (A17 Pro)", "iPad16,2": "iPad mini (A17 Pro)",
    "iPad16,3": "iPad Pro 11-inch (M4)", "iPad16,4": "iPad Pro 11-inch (M4)",
    "iPad16,5": "iPad Pro 13-inch (M4)", "iPad16,6": "iPad Pro 13-inch (M4)",
}
SCREEN_POINTS = {  # portrait screen size in points -> the Model Number Strings that have it
    (375, 667): "iPhone9,1 iPhone9,3 iPhone10,1 iPhone10,4 iPhone12,8 iPhone14,6",
    (414, 736): "iPhone9,2 iPhone9,4 iPhone10,2 iPhone10,5",
    (375, 812): "iPhone10,3 iPhone10,6 iPhone11,2 iPhone12,3 iPhone13,1 iPhone14,4",
    (414, 896): "iPhone11,4 iPhone11,6 iPhone11,8 iPhone12,1 iPhone12,5",
    (390, 844): "iPhone13,2 iPhone13,3 iPhone14,2 iPhone14,5 iPhone14,7 iPhone17,5",
    (428, 926): "iPhone13,4 iPhone14,3 iPhone14,8",
    (393, 852): "iPhone15,2 iPhone15,4 iPhone16,1 iPhone17,3",
    (430, 932): "iPhone15,3 iPhone15,5 iPhone16,2 iPhone17,4",
    (402, 874): "iPhone17,1 iPhone18,1 iPhone18,3",
    (440, 956): "iPhone17,2 iPhone18,2",
    (420, 912): "iPhone18,4",
    (768, 1024): "iPad11,1 iPad11,2",
    (810, 1080): "iPad7,11 iPad7,12 iPad11,6 iPad11,7 iPad12,1 iPad12,2",
    (834, 1112): "iPad11,3 iPad11,4",
    (744, 1133): "iPad14,1 iPad14,2 iPad16,1 iPad16,2",
    (820, 1180): "iPad13,1 iPad13,2 iPad13,16 iPad13,17 iPad13,18 iPad13,19 iPad14,8 iPad14,9 "
                 "iPad15,3 iPad15,4 iPad15,7 iPad15,8",
    (834, 1194): "iPad8,1 iPad8,2 iPad8,3 iPad8,4 iPad8,9 iPad8,10 iPad13,4 iPad13,5 iPad13,6 iPad13,7 "
                 "iPad14,3 iPad14,4",
    (1024, 1366): "iPad8,5 iPad8,6 iPad8,7 iPad8,8 iPad8,11 iPad8,12 iPad13,8 iPad13,9 iPad13,10 iPad13,11 "
                  "iPad14,5 iPad14,6 iPad14,10 iPad14,11 iPad15,5 iPad15,6",
    (834, 1210): "iPad16,3 iPad16,4",
    (1032, 1376): "iPad16,5 iPad16,6",
}
SCREEN_OF = {model: size for size, models in SCREEN_POINTS.items() for model in models.split()}


class NoPhone:
    """Stand-in output device while nothing has been connected yet."""
    connected = False
    peer = address = None

    def send(self, *args):
        pass

    def backlogged(self):
        return False


NO_PHONE = NoPhone()


class Phone:
    """One paired iPhone or iPad: its HID channels plus its own hand-off settings."""

    def __init__(self, dev_id, address, alias):
        self.dev_id, self.address, self.alias = dev_id, address, alias
        self.ctrl = self.intr = None
        self.peer = None   # == address while connected
        self.model = None  # e.g. "iPhone16,1", read once per connection
        self.manufacturer = self.software = None
        self.battery = None  # percent, read every BATTERY_EVERY s while connected
        self.battery_t = 0.0
        self.icon, self.vendor = "", None  # from BlueZ
        self.dialing = False
        self.dial_fails = 0
        self.next_dial = 0.0
        self.back_t = 0.0  # when a sign it was back last beat the dial timer (DIAL_TRIGGER_GAP_S)
        # hand-off settings, pushed by the GUI
        self.zone = None
        self.span = None  # (lo, hi) fraction of a side edge that leads here; None: all of it
        self.require_near = True
        self.screen_w = self.screen_h = None  # None: default for the device kind
        self.speed = 1.0                      # pointer speed multiplier

    @property
    def apple(self):
        if self.vendor is not None:
            return self.vendor == 0x004C
        if self.manufacturer:
            return self.manufacturer.lower().startswith("apple")
        return True  # until we know better: iPhones and iPads were all this supported

    @property
    def form(self):
        if self.apple:
            return "tablet" if "ipad" in (self.model or self.alias).lower() else "phone"
        return "phone" if self.icon in ("phone", "") else "tablet"

    @property
    def os(self):
        return ("iPadOS" if self.form == "tablet" else "iOS") if self.apple else "Android"

    @property
    def kind(self):  # sizes are keyed by these
        return "iPad" if self.form == "tablet" else "iPhone"

    def size(self):
        """Screen size in mouse counts, held the way the arrangement shows it: phones upright, tablets
        on their side. Known models use their real screen; Bluetooth itself doesn't tell."""
        points = SCREEN_OF.get(self.model)
        if points:
            short, long = (round(v * COUNTS_PER_POINT) for v in points)
            w, h = (long, short) if self.form == "tablet" else (short, long)
        else:
            w, h = DEFAULT_SCREEN[self.kind]
        return self.screen_w or w, self.screen_h or h

    @property
    def model_name(self):
        return DEVICE_MODELS.get(self.model, self.model)

    def _read_model(self):
        info = read_gatt(self.address, [GATT_MODEL, GATT_MANUFACTURER, GATT_SOFTWARE, GATT_BATTERY])
        if self.peer != self.address:
            return
        text = lambda uuid: info[uuid].decode(errors="replace").strip("\0 ") if uuid in info else None  # noqa: E731
        self.manufacturer, self.software = text(GATT_MANUFACTURER), text(GATT_SOFTWARE)
        self._set_battery(info.get(GATT_BATTERY))
        model = text(GATT_MODEL)
        if model:
            self.model = model
            log(f"{self.alias}: {self.model_name}"
                + (f" ({model})" if self.model_name != model else ""))

    def _set_battery(self, value):
        self.battery_t = time.monotonic()
        if value:
            self.battery = min(100, value[0])

    def _read_battery(self):
        self._set_battery(read_gatt(self.address, [GATT_BATTERY]).get(GATT_BATTERY))

    def refresh_battery(self):
        self.battery_t = time.monotonic()
        threading.Thread(target=self._read_battery, daemon=True).start()

    def connected_to(self):
        self.peer = self.address
        self.model = None
        disable_sniff(self.dev_id, self.address)
        threading.Thread(target=self._read_model, daemon=True).start()

    @property
    def connected(self):
        return self.ctrl is not None and self.intr is not None

    def send(self, report_id, payload):
        if not self.connected:
            return
        try:
            self.intr.send(bytes([0xA1, report_id]) + bytes(payload))
        except OSError as err:
            log(f"send failed: {err}")
            self.close()

    def backlogged(self):
        """True while earlier reports pile up in the kernel's send queue."""
        try:
            # On Bluetooth sockets TIOCOUTQ is the *free* send-buffer space
            free = struct.unpack("i", fcntl.ioctl(self.intr, TIOCOUTQ, b"\0" * 4))[0]
            sndbuf = self.intr.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        except OSError:
            return False
        return sndbuf - free > BACKLOG_BYTES

    def handle_ctrl(self):
        try:
            msg = self.ctrl.recv(64)
        except OSError:
            msg = b""
        if not msg:
            self.close()
            return
        kind = msg[0] >> 4
        if kind == 0x4:  # GET_REPORT
            rid = msg[1] if len(msg) > 1 else 1
            self.ctrl.send(bytes([0xA1, rid]) + bytes(8 if rid == 1 else 5))
        elif kind == 0x6:  # GET_PROTOCOL -> report protocol
            self.ctrl.send(b"\xa0\x01")
        elif kind == 0x1 and msg[0] & 0x0F == 0x5:  # VIRTUAL_CABLE_UNPLUG
            self.close()
        elif kind in (0x5, 0x7, 0x9):  # SET_REPORT / SET_PROTOCOL / SET_IDLE
            self.ctrl.send(b"\x00")

    def dial_hid(self, out):
        """Thread body: connect to the device ourselves, like a real keyboard does after power-on.
        Paging an absent device blocks for seconds, so this must not run on the input loop."""
        try:
            ctrl = dial(self.address, PSM_CTRL)
            try:
                intr = dial(self.address, PSM_INTR)
            except OSError as err:
                ctrl.close()
                log(f"{self.alias}: interrupt channel failed: {err}")
                raise
            self.dial_fails = 0
            out.append((self, ctrl, intr))
        except OSError as err:
            if err.errno == errno.EBADE and not self.dial_fails:  # HCI "PIN or Key Missing"
                log(f"{self.alias} no longer knows this computer (it was forgotten there): forget it in "
                    "Side Control too and pair again")
            self.dial_fails += 1
            self.next_dial = time.monotonic() + min(RECONNECT_EVERY * 2 ** self.dial_fails,
                                                    DIAL_ONLY_MAX_S if config.DIAL_ONLY else 120)
        finally:
            self.dialing = False

    def close(self):
        if self.ctrl or self.intr:
            log(f"{self.alias} disconnected")
        for s in (self.ctrl, self.intr):
            if s:
                s.close()
        self.ctrl = self.intr = self.peer = self.model = None
        self.battery = None


class Fleet:
    """Every paired iPhone / iPad, connected or not, keyed by Bluetooth address."""

    def __init__(self, dev_id):
        self.dev_id = dev_id
        self.phones = {}
        self.dialed = deque()  # (phone, ctrl, intr) handed over by dial threads
        self.dial_now = False   # set after pairing, or when a device shows a sign of being back:
                                # dial without waiting for RECONNECT_EVERY

    def get(self, address, alias=None):
        phone = self.phones.get(address)
        if phone is None:
            phone = self.phones[address] = Phone(self.dev_id, address, alias or address)
        elif alias:
            phone.alias = alias
        return phone

    def sync_paired(self, paired):
        for address, alias, icon, vendor in paired:
            phone = self.get(address, alias)
            phone.icon, phone.vendor = icon, vendor

    def connected(self):
        return [p for p in self.phones.values() if p.connected]

    def back_in_range(self, address):
        """A sign a paired device is back - an advertisement, the link coming up: dial it at once
        (the main loop honours dial_now) instead of waiting for RECONNECT_EVERY. One sign per device
        counts at most every DIAL_TRIGGER_GAP_S: advertisements keep coming while it is here, and one
        that cannot be reached must not be paged over and over."""
        phone = self.phones.get(address)  # anything else is not ours to dial
        now = time.monotonic()
        if phone is None or now - phone.back_t < DIAL_TRIGGER_GAP_S:
            return
        phone.back_t = now
        phone.next_dial = 0.0  # the sign beats the failure back-off
        self.dial_now = True

    def dial_missing(self):
        now = time.monotonic()
        for phone in self.phones.values():
            if not (phone.connected or phone.dialing or now < phone.next_dial):
                phone.dialing = True
                threading.Thread(target=phone.dial_hid, args=(self.dialed,), daemon=True).start()

    def accept_dialed(self):
        while self.dialed:
            phone, ctrl, intr = self.dialed.popleft()
            # it connected to us in the meantime, or was forgotten
            if phone.connected or self.phones.get(phone.address) is not phone:
                ctrl.close()
                intr.close()
                continue
            phone.close()
            phone.ctrl, phone.intr = ctrl, intr
            phone.connected_to()
            log(f"connected to {phone.alias} - press Ctrl+Alt+P to control it")
