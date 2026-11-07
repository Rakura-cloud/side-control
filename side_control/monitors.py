# SPDX-License-Identifier: GPL-3.0-or-later
"""Monitor layout maths shared by the backend (edge hand-off) and the app (the edge indicator): which
parts of the monitors' edges are outer edges, and positions along them. No compositor, no GTK.

Rectangles are (left, top, right, bottom) in layout pixels, right and bottom exclusive. A side's outer
edge is every stretch of a monitor's edge on that side that no other monitor touches; laid end to end
in order along the side, they make one line, and fractions (a device's span, where the pointer left)
are measured along that line, so with monitors of different sizes or offsets no part of it lies off
every monitor.
"""

SIDES = ("left", "right", "top", "bottom")
FACING = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
TOUCH_PX = 1  # monitor edges this close count as touching (scaled sizes are rounded)


def edge_of(rect, side):
    """(coordinate of that edge, start, end along it)"""
    left, top, right, bottom = rect
    return {"left": (left, top, bottom), "right": (right, top, bottom),
            "top": (top, left, right), "bottom": (bottom, left, right)}[side]


def outer_edges(rects):
    """{side: [(coordinate, start, end, monitor index)]} in order along the side."""
    rects = list(rects)
    out = {}
    for side in SIDES:
        segs = []
        for i, rect in enumerate(rects):
            fixed, a0, a1 = edge_of(rect, side)
            if rect in rects[:i]:  # a mirror of an earlier monitor
                continue
            touching = sorted((n0, n1) for other in rects if other != rect
                              for f, n0, n1 in [edge_of(other, FACING[side])]
                              if abs(f - fixed) <= TOUCH_PX and n0 < a1 and n1 > a0)
            pos = a0
            for n0, n1 in touching:
                if n0 > pos:
                    segs.append((fixed, pos, n0, i))
                pos = max(pos, n1)
            if pos < a1:
                segs.append((fixed, pos, a1, i))
        out[side] = sorted(segs, key=lambda s: (s[1], s[0]))
    return out


def length(segs):
    return sum(a1 - a0 for _, a0, a1, _ in segs)


def along_edge(segs, side, x, y, margin):
    """Fraction (0..1) along the side if (x, y) is on a monitor within margin of its outer edge there,
    else None."""
    across, along = (x, y) if side in ("left", "right") else (y, x)
    inward = 1 if side in ("left", "top") else -1
    total, done = length(segs), 0
    for fixed, a0, a1, _ in segs:
        last = fixed if inward > 0 else fixed - 1  # the outermost row of pixels on the monitor
        if a0 <= along < a1 and 0 <= inward * (across - last) <= margin:
            return (done + along - a0) / max(1, total)
        done += a1 - a0
    return None


def point_on_edge(segs, side, frac, inset):
    """(x, y) on a monitor, inset pixels in from the side's outer edge, frac of the way along it."""
    want = max(0.0, min(1.0, frac)) * length(segs)
    for fixed, a0, a1, _ in segs:
        if want <= a1 - a0:
            break
        want -= a1 - a0
    along = max(a0, min(a1 - 1, a0 + want))
    across = fixed + inset if side in ("left", "top") else fixed - 1 - inset
    return (across, along) if side in ("left", "right") else (along, across)


def pieces(segs, lo, hi):
    """[(coordinate, start, end, monitor index)]: the stretch from lo to hi (fractions) of the side."""
    total, done, out = length(segs), 0, []
    g0, g1 = lo * total, hi * total
    for fixed, a0, a1, i in segs:
        s0, s1 = max(g0, done), min(g1, done + a1 - a0)
        if s1 > s0:
            out.append((fixed, a0 + s0 - done, a0 + s1 - done, i))
        done += a1 - a0
    return out
