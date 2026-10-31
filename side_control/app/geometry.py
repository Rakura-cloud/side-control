# SPDX-License-Identifier: GPL-3.0-or-later
"""The arrangement canvas: where tiles snap, and where the pointer crosses to each device.
Ported from the design; no GTK, so it can be tested on its own."""


OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}

# Arrangement canvas, in canvas pixels: this computer's screen in the middle, devices around it
CANVAS_W, CANVAS_H = 640, 340
SX, SY, SW, SH = 210, 102, 220, 136
TILE = {"phone": (46, 86), "tablet": (100, 70)}
GAP = 6
CORNER_FRAC = 0.3  # same as the backend's: a corner zone covers this fraction of each edge
DEAD_PX = 6        # "Corner Dead Zones": side zones stop this far short of the screen corners
BASE_SCREEN = 400 * 860  # iPhone screen in mouse counts: "Match Pointer Speed" scales others to it


def snap(form, cx, cy):
    """Where a tile dropped with its centre at (cx, cy) settles: (edge, x, y)."""
    w, h = TILE[form]
    out_x, out_y = cx < SX or cx > SX + SW, cy < SY or cy > SY + SH
    if out_x and out_y:
        right, bottom = cx > SX + SW, cy > SY + SH
        return (("bottom" if bottom else "top") + "-" + ("right" if right else "left"),
                SX + SW + 2 if right else SX - 2 - w, SY + SH + 2 if bottom else SY - 2 - h)
    nearest = lambda v, opts: min(opts, key=lambda o: abs(o - v))  # noqa: E731
    dx, dy = (cx - (SX + SW / 2)) / SW, (cy - (SY + SH / 2)) / SH
    if abs(dx) >= abs(dy):
        edge = "left" if dx < 0 else "right"
        return (edge, SX - GAP - w if edge == "left" else SX + SW + GAP,
                nearest(cy - h / 2, [SY, SY + (SH - h) / 2, SY + SH - h]))
    edge = "top" if dy < 0 else "bottom"
    return (edge, nearest(cx - w / 2, [SX, SX + (SW - w) / 2, SX + SW - w]),
            SY - GAP - h if edge == "top" else SY + SH + GAP)


def free_spot(form, edge, others):
    """Default place for a new device on `edge`, clear of the tiles in `others` [(form, edge, x, y)]."""
    if "-" in edge:
        vertical, horizontal = edge.split("-")
        return snap(form, 0 if horizontal == "left" else CANVAS_W, 0 if vertical == "top" else CANVAS_H)
    centre = {"left": (0, CANVAS_H / 2), "right": (CANVAS_W, CANVAS_H / 2),
              "top": (CANVAS_W / 2, 0), "bottom": (CANVAS_W / 2, CANVAS_H)}[edge]
    edge, x, y = snap(form, *centre)
    vert = edge in ("left", "right")
    length = TILE[form][1] if vert else TILE[form][0]
    lo, hi = (SY, SY + SH) if vert else (SX, SX + SW)
    segs = [(oy, oy + TILE[f][1]) if vert else (ox, ox + TILE[f][0]) for f, e, ox, oy in others if e == edge]
    free = lambda v: (all(v + length + GAP <= a or v >= b + GAP for a, b in segs)  # noqa: E731
                      and min(v + length, hi) - max(v, lo) >= 28)
    v = y if vert else x
    if not free(v):
        options = [c for a, b in segs for c in (a - length - GAP, b + GAP) if free(c)]
        if options:
            v = min(options, key=lambda c: abs(c - v))
    return (edge, x, v) if vert else (edge, v, y)


def spread(tiles, edge):
    """Centre every tile on a side edge as one row, in their current order. tiles: [(form, cfg)]"""
    vert = edge in ("left", "right")
    key, i = ("y", 1) if vert else ("x", 0)
    row = sorted(tiles, key=lambda t: t[1][key] + TILE[t[0]][i] / 2)
    total = sum(TILE[f][i] for f, _ in row) + GAP * (len(row) - 1)
    v = (SY + SH / 2 if vert else SX + SW / 2) - total / 2
    for form, cfg in row:
        cfg[key] = v
        v += TILE[form][i] + GAP


def crossings(tiles, corner_dead):
    """Where the pointer crosses to each device. tiles: {address: (form, cfg)} of active devices.
    Returns {address: ([(x, y, w, h)] indicator rectangles, (lo, hi) span along a side edge or None)}"""
    corners = {cfg["edge"] for _, cfg in tiles.values() if "-" in cfg["edge"]}
    inset = DEAD_PX if corner_dead else 0
    out = {}
    for address, (form, cfg) in tiles.items():
        edge, x, y = cfg["edge"], cfg["x"], cfg["y"]
        w, h = TILE[form]
        if "-" in edge:
            vertical, horizontal = edge.split("-")
            lv, lh = CORNER_FRAC * SH, CORNER_FRAC * SW
            out[address] = ([(SX + SW - 3 if horizontal == "right" else SX - 1,
                              SY + SH - lv if vertical == "bottom" else SY, 4, lv),
                             (SX + SW - lh if horizontal == "right" else SX,
                              SY + SH - 3 if vertical == "bottom" else SY - 1, lh, 4)], None)
        elif edge in ("left", "right"):
            clear = CORNER_FRAC * SH + GAP
            t = max(y, SY + (clear if f"top-{edge}" in corners else inset))
            b = min(y + h, SY + SH - (clear if f"bottom-{edge}" in corners else inset))
            out[address] = ([(SX - 1 if edge == "left" else SX + SW - 3, t, 4, max(0, b - t))],
                            ((t - SY) / SH, (max(t, b) - SY) / SH))
        else:
            clear = CORNER_FRAC * SW + GAP
            l = max(x, SX + (clear if f"{edge}-left" in corners else inset))  # noqa: E741
            r = min(x + w, SX + SW - (clear if f"{edge}-right" in corners else inset))
            out[address] = ([(l, SY - 1 if edge == "top" else SY + SH - 3, max(0, r - l), 4)],
                            ((l - SX) / SW, (max(l, r) - SX) / SW))
    return out


def edge_segments(zone, span):
    """[(screen edge, lo, hi)] fractions along the edges where the pointer crosses for one device."""
    c = CORNER_FRAC
    if zone in OPPOSITE:
        return [(zone, *(span or (0.0, 1.0)))]
    vertical, horizontal = zone.split("-")
    along_side = (0.0, c) if vertical == "top" else (1 - c, 1.0)
    along_top = (0.0, c) if horizontal == "left" else (1 - c, 1.0)
    return [(horizontal, *along_side), (vertical, *along_top)]
