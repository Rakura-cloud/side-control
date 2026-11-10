# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend logic that needs no Bluetooth, input devices or root: python -m unittest discover tests"""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from side_control.backend import config  # noqa: E402
from side_control.backend.bridge import Bridge  # noqa: E402
from side_control.backend.devices import SCREEN_POINTS, Phone  # noqa: E402
from side_control.backend.handoff import Handoff, handoff_exits  # noqa: E402
from side_control.backend.hid import REPORT_MAP  # noqa: E402
from side_control.backend import inputs  # noqa: E402
from side_control.backend.bluez import form_of  # noqa: E402
from side_control.backend.compositor import Hyprland  # noqa: E402
from side_control.monitors import along_edge, outer_edges, pieces, point_on_edge  # noqa: E402
from side_control.backend.inputs import Accel, Touchpad  # noqa: E402
from evdev import InputEvent, ecodes as e  # noqa: E402


class FakePhone:
    """Stands in for Phone: records the mouse reports (buttons, x, y) instead of sending them."""
    connected, speed, alias = True, 1.0, "Test iPhone"
    zone, span = "right", None

    def __init__(self, size=(393, 852)):
        self._size = size
        self.reports = []
        self.raw = []  # every report: (report id, bytes)

    def send(self, report_id, payload):
        self.raw.append((report_id, bytes(payload)))
        if report_id == 2:
            signed = [v - 256 if v > 127 else v for v in payload[1:3]]
            self.reports.append((payload[0], *signed))

    def backlogged(self):
        return False

    def size(self):
        return self._size


class FakeCompositor:
    available = True
    name = "a fake compositor"

    def __init__(self, rects=((0, 0, 1920, 1080),), cursor=None):
        self.rects, self.pos = list(rects), cursor
        self.warped = None
        self.hidden = False

    def layout(self):
        return self.rects

    def cursor(self):
        return self.pos

    def warp(self, x, y):
        self.warped = (x, y)
        return True

    def hide_cursor(self, hide):
        self.hidden = hide


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


class FakeMouse:
    """Stands in for an evdev InputDevice: read() hands out the events queued, or fails once unplugged."""
    name = "Test Mouse"

    def __init__(self):
        self.queue, self.gone, self.closed = [], False, False

    def push(self, *events):
        self.queue.extend(InputEvent(0, 0, *ev) for ev in events)

    def read(self):
        if self.gone:
            raise OSError(19, "No such device")
        events, self.queue = self.queue, []
        return events

    def close(self):
        self.closed = True


class FakeFleet:
    def connected(self):
        return []


def make_mouse_bridge(on_phone=True):
    phone = FakePhone()
    bridge = make_bridge(phone)
    mouse = FakeMouse()
    bridge.on_phone, bridge.fleet, bridge.devices = on_phone, FakeFleet(), {7: mouse}
    bridge.held, bridge.toggle_pending, bridge.action_pending = set(), False, None
    bridge.rel, bridge.rel_t, bridge.accels = [0, 0], 0.0, {}
    return bridge, phone, mouse


SYN = (e.EV_SYN, e.SYN_REPORT, 0)


class MouseTest(unittest.TestCase):
    def test_slow_motion_is_one_to_one_and_fast_motion_accelerates(self):
        for counts, gain in ((2, 1.0), (400, inputs.MOUSE_ACCEL_MAX)):  # in one report: 0.35, 70 counts/ms
            bridge, phone, mouse = make_mouse_bridge()
            mouse.push((e.EV_REL, e.REL_X, counts), (e.EV_REL, e.REL_Y, -counts), SYN)
            bridge.handle(7)
            drain(bridge)
            self.assertEqual(sum(x for _, x, _ in phone.reports), round(counts * gain))
            self.assertEqual(sum(y for _, _, y in phone.reports), round(-counts * gain))

    def test_acceleration_curve(self):
        with mock.patch.multiple(inputs, MOUSE_ACCEL_SLOW=0.07, MOUSE_ACCEL_MAX=2.0):  # libinput's
            self.assertAlmostEqual(Accel.gain(0.0), 0.3)  # creeping: finer than 1:1
            self.assertAlmostEqual(Accel.gain(0.07), 1.0)  # no step
            self.assertEqual(Accel.gain(inputs.MOUSE_ACCEL_THRESHOLD), 1.0)
            self.assertEqual(Accel.gain(100.0), 2.0)
            gains = [Accel.gain(v / 10) for v in range(30)]
            self.assertEqual(gains, sorted(gains))  # faster never moves less
        if config.MOUSE_ACCEL_MAX == 1.0 and config.MOUSE_ACCEL_SLOW == 0.0:  # acceleration off: 1:1
            self.assertEqual({Accel.gain(v / 10) for v in range(300)}, {1.0})

    def test_bursts_measure_the_same_speed(self):
        """Bluetooth hands over reports in bursts: speed is measured over a window, not per report."""
        steady, burst = Accel(), Accel()
        t = 100.0
        for i in range(10):
            steady.frame = [8, 0]
            sx, _ = steady.take(t + i * 0.008)
            burst.frame = [8, 0]
            bx, _ = burst.take(t + (i // 3) * 0.024)  # three at a time
        self.assertAlmostEqual(sx, bx, delta=sx * 0.2)

    def test_a_move_starts_at_full_speed(self):
        """The first reports of a move are not taken for creeping (the pointer would lag behind)."""
        accel = Accel()
        accel.frame = [4, 0]  # 4 counts in one 8 ms report: 0.5 counts per ms, 1:1 or more
        x, _ = accel.take(100.0)
        self.assertGreaterEqual(x, 4)

    def test_natural_mouse_scrolling_reverses_the_wheel(self):
        for natural, wheel in ((False, 1), (True, 0xFF)):
            with mock.patch.object(config, "MOUSE_NATURAL_SCROLL", natural):
                bridge, phone, mouse = make_mouse_bridge()
                mouse.push((e.EV_REL, e.REL_WHEEL, 1), SYN)
                bridge.handle(7)
                drain(bridge)
                self.assertEqual([r[1][3] for r in phone.raw if r[0] == 2], [wheel])

    def test_side_buttons_and_horizontal_wheel(self):
        bridge, phone, mouse = make_mouse_bridge()
        mouse.push((e.EV_KEY, e.BTN_SIDE, 1), SYN, (e.EV_KEY, e.BTN_SIDE, 0), SYN,
                   (e.EV_KEY, e.BTN_EXTRA, 1), SYN, (e.EV_REL, e.REL_HWHEEL, -2), SYN)
        bridge.handle(7)
        drain(bridge)
        buttons = [r[1][0] for r in phone.raw if r[0] == 2]
        self.assertIn(0x08, buttons)
        self.assertEqual(buttons[-1], 0x10)  # forward still held
        self.assertIn(0xFE, [r[1][4] for r in phone.raw if r[0] == 2])  # AC Pan -2

    def test_unplugged_mouse_lets_go_of_its_buttons(self):
        bridge, phone, mouse = make_mouse_bridge()
        mouse.push((e.EV_KEY, e.BTN_LEFT, 1), SYN)
        bridge.handle(7)
        mouse.gone = True
        bridge.handle(7)
        self.assertEqual(bridge.devices, {})
        self.assertTrue(mouse.closed)
        self.assertEqual(bridge.buttons, 0)
        self.assertEqual(phone.raw[-1], (2, bytes(5)))

    def test_pause_ends_a_push_against_the_edge(self):
        bridge, _, mouse = make_mouse_bridge(on_phone=False)
        bridge.handoff.push[("AA", "right")] = 3.0
        bridge.rel_t = time.monotonic() - config.MOUSE_PAUSE_S - 0.1
        mouse.push((e.EV_REL, e.REL_X, 40), SYN)
        bridge.handle(7)
        self.assertEqual(bridge.handoff.push, {})
        bridge.handoff.push[("AA", "right")] = 3.0  # still moving: the push goes on
        time.sleep(config.MOUSE_INTERVAL)
        mouse.push((e.EV_REL, e.REL_X, 40), SYN)
        bridge.handle(7)
        self.assertEqual(bridge.handoff.push, {("AA", "right"): 3.0})


class ScanInputs(unittest.TestCase):
    def test_new_and_replaced_nodes_are_opened_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "event17")
            open(path, "w").close()
            opened = []

            def input_device(p):
                dev = mock.Mock(fd=len(opened) + 100, path=p)
                dev.name = "Test Mouse"
                dev.capabilities.return_value = {e.EV_REL: [e.REL_X, e.REL_Y]}
                opened.append(dev)
                return dev

            seen = set()
            with mock.patch.object(inputs.evdev, "list_devices", return_value=[path]), \
                    mock.patch.object(inputs.evdev, "InputDevice", side_effect=input_device), \
                    mock.patch.object(inputs, "log"):
                self.assertEqual(list(inputs.scan_inputs(seen)), [100])
                self.assertEqual(inputs.scan_inputs(seen), {})  # already open
                # the mouse slept and came back: a new node at the same path (the old one kept
                # aside, so the new one cannot get its inode)
                os.rename(path, path + ".old")
                open(path, "w").close()
                self.assertEqual(list(inputs.scan_inputs(seen)), [101])
            self.assertEqual(len(seen), 1)


class PairedForms(unittest.TestCase):
    """Only phones and tablets become devices to control: anything else paired would be dialed."""

    def test_phones_tablets_and_others(self):
        iphone = {"Class": 0x7A020C, "Icon": "phone", "Modalias": "bluetooth:v004Cp761Dd1B00",
                  "Alias": "Someone's iPhone", "AddressType": "public"}
        ipad = {"Class": 0x2C0114, "Icon": "computer", "Modalias": "bluetooth:v004Cp7213d1100",
                "Alias": "iPad", "AddressType": "public"}
        le_mouse = {"Icon": "input-mouse", "Modalias": "usb:v046DpB023d0015", "Alias": "MX Master 3",
                    "AddressType": "random"}
        headphones = {"Class": 0x240404, "Icon": "audio-headset", "Alias": "Headphones",
                      "AddressType": "public"}
        self.assertEqual(form_of(iphone), "phone")
        self.assertEqual(form_of(ipad), "tablet")
        self.assertIsNone(form_of(le_mouse))
        self.assertIsNone(form_of(headphones))


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

    def test_cursor_hidden_while_on_device(self):
        bridge = make_bridge(FakePhone())
        h = bridge.handoff
        h.edge = "right"
        h.on_enter()
        self.assertTrue(h.comp.hidden)
        bridge.on_phone = False
        h.on_leave()
        self.assertFalse(h.comp.hidden)
        bridge.on_phone = True
        with mock.patch.object(config, "HIDE_CURSOR", False):
            h.on_enter()
        self.assertFalse(h.comp.hidden)

    def test_exit_spans(self):
        (edge, inside), = handoff_exits("left", (0.2, 0.6))
        self.assertEqual(edge, "left")
        self.assertTrue(inside(0.4))
        self.assertFalse(inside(0.7))


class NoCursorHandoff(unittest.TestCase):
    """A compositor that cannot tell where the PC pointer is (niri, KDE, GNOME): edge hand-off
    never starts, while switching by hand and coming back still work."""

    def test_edge_hand_off_never_starts(self):
        phone = FakePhone()
        phone.zone, phone.address, phone.require_near = "right", "AA", False
        bridge = make_bridge(phone)
        bridge.on_phone = False
        bridge.fleet = types.SimpleNamespace(connected=lambda: [phone])
        bridge.handoff = h = Handoff(bridge, bridge.fleet, None, FakeCompositor(cursor=None))
        entered = []
        h.enter = lambda p, edge, frac: entered.append((edge, frac))
        with mock.patch.object(config, "SWITCH_DELAY_S", 0):
            for _ in range(5):
                h.pc_motion(config.EDGE_PUSH_MM + 1, 0)  # pushing right, off the screen edge
        self.assertEqual(entered, [])

    def test_switching_by_hand_still_works(self):
        phone = FakePhone()
        bridge = make_bridge(phone)
        bridge.handoff = h = Handoff(bridge, None, None, FakeCompositor(cursor=None))
        bridge.on_phone = True
        h.edge, h.entry_frac = "right", 0.5  # a hotkey or the app switched to the device
        h.on_enter()
        drain(bridge)
        self.assertAlmostEqual(h.vy / phone.size()[1], 0.5, places=2)
        bridge.dx = -(config.RETURN_PUSH + 50.0)  # push back out through the side it came in by
        drain(bridge)
        self.assertFalse(bridge.on_phone)

    def test_configure_says_edge_hand_off_is_off(self):
        phone = FakePhone()
        bridge = make_bridge(phone)
        comp = FakeCompositor(cursor=None)
        comp.available = False
        bridge.handoff = h = Handoff(bridge, types.SimpleNamespace(phones={}), None, comp)
        said = []
        with mock.patch("side_control.backend.handoff.log", said.append):
            h.configure(phone, "right", False, 393, 852)
        self.assertTrue(any("edge hand-off is off" in line for line in said))


# eDP-1 (1920x1080 at scale 1.2) below DP-1 (3440x1440), offset to the left: the issue's example
LAPTOP, WIDE = (0, 0, 1600, 900), (-791, -1440, 2649, 0)


class Monitors(unittest.TestCase):
    """Outer edges of the monitor layout: zones, fractions and where the pointer comes back."""

    def spans(self, rects, side):
        return [(fixed, a0, a1) for fixed, a0, a1, _ in outer_edges(rects)[side]]

    def test_one_monitor(self):
        edges = outer_edges([(0, 0, 1920, 1080)])
        self.assertEqual(edges["left"], [(0, 0, 1080, 0)])
        self.assertAlmostEqual(along_edge(edges["right"], "right", 1919, 540, 2), 0.5)
        self.assertIsNone(along_edge(edges["right"], "right", 1900, 540, 2))
        self.assertEqual(point_on_edge(edges["bottom"], "bottom", 0.25, 40), (480, 1039))

    def test_offset_monitors_of_different_sizes(self):
        rects = [LAPTOP, WIDE]
        # each monitor's own left edge counts; the edge between them does not
        self.assertEqual(self.spans(rects, "left"), [(-791, -1440, 0), (0, 0, 900)])
        self.assertEqual(self.spans(rects, "top"), [(-1440, -791, 2649)])
        self.assertEqual(self.spans(rects, "bottom"), [(0, -791, 0), (900, 0, 1600), (0, 1600, 2649)])
        edges = outer_edges(rects)
        self.assertIsNotNone(along_edge(edges["left"], "left", 0, 450, 2))  # the laptop's left edge
        self.assertIsNone(along_edge(edges["top"], "top", 800, 0, 2))  # under the wide monitor
        self.assertAlmostEqual(along_edge(edges["left"], "left", -791, -720, 2), 720 / 2340)
        # the pointer comes back onto a monitor, never into the corner of the box no monitor covers
        for frac in (0.0, 0.3, 0.62, 0.8, 0.999):
            x, y = point_on_edge(edges["left"], "left", frac, 40)
            self.assertTrue(any(l <= x < r and t <= y < b for l, t, r, b in rects), (frac, x, y))  # noqa: E741
            self.assertAlmostEqual(along_edge(edges["left"], "left", x - 38, y, 2), frac, delta=0.001)

    def test_side_by_side_different_heights(self):
        rects = [(0, 0, 1920, 1080), (1920, 0, 4480, 1440)]
        self.assertEqual(self.spans(rects, "right"), [(4480, 0, 1440)])
        self.assertEqual(self.spans(rects, "bottom"), [(1080, 0, 1920), (1440, 1920, 4480)])
        # the stretch of the big monitor's left edge below the small one is an outer edge
        self.assertEqual(self.spans(rects, "left"), [(0, 0, 1080), (1920, 1080, 1440)])

    def test_stacked_gap_mirrored(self):
        self.assertEqual(self.spans([(0, 0, 1920, 1080), (0, 1080, 1920, 2160)], "top"), [(0, 0, 1920)])
        gap = [(0, 0, 1920, 1080), (2000, 0, 3920, 1080)]
        self.assertEqual(self.spans(gap, "right"), [(1920, 0, 1080), (3920, 0, 1080)])
        self.assertEqual(self.spans([(0, 0, 1920, 1080)] * 2, "left"), [(0, 0, 1080)])

    def test_pieces_split_a_span_across_monitors(self):
        edges = outer_edges([LAPTOP, WIDE])
        # the middle third of the left side: the bottom of the wide monitor and the top of the laptop
        self.assertEqual([(i, round(a0), round(a1)) for _, a0, a1, i in pieces(edges["left"], 0.5, 0.8)],
                         [(1, -270, 0), (0, 0, 432)])

    def test_rotated_and_scaled_monitors_from_hyprland(self):
        hypr = Hyprland.__new__(Hyprland)
        hypr.rects, hypr.rects_t = None, 0.0
        hypr.request = lambda _cmd: json.dumps([
            {"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.2, "transform": 0},
            {"x": 1600, "y": 0, "width": 2560, "height": 1440, "scale": 1, "transform": 1}])
        self.assertEqual(hypr.layout(), [(0, 0, 1600, 900), (1600, 0, 3040, 2560)])


class MultiMonitorHandoff(unittest.TestCase):
    def test_left_zone_on_the_laptop_and_back_onto_a_monitor(self):
        phone = FakePhone()
        phone.zone, phone.address, phone.require_near = "left", "AA", False
        bridge = make_bridge(phone)
        bridge.on_phone = False
        bridge.fleet = types.SimpleNamespace(connected=lambda: [phone])
        bridge.handoff = h = Handoff(bridge, bridge.fleet, None, FakeCompositor([LAPTOP, WIDE], (0, 450)))
        entered = []
        h.enter = lambda p, edge, frac: entered.append((edge, round(frac, 3)))
        with mock.patch.object(config, "SWITCH_DELAY_S", 0):
            h.pc_motion(-(config.EDGE_PUSH_MM + 1), 0)  # pushing left off the laptop, below the wide one
        self.assertEqual(entered, [("left", round((1440 + 450) / 2340, 3))])
        bridge.on_phone, h.edge = True, "left"
        h.vy = phone.size()[1] * 0.2  # leaving the device's right side high up: the wide monitor
        h.leave()
        x, y = h.comp.warped
        self.assertEqual(x, -791 + config.RETURN_INSET_PX)
        self.assertAlmostEqual(y, -1440 + 0.2 * 2340, delta=1)


class SpanHandoff(unittest.TestCase):
    def test_device_beside_the_laptop_maps_onto_its_own_stretch(self):
        """iPhone against the laptop's left edge (under a wider monitor): its whole height is the laptop's
        stretch of the left side, going in and coming back."""
        phone = FakePhone()
        phone.zone, phone.span = "left", (1440 / 2340, 1.0)  # the laptop's part of the left side
        bridge = make_bridge(phone)
        h = bridge.handoff = Handoff(bridge, None, None, FakeCompositor([LAPTOP, WIDE]))
        h.edge, h.entry_frac = "left", (1440 + 450) / 2340  # pushed off the laptop at half its height
        h.on_enter()
        drain(bridge)
        self.assertAlmostEqual(h.vy / phone.size()[1], 0.5, places=2)
        h.vy = 0.05 * phone.size()[1]  # leaving the iPhone near its top
        h.leave()
        x, y = h.comp.warped
        self.assertEqual(x, config.RETURN_INSET_PX)  # on the laptop, not on the wide monitor above
        self.assertAlmostEqual(y, 0.05 * 900, delta=2)


class ThreeFingerSwitch(unittest.TestCase):
    def swipe(self, on_phone):
        """Three fingers 30 mm to the right, then lifted: (buttons clicked and devices cycled mid-swipe,
        devices cycled once the fingers are up)."""
        bridge, _phone, _mouse = make_mouse_bridge(on_phone=on_phone)
        events = []
        bridge.cycle = lambda step: events.append(("cycle", step))
        bridge.click = lambda bit: events.append(("click", bit))
        tp = make_touchpad()
        tp.three, tp.three_done, tp.three_step = None, False, 0
        for dx in (0, 10, 20, 30):
            tp.contacts = {i: [10 * i + dx, 5] for i in range(3)}
            bridge.three_finger_switch(tp)
        during = list(events)
        tp.contacts = {}
        bridge.three_finger_switch(tp)
        return during, events[len(during):]

    def test_on_a_device_opens_its_app_switcher(self):
        self.assertEqual(self.swipe(on_phone=True), ([("click", config.SWIPE_SIDE_BUTTON)], []))

    def test_on_the_pc_the_swipe_is_the_compositors(self):
        self.assertEqual(self.swipe(on_phone=False), ([], []))
        with mock.patch("side_control.backend.bridge.THREE_FINGER_FROM_PC", True):
            # and the switch waits for the fingers: the touchpad must not change hands mid-swipe
            self.assertEqual(self.swipe(on_phone=False), ([], [("cycle", 1)]))


class HideCursor(unittest.TestCase):
    """Hyprland.hide_cursor against a fake IPC socket: only a pointer it hid is shown again."""

    def make(self, user_invisible=False, mark_exists=False):
        self.mark = os.path.join(tempfile.mkdtemp(), "hidden")
        if mark_exists:
            open(self.mark, "w").close()
        self.sent = []

        def request(command):
            self.sent.append(command)
            if command == "j/getoption cursor:invisible":
                return '{"option": "cursor:invisible", "bool": %s, "set": false}' % str(user_invisible).lower()
            return "ok"
        with mock.patch("side_control.backend.compositor.CURSOR_MARK", self.mark):
            comp = Hyprland()
        comp.request = request
        return comp

    def hide_cursor(self, comp, hide):
        with mock.patch("side_control.backend.compositor.CURSOR_MARK", self.mark):
            comp.hide_cursor(hide)

    def test_hides_and_shows(self):
        comp = self.make()
        self.hide_cursor(comp, True)
        self.assertTrue(comp.hidden and os.path.exists(self.mark))
        self.assertIn("eval hl.config({cursor = {invisible = true}})", self.sent)
        self.hide_cursor(comp, False)
        self.assertFalse(comp.hidden or os.path.exists(self.mark))
        self.assertEqual(self.sent[-1], "eval hl.config({cursor = {invisible = false}})")

    def test_leaves_the_users_own_setting(self):
        comp = self.make(user_invisible=True)
        self.hide_cursor(comp, True)
        self.hide_cursor(comp, False)
        self.assertEqual(self.sent, ["j/getoption cursor:invisible"])

    def test_shows_a_pointer_a_killed_backend_hid(self):
        comp = self.make(mark_exists=True)
        self.hide_cursor(comp, False)
        self.assertEqual(self.sent, ["eval hl.config({cursor = {invisible = false}})"])
        self.assertFalse(os.path.exists(self.mark))


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
