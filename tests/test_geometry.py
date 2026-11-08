# SPDX-License-Identifier: GPL-3.0-or-later
"""The Layout page's arrangement maths (no window needed): python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.app.geometry import (  # noqa: E402
    GAP, SH, SW, SX, SY, TILE, crossings, edge_segments, indicator_rects, screen_edges, screen_layout, snap, spread,
)


class Snap(unittest.TestCase):
    def test_sides(self):
        edge, x, _y = snap("phone", SX - 50, SY + SH / 2)
        self.assertEqual(edge, "left")
        self.assertEqual(x, SX - GAP - TILE["phone"][0])
        edge, x, _y = snap("tablet", SX + SW + 50, SY + SH / 2)
        self.assertEqual((edge, x), ("right", SX + SW + GAP))
        self.assertEqual(snap("phone", SX + SW / 2, SY - 40)[0], "top")

    def test_corner(self):
        edge, x, y = snap("phone", SX + SW + 30, SY + SH + 30)
        self.assertEqual(edge, "bottom-right")
        self.assertGreater(x, SX + SW)
        self.assertGreater(y, SY + SH)


class Crossings(unittest.TestCase):
    def tile(self, form, edge):
        _edge, x, y = snap(form, SX - 50 if edge == "left" else SX + SW + 50, SY + SH / 2)
        return form, {"edge": edge, "x": x, "y": y}

    def test_side_span_is_the_tile_along_the_edge(self):
        out = crossings({"a": self.tile("phone", "left")}, corner_dead=False)
        _rects, (lo, hi) = out["a"]
        self.assertAlmostEqual(hi - lo, TILE["phone"][1] / SH)

    def test_corner_dead_zones_shrink_a_full_height_span(self):
        form, cfg = self.tile("phone", "left")
        cfg["y"] = SY - 20  # taller than the screen edge it sits on
        _rects, (lo, hi) = crossings({"a": (form, cfg)}, corner_dead=True)["a"]
        self.assertGreater(lo, 0)
        self.assertLess(hi, 1)

    def test_corner_device_has_no_span(self):
        form, cfg = "phone", {"edge": "top-left", "x": 0, "y": 0}
        _rects, span = crossings({"a": (form, cfg)}, corner_dead=False)["a"]
        self.assertIsNone(span)
        self.assertEqual([seg[0] for seg in edge_segments("top-left", None)], ["left", "top"])


class Spread(unittest.TestCase):
    def test_row_is_centred_on_the_edge(self):
        tiles = [("phone", {"y": 0}), ("tablet", {"y": 200})]
        spread(tiles, "left")
        top = tiles[0][1]["y"]
        bottom = tiles[1][1]["y"] + TILE["tablet"][1]
        self.assertAlmostEqual((top + bottom) / 2, SY + SH / 2)
        self.assertEqual(tiles[1][1]["y"] - (top + TILE["phone"][1]), GAP)


if __name__ == "__main__":
    unittest.main()


class Monitors(unittest.TestCase):
    """Several monitors on the canvas: the laptop below a wide monitor, offset to the left."""
    RECTS = [(0, 0, 1600, 900), (-791, -1440, 2649, 0)]

    def test_monitors_fill_the_screen_area(self):
        laptop, wide = screen_layout(self.RECTS)
        self.assertEqual(wide[:3], (SX, SY, SX + SW))
        self.assertAlmostEqual(laptop[3], SY + SH)
        self.assertAlmostEqual(laptop[1], wide[3])  # the laptop sits right under the wide monitor
        self.assertAlmostEqual(laptop[0], SX + 791 / 3440 * SW)

    def test_one_monitor_draws_as_before(self):
        form, cfg = "phone", {"edge": "left", "x": 0, "y": SY + 0.2 * SH}
        rects, span = crossings({"a": (form, cfg)}, corner_dead=False)["a"]
        x, y, w, h = indicator_rects("left", span, screen_edges([(0, 0, 1920, 1080)]))[0]
        self.assertEqual((x, w), (rects[0][0], rects[0][2]))
        self.assertAlmostEqual(y, rects[0][1])

    def test_a_span_on_the_left_reaches_both_monitors(self):
        laptop, wide = screen_layout(self.RECTS)
        bars = indicator_rects("left", (0.37, 1.0), screen_edges(self.RECTS))
        self.assertEqual([round(x + 1) for x, *_ in bars], [round(wide[0]), round(laptop[0])])
        self.assertAlmostEqual(bars[-1][1] + bars[-1][3], SY + SH)
