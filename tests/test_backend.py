# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend logic that needs no Bluetooth, input devices or root: python -m unittest discover tests"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.backend import config  # noqa: E402
from side_control.backend.bridge import Bridge  # noqa: E402
from side_control.backend.devices import SCREEN_POINTS, Phone  # noqa: E402
from side_control.backend.handoff import Handoff, handoff_exits  # noqa: E402
from side_control.backend.hid import REPORT_MAP  # noqa: E402
from side_control.backend.inputs import Touchpad  # noqa: E402


class FakePhone:
    """Stands in for Phone: records the mouse reports (buttons, x, y) instead of sending them."""
    connected, speed, alias = True, 1.0, "Test iPhone"
    zone, span = "right", None

    def __init__(self, size=(393, 852)):
        self._size = size
        self.reports = []

    def send(self, report_id, payload):
        if report_id == 2:
            signed = [v - 256 if v > 127 else v for v in payload[1:3]]
            self.reports.append((payload[0], *signed))

    def backlogged(self):
        return False

    def size(self):
        return self._size


class FakeCompositor:
    available = True

    def __init__(self):
        self.warped = None

    def layout(self):
        return (0, 0, 1920, 1080)

    def warp(self, x, y):
        self.warped = (x, y)


def make_bridge(phone):
    bridge = Bridge.__new__(Bridge)  # no input devices, fleet or proximity
    bridge.touchpads, bridge.phone, bridge.on_phone, bridge.locked = {}, phone, True, False
    bridge.reset_reports()
    bridge.handoff = Handoff(bridge, None, None, FakeCompositor())

    def switch_to(device):
        bridge.on_phone = device is not None
        bridge.reset_reports()
        bridge.handoff.on_leave()
    bridge.switch_to = switch_to
    return bridge


def drain(bridge):
    while bridge.mouse_pending():
        bridge.flush_mouse()
        time.sleep(0.001)


def make_touchpad():
    tp = Touchpad.__new__(Touchpad)  # positions in mm
    tp.resx = tp.resy = 1
    tp.contacts = {}
    tp.reset()
    return tp


class ReportMap(unittest.TestCase):
    def test_report_sizes(self):
        """The descriptor parses into the report sizes the code sends: keyboard 8 bytes, mouse 5."""
        data, i, size, count, rid, bits = REPORT_MAP, 0, 0, 0, 0, {}
        while i < len(data):
            prefix = data[i]
            n = (0, 1, 2, 4)[prefix & 3]
            value = int.from_bytes(data[i + 1:i + 1 + n], "little")
            i += 1 + n
            tag = prefix & 0xFC
            if tag == 0x74:
                size = value
            elif tag == 0x94:
                count = value
            elif tag == 0x84:
                rid = value
            elif tag == 0x80:
                bits[rid] = bits.get(rid, 0) + size * count
        self.assertEqual(bits, {1: 64, 2: 40})


class ScreenSize(unittest.TestCase):
    def phone(self, model, vendor=0x004C):
        phone = Phone(0, "AA:BB:CC:DD:EE:FF", "device")
        phone.vendor, phone.model = vendor, model
        return phone

    def test_known_models(self):
        self.assertEqual(self.phone("iPhone16,1").size(), (393, 852))  # upright
        self.assertEqual(self.phone("iPad13,16").size(), (1180, 820))  # on its side

    def test_unknown_model_uses_default(self):
        self.assertEqual(self.phone(None).size(), config.DEFAULT_SCREEN["iPhone"])

    def test_every_model_has_one_size(self):
        models = [m for ms in SCREEN_POINTS.values() for m in ms.split()]
        self.assertEqual(len(models), len(set(models)))


class HandoffTest(unittest.TestCase):
    def test_enters_at_matching_height_and_returns_there(self):
        phone = FakePhone(size=(1180, 820))
        bridge = make_bridge(phone)
        h = bridge.handoff
        for frac in (0.3, 0.8):
            phone.reports.clear()
            bridge.on_phone = True
            h.edge, h.entry_frac = "right", frac
            h.on_enter()
            drain(bridge)
            self.assertAlmostEqual(h.vy / 820, frac, places=2)
            # the glide after the pin moves at most GLIDE_STEP per report
            self.assertLessEqual(max(abs(y) for _, _, y in phone.reports[-5:]), config.GLIDE_STEP)
        bridge.dx = -(config.RETURN_PUSH + 50.0)  # push back out through the side it came in by
        drain(bridge)
        self.assertFalse(bridge.on_phone)
        self.assertAlmostEqual(h.comp.warped[1], 0.8 * 1080, delta=2)

    def test_exit_spans(self):
        (edge, inside), = handoff_exits("left", (0.2, 0.6))
        self.assertEqual(edge, "left")
        self.assertTrue(inside(0.4))
        self.assertFalse(inside(0.7))


class SwipeFlick(unittest.TestCase):
    def swipe(self, bridge, tp, mm):
        tp.reset_gesture()
        step = 0.5 if mm > 0 else -0.5
        for i in range(int(abs(mm) / 0.5) + 1):
            tp.contacts = {0: [10 + i * step, 30], 1: [30 + i * step, 30]}
            bridge.two_finger_gesture(tp, 0, 0)
            bridge.flush_mouse()
            time.sleep(0.002)
        bridge.end_gesture(tp)
        drain(bridge)

    def test_flick_presses_drags_and_returns(self):
        phone = FakePhone()
        bridge = make_bridge(phone)
        h = bridge.handoff
        h.edge, h.vx, h.vy = "right", 200.0, 400.0
        tp = make_touchpad()
        for mm in (-20, 20):
            phone.reports.clear()
            start = h.vx
            self.swipe(bridge, tp, mm)
            held = [x for buttons, x, _ in phone.reports if buttons & 1]
            self.assertTrue(held, "the flick holds button 1")
            self.assertEqual(sum(held) > 0, mm > 0)  # dragged the way the fingers went
            self.assertEqual(phone.reports[-1][0] & 1, 0)  # and let go
            # the pointer ends where the model says: the drag and its way back cancel out
            moved = sum(x for _, x, _ in phone.reports)
            self.assertEqual(moved, round(h.vx - start))

    def test_flick_makes_room_near_the_edge(self):
        phone = FakePhone()
        bridge = make_bridge(phone)
        h = bridge.handoff
        h.edge, h.vx, h.vy = "right", 380.0, 400.0
        self.swipe(bridge, make_touchpad(), 20)  # towards the right edge, 13 counts away
        need = config.FLICK_ROOM * config.FLICK_STEP * config.FLICK_REPORTS
        self.assertGreaterEqual(393 - h.vx, need - 1)


if __name__ == "__main__":
    unittest.main()
