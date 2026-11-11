# SPDX-License-Identifier: GPL-3.0-or-later
"""The app's settings: the shortcuts and gestures round trip, migration, clash detection - no
GTK, display or backend needed: python -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from side_control.backend import config as backend_config  # noqa: E402
from side_control.app import config  # noqa: E402
from evdev import ecodes as e  # noqa: E402
from test_backend import (DEFAULT_ACTIONS, DEFAULT_HOTKEYS, DEFAULT_TOGGLE,  # noqa: E402
                          make_mouse_bridge, make_touchpad)


def combo(mods, key):
    """A shortcut override as settings.json holds it (the accel string is only for showing)."""
    return {"accel": "<Super>j", "mods": sorted(mods), "key": key}


class Settings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        patch = mock.patch.multiple(config, CONFIG_DIR=os.path.join(tmp, "side-control"),
                                    CONFIG_PATH=os.path.join(tmp, "side-control", "settings.json"))
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, settings):
        os.makedirs(config.CONFIG_DIR)
        with open(config.CONFIG_PATH, "w") as f:
            json.dump(settings, f)

    def test_shortcuts_gestures_and_length_round_trip(self):
        settings = config.load_settings()
        settings["shortcuts"]["toggle_sharing"] = combo(["ctrl", "alt"], e.KEY_J)
        settings["shortcuts"]["lock"] = combo([], e.KEY_L)
        settings["gesture_actions"]["swipe_lr"] = "scroll"
        settings["swipe_length"] = "long"
        config.save_settings(settings)
        loaded = config.load_settings()
        self.assertEqual(loaded["shortcuts"]["toggle_sharing"], settings["shortcuts"]["toggle_sharing"])
        self.assertEqual(loaded["shortcuts"]["lock"], settings["shortcuts"]["lock"])
        self.assertEqual(loaded["gesture_actions"]["swipe_lr"], "scroll")
        self.assertEqual(loaded["swipe_length"], "long")

    def test_the_old_three_finger_switch_off_becomes_the_gesture(self):
        self.write({"three_finger_switch": False})
        loaded = config.load_settings()
        self.assertTrue(loaded["three_finger_switch"])
        self.assertEqual(loaded["gesture_actions"]["swipe_side"], "none")

    def test_unknown_values_fall_back(self):
        self.write({"swipe_length": "enormous", "gesture_actions": {"swipe_up": "sideways", "pinch": "zoom"},
                    "shortcuts": {"button9": {"mods": ["ctrl", "alt"], "key": 38},
                                  "lock": {"mods": [], "key": e.KEY_L}}})
        loaded = config.load_settings()
        self.assertEqual(loaded["swipe_length"], "medium")
        self.assertEqual(loaded["gesture_actions"], {"pinch": "zoom"})
        self.assertEqual(loaded["shortcuts"], {"lock": {"mods": [], "key": e.KEY_L}})


class Clash(unittest.TestCase):
    def test_defaults_clash(self):
        self.assertIsNone(config.shortcut_clash("next_device", ["super"], e.KEY_RIGHT, {}))
        self.assertEqual(config.shortcut_clash("open_gui", ["super"], e.KEY_RIGHT, {}),
                         "Send Cursor to Next Device")

    def test_overrides_clash(self):
        shortcuts = {"cycle": combo(["ctrl", "alt"], e.KEY_J)}
        self.assertEqual(config.shortcut_clash("open_gui", ["ctrl", "alt"], e.KEY_J, shortcuts),
                         "Cycle Devices")
        self.assertIsNone(config.shortcut_clash("open_gui", ["ctrl", "alt"], e.KEY_J, {}))

    def test_button_chords_match_on_ctrl_alt_alone(self):
        # button4 is Ctrl+Alt+H by default: a captured Ctrl+Alt+Shift+H would clash for button5
        self.assertEqual(config.shortcut_clash("button5", ["ctrl", "alt", "shift"], e.KEY_H, {}),
                         "Mouse Button 4 on the Device")
        self.assertIsNone(config.shortcut_clash("button4", ["ctrl", "alt", "shift"], e.KEY_H, {}))


class DefaultsAgree(unittest.TestCase):
    """The app's tables and the backend's defaults say the same thing, and what the app sends is
    what the backend matches."""

    def setUp(self):
        patch = mock.patch.multiple(backend_config, HOTKEYS=list(DEFAULT_HOTKEYS),
                                    TOGGLE=set(DEFAULT_TOGGLE), ACTIONS=dict(DEFAULT_ACTIONS))
        patch.start()
        self.addCleanup(patch.stop)

    def test_shortcuts(self):
        self.assertEqual({a: (set(v[3]), v[4]) for a, v in config.SHORTCUTS.items()},
                         backend_config.DEFAULT_SHORTCUTS)

    def test_gestures(self):
        self.assertEqual(config.GESTURE_DEFAULTS, backend_config.GESTURE_ACTIONS)
        for gesture, choices in config.GESTURE_CHOICES.items():
            self.assertEqual(set(choices), set(backend_config.GESTURE_CHOICES[gesture]))
        self.assertEqual(set(config.GESTURE_ACTION_LABELS),
                         {"none", "flick", "scroll", "zoom"} | {f"button{n}" for n in range(4, 9)})

    def test_reaching_the_backend(self):
        given = {a: {"mods": sorted(v[3]), "key": v[4]} for a, v in config.SHORTCUTS.items()}
        given["toggle_sharing"] = {"mods": ["ctrl", "alt"], "key": e.KEY_J}
        backend_config.set_shortcuts(given)
        bridge, phone, _mouse = make_mouse_bridge()
        bridge.held = {e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_J}
        self.assertTrue(bridge.track_toggle(mock.Mock(code=e.KEY_J, value=1)))
        self.assertEqual(bridge.action_pending, "toggle_sharing")
        bridge.held = {e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_H}
        self.assertTrue(bridge.track_toggle(mock.Mock(code=e.KEY_H, value=1)))
        self.assertIn(backend_config.ACTIONS[e.KEY_H],  # the report pressed mouse button 4
                      [r[1][0] for r in phone.raw if r[0] == 2])
        with mock.patch.dict(backend_config.GESTURE_ACTIONS, {"swipe_side": "none"}):
            before = len(phone.raw)
            tp = make_touchpad()
            tp.three, tp.three_done, tp.three_step, tp.swiped = 10.0, False, 0, False
            for dx in (0, 30):
                tp.contacts = {i: [10 * i + dx, 5] for i in range(3)}
                bridge.three_finger_switch(tp)
            self.assertEqual(len(phone.raw), before)  # the swipe was set to do nothing




class ShortcutProblem(unittest.TestCase):
    """config.shortcut_problem: combinations the capture dialog refuses."""

    def test_a_key_that_types_needs_a_modifier(self):
        self.assertIsNotNone(config.shortcut_problem("lock", [], e.KEY_A))
        self.assertIsNotNone(config.shortcut_problem("lock", ["shift"], e.KEY_A))
        self.assertIsNone(config.shortcut_problem("lock", [], e.KEY_SCROLLLOCK))
        self.assertIsNone(config.shortcut_problem("lock", [], e.KEY_F9))
        self.assertIsNone(config.shortcut_problem("next_device", ["super"], e.KEY_A))

    def test_mouse_buttons_need_ctrl_alt(self):
        self.assertIsNotNone(config.shortcut_problem("button4", ["super"], e.KEY_H))
        self.assertIsNone(config.shortcut_problem("button4", ["ctrl", "alt"], e.KEY_J))


if __name__ == "__main__":
    unittest.main()
