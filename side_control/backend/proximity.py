# SPDX-License-Identifier: GPL-3.0-or-later
"""Signal strength of paired devices from LE advertisements, and the terminal panel."""

import atexit
import shutil
import sys
import time
from collections import deque

import dbus
from gi.repository import GLib

from .config import (
    AWAY_RSSI,
    GAUGE_MAX,
    GAUGE_MIN,
    HISTORY_S,
    NEAR_RSSI,
    PANEL_FPS,
    PANEL_ROWS,
    PATH_LOSS,
    REF_RSSI,
    SPARK,
    STALE_S,
    ZONES,
)
from .events import EVENTS, TERM_LOCK, log


def signal_color(rssi):
    """256-colour red -> yellow -> green along the gauge."""
    f = max(0.0, min(1.0, (rssi - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN)))
    return 196 if f < 0.2 else 208 if f < 0.4 else 220 if f < 0.6 else 154 if f < 0.8 else 46


def zone_for(value):
    return next((name, code) for limit, name, code in ZONES if value >= limit)


def distance_for(avg):
    return 10 ** ((REF_RSSI - avg) / (10 * PATH_LOSS))


def gauge_text(value, width):
    span = GAUGE_MAX - GAUGE_MIN
    filled = int(max(0.0, min(1.0, (value - GAUGE_MIN) / span)) * width)
    return "".join(
        f"\x1b[38;5;{signal_color(GAUGE_MIN + (i + 0.5) / width * span)}m█\x1b[0m" if i < filled
        else "\x1b[2m░\x1b[0m" for i in range(width))


class Signal:
    """Smoothed signal strength of one device."""

    def __init__(self, alias):
        self.alias = alias
        self.target = self.shown = self.last = None  # smoothed, displayed (eased), latest raw
        self.last_t = 0.0
        self.times = deque()  # (monotonic time, rssi)
        self.lo = self.hi = None
        self.near = False

    def rate(self):
        now = time.monotonic()
        recent = sum(1 for t, _ in self.times if now - t <= 10)
        span = min(10.0, now - self.times[0][0]) if self.times else 0
        return recent / span if span > 1 else 0.0

    def history(self, width):
        now = time.monotonic()
        buckets = [[] for _ in range(width)]
        for t, v in self.times:
            idx = width - 1 - int((now - t) * width / HISTORY_S)
            if 0 <= idx < width:
                buckets[idx].append(v)
        out, level = [], None
        for b in buckets:
            if b:
                level = sum(b) / len(b)
            if level is None:
                out.append(" ")
                continue
            f = (level - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN)
            out.append(f"\x1b[38;5;{signal_color(level)}m{SPARK[max(1, min(8, round(f * 8)))]}\x1b[0m")
        return "".join(out)


class Proximity:
    """How close each paired device is, from the RSSI of its Bluetooth LE advertisements.

    Classic Bluetooth only reports RSSI relative to an "ideal range" (0 almost everywhere),
    but BlueZ resolves a bonded device's LE advertisements to the same address and exposes
    their absolute signal strength in dBm: roughly -30 with the device touching the laptop,
    -50 at arm's length, below -70 across a room (varies with the device and its position).
    Devices advertise about every 0.5 s while awake; each one updates the panel pinned to
    the bottom of the terminal (one device: gauge, zone, rough distance, 60 s history; several:
    one line each).
    """

    def __init__(self, bus, adapter_path):
        self.bus, self.adapter_path = bus, adapter_path
        self.paired = {}   # device path -> alias
        self.signals = {}  # address -> Signal
        self.size = None
        self.ticks = 0
        self.panel = sys.stdout.isatty()
        self.scanning = False

    def start(self):
        if not self._scan(True):
            self.panel = False
            return
        self.refresh_paired()
        self.bus.add_signal_receiver(self.on_props, "PropertiesChanged",
                                     "org.freedesktop.DBus.Properties", path_keyword="path")
        GLib.timeout_add(1000 // PANEL_FPS, self.tick)  # runs in setup_bluez's GLib thread
        atexit.register(self.stop)

    def _scan(self, on):
        adapter = dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                                 "org.bluez.Adapter1")
        try:
            if on:
                # LE only: a classic inquiry would disturb the HID link; duplicates make BlueZ
                # report the RSSI of every advertisement, not just the first
                adapter.SetDiscoveryFilter({"Transport": "le", "DuplicateData": True})
                adapter.StartDiscovery()
            else:
                adapter.StopDiscovery()
        except dbus.DBusException as err:
            if err.get_dbus_name() not in ("org.bluez.Error.InProgress", "org.bluez.Error.Failed"):
                log(f"proximity scan {'start' if on else 'stop'} failed: {err.get_dbus_message()}")
                return False
        self.scanning = on
        return True

    def set_transport(self, transport):
        """"le" for proximity only, "auto" to also find unpaired classic devices for pairing."""
        def apply():
            adapter = dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path), "org.bluez.Adapter1")
            try:
                if self.scanning:
                    adapter.StopDiscovery()
                adapter.SetDiscoveryFilter({"Transport": transport, "DuplicateData": transport == "le"})
                adapter.StartDiscovery()
                self.scanning = True
            except dbus.DBusException as err:
                log(f"discovery ({transport}): {err.get_dbus_message()}")
            return False
        GLib.idle_add(apply)

    def set_scanning(self, on):
        """Pause / resume the LE scan. Scanning shares the radio with the HID link, so it is paused
        while a device has control. Runs on the GLib thread, where the other D-Bus work happens."""
        if on == self.scanning:
            return
        if not on:
            log("proximity scan paused while a device has control (keeps the radio free for the pointer)")
        GLib.idle_add(lambda: (self._scan(on), False)[1])

    def refresh_paired(self):
        objects = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
        self.paired = {str(p): str(i["org.bluez.Device1"].get("Alias", "device"))
                       for p, i in objects.items()
                       if "org.bluez.Device1" in i
                       and i["org.bluez.Device1"].get("Adapter") == self.adapter_path
                       and i["org.bluez.Device1"].get("Paired")}

    def on_props(self, iface, changed, invalidated, path=None):
        if iface == "org.bluez.Device1" and "RSSI" in changed and str(path) in self.paired:
            address = str(path).rsplit("dev_", 1)[1].replace("_", ":")
            self.reading(address, int(changed["RSSI"]), self.paired[str(path)])

    def reading(self, address, rssi, alias):
        now = time.monotonic()
        with TERM_LOCK:
            sig = self.signals.setdefault(address, Signal(alias))
            sig.alias, sig.last, sig.last_t = alias, rssi, now
            sig.target = rssi if sig.target is None else 0.5 * sig.target + 0.5 * rssi
            if sig.shown is None:
                sig.shown = sig.target
            sig.times.append((now, rssi))
            while now - sig.times[0][0] > HISTORY_S:
                sig.times.popleft()
            sig.lo = rssi if sig.lo is None else min(sig.lo, rssi)
            sig.hi = rssi if sig.hi is None else max(sig.hi, rssi)
            EVENTS.append({"type": "rssi", "address": address, "alias": alias, "rssi": rssi,
                           "avg": round(sig.target, 1), "zone": zone_for(sig.target)[0],
                           "distance": round(distance_for(sig.target), 2)})
            if not sig.near and sig.target >= NEAR_RSSI:
                sig.near = True
                log(f"*** {alias} is VERY NEAR ({sig.target:.0f} dBm) ***")
            elif sig.near and sig.target < AWAY_RSSI:
                sig.near = False
                log(f"{alias} moved away ({sig.target:.0f} dBm)")

    # --- panel ---

    def full_lines(self, sig, cols):
        """Three rows for a single device: header, gauge, 60 s history."""
        width = max(20, min(cols - 4, 100))
        age = time.monotonic() - sig.last_t
        sig.shown += (sig.target - sig.shown) * 0.4
        name, code = zone_for(sig.shown)
        asleep = "  \x1b[2;33mno recent signal - asleep?\x1b[0m" if age > STALE_S else ""
        head = (f" \x1b[1m{sig.alias}\x1b[0m {sig.last:4d} dBm \x1b[30;{code}m {name} \x1b[0m"
                f" ≈ {distance_for(sig.target):.1f} m \x1b[2m(rough)  min {sig.lo} max {sig.hi}  "
                f"{sig.rate():.1f}/s  {age:.1f}s ago\x1b[0m{asleep}")
        return [head, " " + gauge_text(sig.shown, width), " " + sig.history(width)]

    def compact_line(self, sig, cols):
        age = time.monotonic() - sig.last_t
        sig.shown += (sig.target - sig.shown) * 0.4
        name, code = zone_for(sig.shown)
        width = max(10, min(cols - 58, 40))
        asleep = " \x1b[2;33mzzz\x1b[0m" if age > STALE_S else ""
        return (f" \x1b[1m{sig.alias[:16]:<16}\x1b[0m {sig.last:4d} dBm \x1b[30;{code}m {name:^11} \x1b[0m"
                f" ≈{distance_for(sig.target):4.1f} m {gauge_text(sig.shown, width)}{asleep}")

    def lines(self, cols):
        sigs = sorted(self.signals.values(), key=lambda s: s.alias)
        if not sigs:
            return [" \x1b[2mproximity: waiting for the Bluetooth LE advertisements...\x1b[0m", "", ""]
        if len(sigs) == 1:
            return self.full_lines(sigs[0], cols)
        rows = [self.compact_line(s, cols) for s in sigs[:PANEL_ROWS]]
        return rows + [""] * (PANEL_ROWS - len(rows))

    def tick(self):
        if not self.panel:
            return True
        self.ticks += 1
        if self.ticks % (PANEL_FPS * 10) == 0:  # pick up a device paired after startup
            self.refresh_paired()
        cols, rows = shutil.get_terminal_size((80, 24))
        if rows < PANEL_ROWS + 8:
            return True
        with TERM_LOCK:
            out = ["\x1b7"]  # save cursor
            if self.size != (cols, rows):
                if self.size is None:
                    out.append("\n" * PANEL_ROWS + f"\x1b[{PANEL_ROWS}A")  # make room, keep position
                out.append(f"\x1b[1;{rows - PANEL_ROWS}r")  # log lines scroll above the panel
                self.size = (cols, rows)
            for k, line in enumerate(self.lines(cols)):
                out.append(f"\x1b[{rows - PANEL_ROWS + 1 + k};1H{line}\x1b[K")
            out.append("\x1b8")  # restore cursor
            sys.stdout.write("".join(out))
            sys.stdout.flush()
        return True

    def stop(self):
        with TERM_LOCK:
            if self.panel and self.size:
                cols, rows = self.size
                sys.stdout.write("\x1b[r" + "".join(f"\x1b[{rows - PANEL_ROWS + 1 + k};1H\x1b[K"
                                                     for k in range(PANEL_ROWS))
                                 + f"\x1b[{rows};1H\n")
                sys.stdout.flush()
            self.panel = False
        try:
            dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                           "org.bluez.Adapter1").StopDiscovery()
        except dbus.DBusException:
            pass
