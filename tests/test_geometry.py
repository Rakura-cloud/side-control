# SPDX-License-Identifier: GPL-3.0-or-later
"""The Layout page's arrangement maths (no window needed): python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.app.geometry import GAP, MAX_H, SH, SW, SX, SY, TILE, Screen, edge_segments  # noqa: E402

ONE = Screen()
# the laptop (1920x1080 at scale 1.2) under a wide monitor, offset to the left
LAPTOP, WIDE = (0, 0, 1600, 900), (-791, -1440, 2649, 0)


def placed(screen, form, cx, cy):
    edge, x, y = screen.snap(form, cx, cy)
    return form, {"edge": edge, "x": x, "y": y}


class Snap(unittest.TestCase):
    def test_sides(self):
        edge, x, _y = ONE.snap("phone", SX - 50, SY + SH / 2)
        self.assertEqual(edge, "left")
        self.assertEqual(x, SX - GAP - TILE["phone"][0])
        edge, x, _y = ONE.snap("tablet", SX + SW + 50, SY + SH / 2)
        self.assertEqual((edge, x), ("right", SX + SW + GAP))
        self.assertEqual(ONE.snap("phone", SX + SW / 2, SY - 40)[0], "top")

    def test_corner(self):
        edge, x, y = ONE.snap("phone", SX + SW + 30, SY + SH + 30)
        self.assertEqual(edge, "bottom-right")
        self.assertGreater(x, SX + SW)
        self.assertGreater(y, SY + SH)


class Crossings(unittest.TestCase):
    def tile(self, form, edge):
        return placed(ONE, form, SX - 50 if edge == "left" else SX + SW + 50, SY + SH / 2)

    def test_side_span_is_the_tile_along_the_edge(self):
        lo, hi = ONE.crossings({"a": self.tile("phone", "left")}, corner_dead=False)["a"]
        self.assertAlmostEqual(hi - lo, TILE["phone"][1] / SH)

    def test_corner_dead_zones_shrink_a_full_height_span(self):
        form, cfg = self.tile("phone", "left")
        cfg["y"] = SY - 20  # taller than the screen edge it sits on
        lo, hi = ONE.crossings({"a": (form, cfg)}, corner_dead=True)["a"]
        self.assertGreater(lo, 0)
        self.assertLess(hi, 1)

    def test_corner_device_has_no_span(self):
        form, cfg = "phone", {"edge": "top-left", "x": 0, "y": 0}
        self.assertIsNone(ONE.crossings({"a": (form, cfg)}, corner_dead=False)["a"])
        self.assertEqual([seg[0] for seg in edge_segments("top-left", None)], ["left", "top"])

    def test_one_monitor_line_is_the_tile_along_the_edge(self):
        form, cfg = self.tile("phone", "left")
        span = ONE.crossings({"a": (form, cfg)}, corner_dead=False)["a"]
        (x, y, w, h), = ONE.indicator_rects("left", span)
        self.assertEqual((x, w), (SX - 1, 4))
        self.assertAlmostEqual(y, cfg["y"])
        self.assertAlmostEqual(h, TILE["phone"][1])


class Spread(unittest.TestCase):
    def test_row_is_centred_on_the_edge(self):
        x = SX - GAP - TILE["tablet"][0]
        tiles = [("phone", {"edge": "left", "x": SX - GAP - TILE["phone"][0], "y": SY}),
                 ("tablet", {"edge": "left", "x": x, "y": SY + SH - 70})]
        ONE.spread(tiles, "left")
        top = tiles[0][1]["y"]
        bottom = tiles[1][1]["y"] + TILE["tablet"][1]
        self.assertAlmostEqual((top + bottom) / 2, SY + SH / 2)
        self.assertEqual(tiles[1][1]["y"] - (top + TILE["phone"][1]), GAP)


class Monitors(unittest.TestCase):
    """Several monitors: drawn at one scale, tiles snap to their outer edges."""
    screen = Screen([LAPTOP, WIDE])

    def test_one_scale_keeps_each_monitors_shape(self):
        for (l, t, r, b), (cl, ct, cr, cb) in zip([LAPTOP, WIDE], self.screen.monitors):  # noqa: E741
            self.assertAlmostEqual((cr - cl) / (cb - ct), (r - l) / (b - t))
        laptop, wide = self.screen.monitors
        self.assertAlmostEqual(laptop[1], wide[3])  # the laptop right under the wide monitor
        self.assertAlmostEqual(laptop[3] - wide[1], MAX_H)
        self.assertAlmostEqual((wide[0] + wide[2]) / 2, SX + SW / 2)

    def test_two_monitors_side_by_side(self):
        screen = Screen([(0, 0, 1920, 1080), (1920, 0, 3840, 1080)])
        (l, t, r, b), _ = screen.monitors  # noqa: E741
        self.assertAlmostEqual((r - l) / (b - t), 16 / 9)

    def test_snaps_to_the_laptops_own_left_edge(self):
        laptop, wide = self.screen.monitors
        form, cfg = placed(self.screen, "phone", laptop[0] - 30, (laptop[1] + laptop[3]) / 2)
        self.assertEqual(cfg["edge"], "left")
        self.assertAlmostEqual(cfg["x"] + TILE["phone"][0] + GAP, laptop[0])
        self.assertTrue(self.screen.clear(cfg["x"], cfg["y"], *TILE[form]))  # not over the wide monitor
        lo, hi = self.screen.crossings({"a": (form, cfg)}, corner_dead=False)["a"]
        self.assertGreater(lo, 1440 / 2340 - 0.001)  # the laptop's part of the left side
        bars = self.screen.indicator_rects("left", (lo, hi))
        self.assertTrue(all(abs(x + 1 - laptop[0]) < 0.01 for x, *_ in bars))

    def test_snaps_to_the_wide_monitors_left_edge(self):
        laptop, wide = self.screen.monitors
        form, cfg = placed(self.screen, "phone", wide[0] - 30, (wide[1] + wide[3]) / 2)
        self.assertAlmostEqual(cfg["x"] + TILE["phone"][0] + GAP, wide[0])
        lo, hi = self.screen.crossings({"a": (form, cfg)}, corner_dead=False)["a"]
        self.assertLess(hi, 1440 / 2340 + 0.001)

    def test_a_new_device_on_the_bottom_sits_under_the_laptop(self):
        laptop, _wide = self.screen.monitors
        edge, x, y = self.screen.free_spot("tablet", "bottom", [])
        self.assertEqual(edge, "bottom")
        self.assertAlmostEqual(y, laptop[3] + GAP)

    def test_settle_resnaps_a_tile_left_off_the_edges(self):
        form, cfg = placed(ONE, "phone", SX - 50, SY + SH / 2)  # placed with one monitor
        self.screen.settle(form, cfg)
        self.assertTrue(self.screen.attached(form, cfg))
