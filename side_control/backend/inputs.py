# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC's keyboard, mouse and touchpad: finding them, finger tracking, shake detection."""

import math
import os
import time
from collections import deque

from evdev import ecodes as e
import evdev

from .config import (
    MOUSE_ACCEL_FIRST_S,
    MOUSE_ACCEL_INCLINE,
    MOUSE_ACCEL_MAX,
    MOUSE_ACCEL_SLOW,
    MOUSE_ACCEL_THRESHOLD,
    MOUSE_ACCEL_WINDOW_S,
    SHAKE_MIN_MM,
    SHAKE_TURNS,
    SHAKE_WINDOW_S,
)
from .events import log


class Touchpad:
    def __init__(self, dev):
        self.resx = dev.absinfo(e.ABS_X).resolution or 12
        self.resy = dev.absinfo(e.ABS_Y).resolution or 12
        mt = e.ABS_MT_SLOT in dev.capabilities(absinfo=False).get(e.EV_ABS, [])
        self.slot = dev.absinfo(e.ABS_MT_SLOT).value if mt else 0
        self.contacts = {}  # multitouch slot -> [x, y] of each finger on the pad
        self.three = None  # centroid x (mm) where a three-finger touch started, for switching devices
        self.three_done = False
        self.reset()

    def reset(self):
        self.touching = False
        self.x = self.y = self.last = None
        self.fingers = self.max_fingers = 0
        self.t0 = self.travel = self.scroll = self.swipe = 0.0
        self.swiped = False
        self.clicked = False
        self.click_bit = 0
        self.reset_gesture()

    def reset_gesture(self):
        self.gesture = None  # two-finger gesture once decided: "v" or "h" scroll, or "pinch"
        self.move = [0.0, 0.0]  # two-finger travel (mm) while still undecided
        self.zoom = self.scroll = 0.0
        self.pair = None  # centroid x, y and spread of two fingers (mm) at the last SYN
        self.flicked = False  # SWIPE_FLICK: this swipe has turned its page

    def track(self, ev):
        """Follow every finger through the multitouch slots, whoever has control."""
        if ev.type != e.EV_ABS:
            return
        if ev.code == e.ABS_MT_SLOT:
            self.slot = ev.value
        elif ev.code == e.ABS_MT_TRACKING_ID:
            if ev.value < 0:
                self.contacts.pop(self.slot, None)
            else:
                self.contacts[self.slot] = [None, None]
        elif ev.code in (e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y) and self.slot in self.contacts:
            self.contacts[self.slot][ev.code == e.ABS_MT_POSITION_Y] = ev.value

    def centroid_x(self, count):
        """Mean x in mm of the fingers on the pad if exactly `count` are down, else None."""
        xs = [p[0] for p in self.contacts.values() if p[0] is not None]
        return sum(xs) / len(xs) / self.resx if len(self.contacts) == count == len(xs) else None

    def two_fingers(self):
        """Centroid x, y and distance between two fingers in mm, or None."""
        pts = [p for p in self.contacts.values() if None not in p]
        if len(pts) != 2:
            return None
        (x1, y1), (x2, y2) = pts
        return ((x1 + x2) / 2 / self.resx, (y1 + y2) / 2 / self.resy,
                math.hypot((x1 - x2) / self.resx, (y1 - y2) / self.resy))


class Shake:
    """Notices the pointer being shaken: quick left-right reversals."""

    def __init__(self):
        self.dir = 0
        self.run = 0.0  # mm travelled in the current direction
        self.turns = deque()
        self.fired = 0.0

    def feed(self, mx):
        if not mx:
            return False
        d = 1 if mx > 0 else -1
        if d == self.dir:
            self.run += abs(mx)
            return False
        now = time.monotonic()
        if self.run >= SHAKE_MIN_MM:
            self.turns.append(now)
        self.dir, self.run = d, abs(mx)
        while self.turns and now - self.turns[0] > SHAKE_WINDOW_S:
            self.turns.popleft()
        if len(self.turns) >= SHAKE_TURNS and now - self.fired > 1.5:
            self.fired = now
            self.turns.clear()
            return True
        return False


class Accel:
    """Pointer acceleration for one mouse: collects a frame's motion, then scales it by how fast the
    mouse moves."""

    def __init__(self):
        self.frame = [0, 0]  # counts since the last SYN
        self.recent = deque()  # (time, counts) of the frames within MOUSE_ACCEL_WINDOW_S
        self.start = 0.0  # when the mouse started moving

    def take(self, now):
        """The frame's motion, accelerated, as (x, y) floats; starts the next frame."""
        (fx, fy), self.frame = self.frame, [0, 0]
        if not self.recent or now - self.recent[-1][0] > MOUSE_ACCEL_WINDOW_S:
            self.recent.clear()
            self.start = now - MOUSE_ACCEL_FIRST_S
        self.recent.append((now, math.hypot(fx, fy)))
        while now - self.recent[0][0] > MOUSE_ACCEL_WINDOW_S:
            self.recent.popleft()
        # over the time it has been moving, not the whole window: the first reports of a move would
        # read as creeping, and the pointer would lag behind the hand
        span = min(MOUSE_ACCEL_WINDOW_S, now - self.start)
        gain = self.gain(sum(d for _, d in self.recent) / (span * 1000))
        return fx * gain, fy * gain

    @staticmethod
    def gain(v):
        """libinput's linear profile: v in counts per ms."""
        if v < MOUSE_ACCEL_SLOW:
            return 10 * v + 0.3
        return min(MOUSE_ACCEL_MAX, max(1.0, 1 + MOUSE_ACCEL_INCLINE * (v - MOUSE_ACCEL_THRESHOLD)))


def is_touchpad(dev):
    caps = dev.capabilities(absinfo=False)
    return e.BTN_TOOL_FINGER in caps.get(e.EV_KEY, []) and e.ABS_X in caps.get(e.EV_ABS, [])


def wanted(dev):
    """A keyboard, mouse or touchpad: something to forward to the device in control."""
    caps = dev.capabilities(absinfo=False)
    return is_touchpad(dev) or e.REL_X in caps.get(e.EV_REL, []) or e.KEY_A in caps.get(e.EV_KEY, [])


def scan_inputs(seen):
    """Open the keyboards, mice and touchpads that appeared since the last call, as {fd: device}.

    `seen` holds the nodes already looked at, kept or not, and is updated. A node is its path and
    inode: a Bluetooth mouse that sleeps and reconnects gets a new node, maybe under the same path.
    """
    found, present = {}, set()
    for path in evdev.list_devices():
        try:
            node = path, os.stat(path).st_ino
        except OSError:
            continue
        present.add(node)
        if node in seen:
            continue
        try:
            dev = evdev.InputDevice(path)  # reads its capabilities too
        except OSError:  # gone again already: next time
            continue
        seen.add(node)
        if wanted(dev):
            found[dev.fd] = dev
            log(f"input: {dev.name}")
        else:
            dev.close()
    seen.intersection_update(present)
    return found
