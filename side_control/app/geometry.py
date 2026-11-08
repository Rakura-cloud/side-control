# SPDX-License-Identifier: GPL-3.0-or-later
"""The arrangement canvas: where tiles snap, and where the pointer crosses to each device.
Ported from the design; no GTK, so it can be tested on its own."""

import math

from ..monitors import length, outer_edges, pieces

OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

# Arrangement canvas, in canvas pixels: this computer's screen in the middle, devices around it
CANVAS_W, CANVAS_H = 640, 340
SX, SY, SW, SH = 210, 102, 220, 136  # one monitor: the design's screen
TILE = {"phone": (46, 86), "tablet": (100, 70)}
GAP = 6
# Several monitors: a taller canvas, so they are drawn big enough next to the device tiles; they fit
# in it at one scale with room for a tablet beside and a phone above and below
MULTI_H = 440
MAX_W = CANVAS_W - 2 * (TILE["tablet"][0] + GAP + 8)
MAX_H = MULTI_H - 2 * (TILE["phone"][1] + GAP + 8)
CORNER_FRAC = 0.3  # same as the backend's: a corner zone covers this fraction of each edge
DEAD_PX = 6        # "Corner Dead Zones": side zones stop this far short of the screen corners
BASE_SCREEN = 400 * 860  # iPhone screen in mouse counts: "Match Pointer Speed" scales others to it
VERTICAL = ("left", "right")


def edge_segments(zone, span):
    """[(screen edge, lo, hi)] fractions along the edges where the pointer crosses for one device."""
    c = CORNER_FRAC
    if zone in OPPOSITE:
        return [(zone, *(span or (0.0, 1.0)))]
    vertical, horizontal = zone.split("-")
    along_side = (0.0, c) if vertical == "top" else (1 - c, 1.0)
    along_top = (0.0, c) if horizontal == "left" else (1 - c, 1.0)
    return [(horizontal, *along_side), (vertical, *along_top)]


class Screen:
    """This computer's screen on the canvas. One monitor is the design's screen; several are drawn at
    one scale, where they are in the layout. Tiles snap to the monitors' outer edges, and a span is a
    fraction along a side's outer edges laid end to end, as the backend and the edge indicator count it.

    rects: the monitors [(left, top, right, bottom)] in layout pixels."""

    def __init__(self, rects=None):
        rects = list(rects or [])
        self.height = CANVAS_H if len(rects) < 2 else MULTI_H  # of the canvas
        if len(rects) < 2:
            rects = [(SX, SY, SX + SW, SY + SH)]
            to_x = to_y = lambda v: v  # noqa: E731
        else:
            left, top = min(r[0] for r in rects), min(r[1] for r in rects)
            w, h = max(r[2] for r in rects) - left, max(r[3] for r in rects) - top
            k = min(MAX_W / max(1, w), MAX_H / max(1, h))
            x0, y0 = CANVAS_W / 2 - w * k / 2, MULTI_H / 2 - h * k / 2
            to_x, to_y = (lambda x: x0 + (x - left) * k), (lambda y: y0 + (y - top) * k)
        self.monitors = [(to_x(l), to_y(t), to_x(r), to_y(b)) for l, t, r, b in rects]  # noqa: E741
        self.edges = {side: [(to_x(f), to_y(a0), to_y(a1), i) if side in VERTICAL
                              else (to_y(f), to_x(a0), to_x(a1), i) for f, a0, a1, i in segs]
                      for side, segs in outer_edges(rects).items()}
        self.left, self.top = min(m[0] for m in self.monitors), min(m[1] for m in self.monitors)
        self.right, self.bottom = max(m[2] for m in self.monitors), max(m[3] for m in self.monitors)

    # --- placing tiles ---

    def clear(self, x, y, w, h):
        """Whether a tile there stays off every monitor."""
        return all(x + w <= l + 0.5 or x >= r - 0.5 or y + h <= t + 0.5 or y >= b - 0.5
                   for l, t, r, b in self.monitors)  # noqa: E741

    def beside(self, form, side, seg, along):
        """(x, y) of a tile next to an outer edge segment, as near as it fits to `along` (its start)."""
        w, h = TILE[form]
        f, a0, a1, _ = seg
        size = h if side in VERTICAL else w
        place = lambda v: ((f - GAP - w if side == "left" else f + GAP, v) if side in VERTICAL  # noqa: E731
                           else (v, f - GAP - h if side == "top" else f + GAP))
        options = [a0, (a0 + a1 - size) / 2, a1 - size]  # start, middle, end of the edge
        fits = [v for v in options if self.clear(*place(v), w, h)] or options
        return place(min(fits, key=lambda v: abs(v - along)))

    def snap(self, form, cx, cy):
        """Where a tile dropped with its centre at (cx, cy) settles: (edge, x, y)."""
        w, h = TILE[form]
        out_x, out_y = cx < self.left or cx > self.right, cy < self.top or cy > self.bottom
        if out_x and out_y:
            right, bottom = cx > self.right, cy > self.bottom
            return (("bottom" if bottom else "top") + "-" + ("right" if right else "left"),
                    self.right + 2 if right else self.left - 2 - w,
                    self.bottom + 2 if bottom else self.top - 2 - h)

        # the outer edge that moves the tile least from where it was dropped
        spots = [(side, *self.beside(form, side, seg, cy - h / 2 if side in VERTICAL else cx - w / 2))
                 for side in OPPOSITE for seg in self.edges[side]]
        return min(spots, key=lambda s: math.hypot(s[1] + w / 2 - cx, s[2] + h / 2 - cy))

    def attached(self, form, cfg):
        """The outer edge segments a side tile sits against: [(index in the side's list, segment)]."""
        edge, x, y = cfg["edge"], cfg["x"], cfg["y"]
        if edge not in OPPOSITE or x is None or y is None:
            return []
        w, h = TILE[form]
        f = {"left": x + w + GAP, "right": x - GAP, "top": y + h + GAP, "bottom": y - GAP}[edge]
        t, b = (y, y + h) if edge in VERTICAL else (x, x + w)
        return [(i, seg) for i, seg in enumerate(self.edges[edge])
                if abs(seg[0] - f) < 0.5 and seg[1] < b and t < seg[2]]

    def settle(self, form, cfg):
        """Snap a tile again if it no longer sits where it could (the monitors changed)."""
        if cfg["edge"] is None or cfg["x"] is None or cfg["y"] is None:
            return
        w, h = TILE[form]
        if "-" in cfg["edge"] or not self.attached(form, cfg):
            cfg["edge"], cfg["x"], cfg["y"] = self.snap(form, cfg["x"] + w / 2, cfg["y"] + h / 2)

    def free_spot(self, form, edge, others):
        """Default place for a new device on `edge`, clear of the tiles in `others` [(form, edge, x, y)]."""
        if "-" in edge:
            vertical, horizontal = edge.split("-")
            return self.snap(form, 0 if horizontal == "left" else CANVAS_W, 0 if vertical == "top" else self.height)
        cx, cy = (self.left + self.right) / 2, (self.top + self.bottom) / 2
        cx, cy = {"left": (self.left - 40, cy), "right": (self.right + 40, cy),
                  "top": (cx, self.top - 40), "bottom": (cx, self.bottom + 40)}[edge]
        edge, x, y = self.snap(form, cx, cy)
        on = self.attached(form, {"edge": edge, "x": x, "y": y})
        if not on:
            return edge, x, y
        _f, lo, hi, _m = on[0][1]
        vert = edge in VERTICAL
        size = TILE[form][1] if vert else TILE[form][0]
        segs = [(oy, oy + TILE[f][1]) if vert else (ox, ox + TILE[f][0]) for f, e, ox, oy in others if e == edge]
        free = lambda v: (all(v + size + GAP <= a or v >= b + GAP for a, b in segs)  # noqa: E731
                          and min(v + size, hi) - max(v, lo) >= 28)
        v = y if vert else x
        if not free(v):
            options = [c for a, b in segs for c in (a - size - GAP, b + GAP) if free(c)]
            if options:
                v = min(options, key=lambda c: abs(c - v))
        return (edge, x, v) if vert else (edge, v, y)

    def spread(self, tiles, edge):
        """Centre tiles against the same edge as one row, in their current order. tiles: [(form, cfg)]"""
        vert = edge in VERTICAL
        key, i = ("y", 1) if vert else ("x", 0)
        row = sorted(tiles, key=lambda t: t[1][key] + TILE[t[0]][i] / 2)
        segs = [seg for form, cfg in row for _j, seg in self.attached(form, cfg)]
        lo, hi = (min(s[1] for s in segs), max(s[2] for s in segs)) if segs else \
                 ((self.top, self.bottom) if vert else (self.left, self.right))
        total = sum(TILE[f][i] for f, _ in row) + GAP * (len(row) - 1)
        v = (lo + hi) / 2 - total / 2
        for form, cfg in row:
            cfg[key] = v
            v += TILE[form][i] + GAP

    # --- where the pointer crosses ---

    def crossings(self, tiles, corner_dead):
        """{address: (lo, hi) span along a side's outer edges, or None for a corner device}.
        tiles: {address: (form, cfg)} of active devices."""
        corners = {cfg["edge"] for _, cfg in tiles.values() if "-" in cfg["edge"]}
        out = {}
        for address, (form, cfg) in tiles.items():
            edge = cfg["edge"]
            if "-" in edge:
                out[address] = None
                continue
            segs = self.edges[edge]
            total = max(1e-6, length(segs))
            w, h = TILE[form]
            t, b = (cfg["y"], cfg["y"] + h) if edge in VERTICAL else (cfg["x"], cfg["x"] + w)
            ends, done = [], 0.0
            on = {i for i, _seg in self.attached(form, cfg)}
            for i, (_f, a0, a1, _m) in enumerate(segs):
                if i in on and min(b, a1) > max(t, a0):
                    ends += [done + max(t, a0) - a0, done + min(b, a1) - a0]
                done += a1 - a0
            if not ends:
                out[address] = (0.0, 0.0)
                continue
            first, last = (f"top-{edge}", f"bottom-{edge}") if edge in VERTICAL else \
                          (f"{edge}-left", f"{edge}-right")
            inset = DEAD_PX if corner_dead else 0
            lo = max(min(ends), CORNER_FRAC * total + GAP if first in corners else inset)
            hi = min(max(ends), total - (CORNER_FRAC * total + GAP if last in corners else inset))
            out[address] = (lo / total, max(lo, hi) / total)
        return out

    def indicator_rects(self, zone, span):
        """[(x, y, w, h)] lines on the canvas where the pointer crosses to a device: on the outer edges,
        as on the real screens."""
        out = []
        for side, lo, hi in edge_segments(zone, span):
            for f, a0, a1, _ in pieces(self.edges[side], lo, hi):
                out.append({"left": (f - 1, a0, 4, a1 - a0), "right": (f - 3, a0, 4, a1 - a0),
                            "top": (a0, f - 1, a1 - a0, 4), "bottom": (a0, f - 3, a1 - a0, 4)}[side])
        return out
