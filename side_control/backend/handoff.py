# SPDX-License-Identifier: GPL-3.0-or-later
"""Continuity-style edge hand-off, and the model of the device pointer it keeps."""

import time

from . import config
from .config import (
    CORNER_FRAC,
    EDGE_MARGIN_PX,
    EDGE_PUSH_MM,
    HANDOFF_RSSI,
    HANDOFF_STALE_S,
    HANDOFF_ZONES,
    OPPOSITE,
    PIN_EXTRA,
    RETURN_INSET_PX,
    RETURN_PUSH,
)
from .events import log
from ..monitors import SIDES, along_edge, outer_edges, point_on_edge


def handoff_exits(zone, span=None):
    """[(screen edge, predicate on the position along that edge, 0..1)] that lead to the phone.

    The fraction runs top -> bottom along the left/right edges and left -> right along top/bottom.
    span (lo, hi) limits a side zone to that part of its edge, so several devices can share one.
    """
    c = CORNER_FRAC
    near_start, near_end = (lambda f: f <= c), (lambda f: f >= 1 - c)
    lo, hi = span or (0.0, 1.0)
    anywhere = lambda f: lo <= f <= hi  # noqa: E731
    return {
        "left": [("left", anywhere)], "right": [("right", anywhere)],
        "top": [("top", anywhere)], "bottom": [("bottom", anywhere)],
        "top-left": [("left", near_start), ("top", near_start)],
        "top-right": [("right", near_start), ("top", near_end)],
        "bottom-left": [("left", near_end), ("bottom", near_start)],
        "bottom-right": [("right", near_end), ("bottom", near_end)],
    }[zone]


class Handoff:
    """Like Apple's Continuity: push the pointer off the screen edge towards a device and it
    appears on that device; push it off the device's opposite edge and it comes back.

    Each device has its own zone (where it sits around the screen), "only when near" flag and
    screen size, so an iPhone on the left and an iPad on the right can both be reached.

    PC -> device: Bridge feeds finger motion in here while the PC has control. When the pointer sits
    on a configured edge (found via Hyprland) and the finger keeps pushing, and - if asked - the
    device is near enough (Proximity), control switches and the device's pointer is pinned against
    the side it enters from, at the same relative height.

    device -> PC: the device never reports its pointer position, so we keep a model of it: it is known
    right after the pin, and every report we send moves it (clamped to the screen size). Pushing past
    the entry edge brings control back and warps the PC pointer to the matching spot.
    """

    def __init__(self, bridge, fleet, proximity, compositor):
        self.bridge, self.fleet, self.proximity, self.comp = bridge, fleet, proximity, compositor
        self.push = {}    # (address, edge) -> mm of finger travel against that edge
        self.push_t = {}  # (address, edge) -> when that push started
        self.edge = None  # PC edge the pointer left through, while a device has control
        self.entry_frac = 0.5
        self.vx = self.vy = 0.0  # modelled device pointer, counts from the top-left
        self.return_push = 0.0
        self.blocked_logged = 0.0
        self.rects = self.outer = None

    def edges(self):
        """The monitors' outer edges, worked out again only when the layout changes."""
        rects = self.comp.layout()
        if rects != self.rects:
            self.rects, self.outer = rects, outer_edges(rects) if rects else None
        return self.outer

    def configure(self, phone, zone, require_near, screen_w, screen_h, speed=None, span=None):
        phone.zone = zone if zone in HANDOFF_ZONES else None
        phone.span = (max(0.0, min(1.0, float(span[0]))), max(0.0, min(1.0, float(span[1])))) \
            if span else None
        phone.require_near = bool(require_near)
        # 0 / None: use the default for this kind of device
        phone.screen_w = max(100, min(3000, int(screen_w))) if screen_w else None
        phone.screen_h = max(100, min(3000, int(screen_h))) if screen_h else None
        phone.speed = max(0.3, min(4.0, float(speed))) if speed else 1.0
        self.push.clear()
        self.push_t.clear()
        log(f"hand-off {phone.alias}: at the {phone.zone} of the screen"
            + (" (only when near)" if phone.require_near else "") if phone.zone
            else f"hand-off {phone.alias}: off")
        if phone.zone:
            lo, hi = phone.span or (0.0, 1.0)
            clash = next((o.alias for o in self.fleet.phones.values()
                          if o is not phone and o.zone == phone.zone
                          and lo < (o.span or (0.0, 1.0))[1] and (o.span or (0.0, 1.0))[0] < hi), None)
            if clash:
                log(f"hand-off: {clash} is also set to the {phone.zone}; the first one found wins")
            if not self.comp.available:
                log("hand-off needs Hyprland: no IPC socket found, the PC pointer cannot be located")

    def status(self):
        return {"available": self.comp.available,
                "edge": self.edge if self.bridge.on_phone else None}

    # --- PC -> device ---

    def near_ok(self, phone):
        if not phone.require_near:
            return True
        sig = self.proximity.signals.get(phone.address)
        now = time.monotonic()
        near = (sig is not None and now - sig.last_t < HANDOFF_STALE_S
                and sig.target >= HANDOFF_RSSI)
        if not near and now - self.blocked_logged > 5:
            self.blocked_logged = now
            seen = "no signal" if sig is None else f"{sig.target:.0f} dBm"
            log(f"hand-off to {phone.alias} blocked: not near ({seen}, need {HANDOFF_RSSI} dBm or stronger)")
        return near

    def pc_motion(self, mx, my):
        """Finger / mouse motion in mm (x right, y down) while the PC has control."""
        if self.bridge.on_phone or not config.SHARE_CURSOR or self.bridge.locked:
            return
        phones = [p for p in self.fleet.connected() if p.zone]
        if not phones:
            return
        pos, edges = self.comp.cursor(), self.edges()
        if not pos or not edges:
            return
        x, y = pos
        along = {side: along_edge(edges[side], side, x, y, EDGE_MARGIN_PX) for side in SIDES}
        toward = {"left": -mx, "right": mx, "top": -my, "bottom": my}
        for phone in phones:
            for edge, in_zone in handoff_exits(phone.zone, phone.span):
                key = (phone.address, edge)
                if along[edge] is not None and in_zone(along[edge]):
                    if not self.push.get(key):
                        self.push_t[key] = time.monotonic()
                    self.push[key] = max(0.0, self.push.get(key, 0.0) + toward[edge])
                    if (self.push[key] >= EDGE_PUSH_MM and self.near_ok(phone)
                            and time.monotonic() - self.push_t[key] >= config.SWITCH_DELAY_S):
                        self.enter(phone, edge, along[edge])
                        return
                else:
                    self.push[key] = 0.0

    def enter(self, phone, edge, frac):
        self.edge, self.entry_frac = edge, frac
        self.bridge.switch_to(phone)
        if not self.bridge.on_phone:  # device vanished meanwhile
            self.edge = None

    def on_enter(self):
        """Called by Bridge.switch_to when a device takes over: pin its pointer where it comes in."""
        self.sync_cursor()
        phone = self.bridge.phone
        if not phone.zone:
            return
        if self.edge is None:  # manual switch (hotkey / GUI): enter as if through the zone's first edge
            self.edge = handoff_exits(phone.zone)[0][0]
            self.entry_frac = sum(phone.span) / 2 if phone.span and phone.zone in OPPOSITE else 0.5
        w, h = phone.size()
        frac = self.entry_frac
        # the pointer enters through the device side opposite to the PC edge it left by; pin it into
        # the nearer corner of that side first (the device clamps at its screen edges), then glide
        # along the side. Sent in one burst, iOS pointer acceleration would carry the pointer much
        # further than the counts say, and it would come in at the wrong height
        sx, sy = {"left": (1, 0), "right": (-1, 0), "top": (0, 1), "bottom": (0, -1)}[self.edge]
        end = 1 if frac > 0.5 else -1
        sx, sy = (sx, end) if sx else (end, sy)
        self.bridge.send_mouse(sx * (w + PIN_EXTRA), sy * (h + PIN_EXTRA))
        self.vx, self.vy = (w if sx > 0 else 0.0), (h if sy > 0 else 0.0)
        if self.edge in ("left", "right"):
            move = round(frac * h - self.vy)
            self.bridge.glide(0, move)
            self.vy += move
        else:
            move = round(frac * w - self.vx)
            self.bridge.glide(move, 0)
            self.vx += move
        self.return_push = 0.0
        log(f"pointer entered {phone.alias} from its {OPPOSITE[self.edge]} side "
            f"(push past that side to come back)")

    # --- device -> PC ---

    def phone_motion(self, ix, iy):
        """Counts just sent to the device while it has control."""
        if self.edge is None or not self.bridge.on_phone or self.bridge.locked:
            return
        w, h = self.bridge.phone.size()
        self.vx = max(0.0, min(w, self.vx + ix))
        self.vy = max(0.0, min(h, self.vy + iy))
        back = OPPOSITE[self.edge]  # device side that leads back to the PC
        at = {"left": self.vx <= 0, "right": self.vx >= w, "top": self.vy <= 0, "bottom": self.vy >= h}[back]
        toward = {"left": -ix, "right": ix, "top": -iy, "bottom": iy}[back]
        if at and toward > 0:
            self.return_push += toward
            if self.return_push >= RETURN_PUSH:
                self.leave()
        elif not at or toward < 0:
            self.return_push = 0.0

    def leave(self):
        edge, back = self.edge, OPPOSITE[self.edge]
        w, h = self.bridge.phone.size()
        frac = (self.vy / h) if back in ("left", "right") else (self.vx / w)
        self.bridge.switch_to(None)  # -> PC; on_leave() clears the state
        edges = self.edges()
        if not edges or not edges[edge]:
            return
        x, y = point_on_edge(edges[edge], edge, frac, RETURN_INSET_PX)
        self.comp.warp(x, y)
        log(f"pointer returned to the PC at its {edge} edge")

    def on_leave(self):
        self.edge = None
        self.push.clear()
        self.sync_cursor()  # before leave() warps the pointer back

    def sync_cursor(self):
        """The PC pointer is hidden while a device has control (if HIDE_CURSOR), shown otherwise."""
        self.comp.hide_cursor(self.bridge.on_phone and config.HIDE_CURSOR)
