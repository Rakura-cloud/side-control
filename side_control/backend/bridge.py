# SPDX-License-Identifier: GPL-3.0-or-later
"""Forwards keyboard and touchpad input to the device in control, gestures included."""

import math
import time
from collections import deque

from evdev import ecodes as e

from . import config
from .config import (
    ACTIONS,
    ACTION_MODS,
    APP_ZOOM_IN,
    APP_ZOOM_OUT,
    CTRL,
    FLICK_MM,
    FLICK_PAUSE_S,
    FLICK_REPORTS,
    FLICK_ROOM,
    FLICK_STEP,
    GESTURE_LOCK_MM,
    GLIDE_PAUSE_S,
    GLIDE_STEP,
    HOTKEYS,
    MOD_KEYS,
    MOUSE_COUNTS_PER_MM,
    MOUSE_INTERVAL,
    MOUSE_PAUSE_S,
    MOUSE_SPEED,
    PASTE_KEY,
    PASTE_MODS,
    PINCH_STEP_MM,
    SCAN_WHILE_CONTROLLING,
    SCROLL_STEP_MM,
    SWIPE_SIDE_BUTTON,
    SWIPE_UP_BUTTON,
    SWIPE_UP_MM,
    SWITCH_SWIPE_MM,
    TAP_TIME,
    TAP_TRAVEL_MM,
    THREE_FINGER_FROM_PC,
    TOGGLE,
    TOUCHPAD_SPEED,
    TYPE_DELAY_S,
    TYPE_MAX,
    ZOOM_IN,
    ZOOM_OUT,
)
from .devices import NO_PHONE
from .events import EVENTS, log
from .hid import BUTTONS, FINGERS, KEYMAP, MODS, disable_sniff, type_keys
from .inputs import Accel, Shake, Touchpad, is_touchpad, scan_inputs


class Bridge:
    def __init__(self, fleet):
        self.fleet = fleet
        self.phone = NO_PHONE  # output device: the one in control, or the last one that was
        self.devices = {}  # fd -> InputDevice
        self.touchpads = {}  # fd -> Touchpad, for the devices that are touchpads
        self.accels = {}  # fd -> Accel, for the mice
        self.seen = set()  # input device nodes looked at (see scan_inputs)
        self.on_phone = False
        self.toggle_pending = False
        self.action_pending = None  # a HOTKEYS action, run once every key is up again
        self.held = set()
        self.locked = False  # Scroll Lock: the pointer stays on the screen it is on
        self.shake = Shake()
        self.handoff = None  # set by main() once Proximity exists
        self.proximity = None
        self.request_paste = None  # set by main() to Control.request_paste: asks the GUI for the
                                   # computer's clipboard, False with no GUI attached to read it
        self.paste_replay = None   # (modifier byte, usage) of the paste chord swallowed last: pressed
                                   # on the device when the clipboard had nothing to type
        self.paste_held = False    # the paste chord's V is down: its repeats and release are ours too
        self.typing = []           # (modifier byte, usage) presses left to type from the clipboard...
        self.typing_phone = None   # ...on the device in control at the time
        self.typing_at = 0.0       # when the next one is due
        self.rel = [0, 0]    # mouse motion collected until the next SYN while the PC has control
        self.rel_t = 0.0     # when that motion last went to the hand-off
        self.reset_reports()
        self.rescan()

    def rescan(self):
        """Pick up input devices that appeared (a Bluetooth mouse that woke up, a USB keyboard), taken
        over at once if a device has the cursor."""
        for fd, dev in scan_inputs(self.seen).items():
            self.devices[fd] = dev
            if is_touchpad(dev):
                self.touchpads[fd] = Touchpad(dev)
            if self.on_phone:
                try:
                    dev.grab()
                except OSError:
                    pass

    def reset_reports(self):
        self.mods, self.keys, self.buttons = 0, [], 0
        self.dx = self.dy = 0.0
        self.wheel = self.pan = 0
        self.last_mouse = 0.0
        # pointer moves of our own, one per report (the glide on entry, swipe flicks): (x, y, button 1
        # pressed: True / False / None unchanged), or a float: pause that many seconds...
        self.steps = deque()
        self.steps_at = 0.0  # ...not before this time
        for tp in self.touchpads.values():
            tp.reset()

    def toggle(self):
        """PC -> the device used last -> the next device ... -> PC."""
        phones = self.fleet.connected()
        if self.on_phone:
            later = phones[phones.index(self.phone) + 1:] if self.phone in phones else []
            self.switch_to(later[0] if later else None)
        elif phones:
            self.switch_to(self.phone if self.phone in phones else phones[0])
        else:
            log("no iPhone/iPad connected - pair/connect one first")

    def switch_to(self, device):
        """Hand keyboard and touchpad to a connected Phone, or back to the PC with None."""
        if device is not None and not device.connected:
            log(f"{device.alias} is not connected")
            return
        if (device is None and not self.on_phone) or (device is self.phone and self.on_phone):
            return
        was_on = self.on_phone
        if was_on:
            self.release_all()  # let go of every key and button on the device being left
        self.on_phone = device is not None
        if device is not None:
            self.phone = device
            # a device may have asked the link back into power-save since we first connected
            disable_sniff(device.dev_id, device.address, quiet=True)
        if self.proximity and not SCAN_WHILE_CONTROLLING:
            self.proximity.set_scanning(not self.on_phone)
        if self.on_phone != was_on:
            for d in self.devices.values():
                try:
                    d.grab() if self.on_phone else d.ungrab()
                except OSError:
                    pass
        held = {fd: (tp.touching, tp.fingers, tp.x, tp.y) for fd, tp in self.touchpads.items()}
        self.reset_reports()
        for fd, (touching, fingers, x, y) in held.items():
            if touching:  # hand-off mid-swipe: keep tracking the finger, but it is not a tap
                tp = self.touchpads[fd]
                tp.touching, tp.fingers, tp.x, tp.y = True, fingers, x, y
                tp.max_fingers, tp.travel, tp.t0 = fingers, 99.0, time.monotonic()
        if self.handoff:
            if was_on:
                self.handoff.on_leave()
            if self.on_phone:
                self.handoff.on_enter()
        log(f"input -> {device.alias} (Ctrl+Alt+P for the next one)" if device else "input -> PC")

    def release_all(self):
        self.phone.send(1, bytes(8))
        self.phone.send(2, bytes(5))

    def drop_phone(self):
        if self.on_phone:
            self.switch_to(None)

    def handle(self, fd):
        dev = self.devices[fd]
        try:
            events = list(dev.read())
        except BlockingIOError:
            return
        except OSError:
            log(f"lost input device {dev.name}")
            del self.devices[fd]
            self.touchpads.pop(fd, None)
            self.accels.pop(fd, None)
            try:
                dev.close()
            except OSError:
                pass
            self.held.clear()  # keys held on it will never be let go
            if self.on_phone:  # nor on the device
                self.release_all()
                self.mods, self.keys, self.buttons = 0, [], 0
            return
        tp = self.touchpads.get(fd)
        for ev in events:
            if tp:
                tp.track(ev)
                if ev.type == e.EV_SYN:
                    self.three_finger_switch(tp)
            if ev.type == e.EV_KEY and ev.code == PASTE_KEY and self.paste_held and ev.value != 1:
                if not ev.value:  # its press was the paste chord: the device never saw V go down
                    self.paste_held = False
                    self.held.discard(ev.code)
                continue
            if ev.type == e.EV_KEY and ev.value != 2:
                if self.track_toggle(ev):
                    continue
            if not self.on_phone:
                self.pc_event(tp, ev)
                continue
            if tp:
                self.touchpad_event(tp, ev)
            elif ev.type == e.EV_KEY and ev.code in BUTTONS:
                self.set_button(BUTTONS[ev.code], ev.value)
            elif ev.type == e.EV_KEY:
                self.key_event(ev)
            elif ev.type == e.EV_REL:
                if ev.code in (e.REL_X, e.REL_Y):
                    self.accels.setdefault(fd, Accel()).frame[ev.code == e.REL_Y] += ev.value
                elif ev.code in (e.REL_WHEEL, e.REL_HWHEEL):  # HWHEEL counts right as positive, like AC Pan
                    step = -ev.value if config.MOUSE_NATURAL_SCROLL else ev.value
                    if ev.code == e.REL_WHEEL:
                        self.wheel += step
                    else:
                        self.pan += step
            elif ev.type == e.EV_SYN:
                accel = self.accels.get(fd)
                if accel and any(accel.frame):
                    ax, ay = accel.take(time.monotonic())
                    self.dx += ax * MOUSE_SPEED * self.phone.speed
                    self.dy += ay * MOUSE_SPEED * self.phone.speed
                self.flush_mouse()
        if self.toggle_pending and not self.held:
            self.toggle_pending = False
            self.toggle()
        if self.action_pending and not self.held:
            action, self.action_pending = self.action_pending, None
            self.run_action(action)

    def run_action(self, action):
        if action == "toggle_sharing":
            config.SHARE_CURSOR = not config.SHARE_CURSOR
            log(f"cursor sharing {'on' if config.SHARE_CURSOR else 'off'}")
        elif action == "next_device":
            phones = self.fleet.connected()
            if not phones:
                log("no device connected")
            elif self.on_phone and self.phone in phones:
                self.switch_to(phones[(phones.index(self.phone) + 1) % len(phones)])
            else:
                self.switch_to(phones[0])
        elif action == "return_to_pc" and self.on_phone:
            if self.handoff and self.handoff.edge:
                self.handoff.leave()  # puts the PC pointer back at the edge it left by
            else:
                self.switch_to(None)
        elif action == "lock":
            self.locked = not self.locked
            log("pointer locked to the current screen" if self.locked else "pointer unlocked")
        elif action == "open_gui":
            EVENTS.append({"type": "open"})

    def cycle(self, step):
        """PC, then each connected device, in a ring: step +1 next, -1 previous."""
        ring = [None, *self.fleet.connected()]
        here = self.phone if self.on_phone and self.phone in ring else None
        target = ring[(ring.index(here) + step) % len(ring)]
        if target is None and self.handoff and self.handoff.edge:
            self.handoff.leave()
        else:
            self.switch_to(target)

    def three_finger_switch(self, tp):
        """Three-finger swipe left/right. On a device: its App Switcher. On the PC, only with
        THREE_FINGER_FROM_PC: the previous / next device, once every finger is off the pad (grabbing the
        touchpad mid-swipe would leave the compositor half a gesture, a workspace swipe that never ends)."""
        if tp.three_step and not tp.contacts:
            step, tp.three_step = tp.three_step, 0
            if not self.on_phone:
                self.cycle(step)
            return
        x = tp.centroid_x(3)
        if x is None:
            tp.three, tp.three_done = None, False
            return
        if tp.three is None:
            tp.three = x
        elif (not tp.three_done and not tp.swiped and abs(x - tp.three) >= SWITCH_SWIPE_MM
              and config.THREE_FINGER_SWITCH):
            tp.three_done = True
            if self.on_phone:
                self.click(SWIPE_SIDE_BUTTON)
                log("three-finger swipe left/right -> App Switcher")
            elif THREE_FINGER_FROM_PC:
                tp.three_step = 1 if x > tp.three else -1

    def pc_event(self, tp, ev):
        """While the PC has control: feed pointer motion to the hand-off edge detector and the
        shake detector."""
        h = self.handoff
        motion = None
        if tp:
            if ev.type == e.EV_ABS:
                if ev.code == e.ABS_X:
                    tp.x = ev.value
                elif ev.code == e.ABS_Y:
                    tp.y = ev.value
            elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                tp.touching, tp.last = bool(ev.value), None
                if not ev.value and h:
                    h.push.clear()
            elif ev.type == e.EV_KEY and ev.code in FINGERS and ev.value:
                tp.fingers, tp.last = FINGERS[ev.code], None
            elif ev.type == e.EV_SYN and tp.touching and tp.fingers == 1 and None not in (tp.x, tp.y):
                if tp.last:
                    motion = ((tp.x - tp.last[0]) / tp.resx, (tp.y - tp.last[1]) / tp.resy)
                tp.last = (tp.x, tp.y)
        elif ev.type == e.EV_REL:
            if ev.code in (e.REL_X, e.REL_Y):
                self.rel[ev.code == e.REL_Y] += ev.value
        elif ev.type == e.EV_SYN and any(self.rel):
            now = time.monotonic()
            if now - self.rel_t < MOUSE_INTERVAL:  # a gaming mouse reports 1000 times a second: merge
                return
            if h and now - self.rel_t > MOUSE_PAUSE_S:  # moving again after a rest
                h.push.clear()
            self.rel_t = now
            rx, ry, self.rel = self.rel[0], self.rel[1], [0, 0]
            motion = (rx / MOUSE_COUNTS_PER_MM, ry / MOUSE_COUNTS_PER_MM)
        if motion is None:
            return
        if config.SHAKE_TO_LOCATE and self.shake.feed(motion[0]):
            EVENTS.append({"type": "shake"})
        if h and any(p.zone for p in self.fleet.connected()):
            h.pc_motion(*motion)

    def track_toggle(self, ev):
        """Track held keys; returns True if the event triggered a hotkey and must not be forwarded."""
        if ev.code >= e.BTN_MISC:  # mouse/touchpad buttons never count as held keys
            return False
        if ev.value:
            self.held.add(ev.code)
            if TOGGLE <= self.held:
                self.toggle_pending = True
                return True
            mods = {MOD_KEYS[k] for k in self.held if k in MOD_KEYS}
            for need, key, action in HOTKEYS:
                if ev.code == key and mods == need:
                    if self.on_phone and mods:
                        self.phone.send(1, bytes(8))  # let go of the modifiers on the device first
                    self.action_pending = action
                    return True
            if self.track_paste(mods, ev.code):
                return True
            if self.on_phone and ev.code in ACTIONS and ACTION_MODS <= self.held:
                self.phone.send(1, bytes(8))  # let go of Ctrl+Alt on the phone first
                self.click(ACTIONS[ev.code])
                log(f"hotkey -> mouse button {ACTIONS[ev.code].bit_length()}")
                return True
        else:
            self.held.discard(ev.code)
        return False

    def track_paste(self, mods, key):
        """Ctrl+V / Super+V while a device has control and Share Clipboard is on: ask the GUI to read
        the computer's clipboard and type it here (this process runs without a Wayland session). True:
        the chord is ours and is not typed on the device; False: it goes there as usual."""
        if not (self.on_phone and config.SHARE_CLIPBOARD and key == PASTE_KEY and mods in PASTE_MODS):
            return False
        if not (self.request_paste and self.request_paste()):  # no GUI attached to read the clipboard
            return False
        self.phone.send(1, bytes(8))  # let go of Ctrl/Super on the device first
        self.paste_held = True
        self.paste_replay = (MODS[e.KEY_LEFTCTRL if "ctrl" in mods else e.KEY_LEFTMETA], KEYMAP[key])
        return True

    def key_event(self, ev):
        if ev.code in MODS:
            if ev.value:
                self.mods |= MODS[ev.code]
            else:
                self.mods &= ~MODS[ev.code]
        elif ev.code in KEYMAP:
            usage = KEYMAP[ev.code]
            if ev.value and usage not in self.keys:
                self.keys.append(usage)
            elif not ev.value and usage in self.keys:
                self.keys.remove(usage)
        else:
            return
        keys = (self.keys + [0] * 6)[:6]
        self.phone.send(1, [self.mods, 0] + keys)

    def set_button(self, bit, pressed):
        self.buttons = self.buttons | bit if pressed else self.buttons & ~bit
        self.flush_mouse(force=True)

    def click(self, bit):
        self.set_button(bit, True)
        self.set_button(bit, False)

    def send_on(self, phone, ix, iy):
        """Move a specific device's pointer, whoever has control."""
        keep, self.phone = self.phone, phone
        try:
            self.send_mouse(ix, iy)
        finally:
            self.phone = keep

    def click_on(self, phone, bit):
        """Click on a specific device, whoever has control."""
        keep, self.phone = self.phone, phone
        try:
            self.click(bit)
        finally:
            self.phone = keep

    def touchpad_event(self, tp, ev):
        if ev.type == e.EV_ABS:
            if ev.code == e.ABS_X:
                tp.x = ev.value
            elif ev.code == e.ABS_Y:
                tp.y = ev.value
        elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
            if ev.value:
                tp.touching, tp.t0, tp.travel, tp.last = True, time.monotonic(), 0.0, None
                tp.max_fingers, tp.clicked = 0, False
                tp.swipe, tp.swiped = 0.0, False
                self.end_gesture(tp)
            else:
                tp.touching = False
                self.end_gesture(tp)
                quick = time.monotonic() - tp.t0 < TAP_TIME and tp.travel < TAP_TRAVEL_MM
                if quick and not tp.clicked and config.TAP_TO_CLICK:
                    self.click(2 if tp.max_fingers >= 2 else 1)
        elif ev.type == e.EV_KEY and ev.code in FINGERS:
            if ev.value:
                tp.fingers = FINGERS[ev.code]
                tp.max_fingers = max(tp.max_fingers, tp.fingers)
                tp.last = None  # avoid a jump when the tracked finger changes
                tp.swipe = 0.0
                self.end_gesture(tp)
        elif ev.type == e.EV_KEY and ev.code in BUTTONS:  # physical clickpad press
            if ev.value:
                tp.clicked = True
                self.end_gesture(tp)
                tp.click_bit = 2 if tp.fingers >= 2 else BUTTONS[ev.code]
                self.set_button(tp.click_bit, True)
            elif tp.click_bit:
                self.set_button(tp.click_bit, False)
                tp.click_bit = 0
        elif ev.type == e.EV_SYN:
            if tp.touching and tp.x is not None and tp.y is not None:
                if tp.last:
                    mx = (tp.x - tp.last[0]) / tp.resx
                    my = (tp.y - tp.last[1]) / tp.resy
                    tp.travel += math.hypot(mx, my)
                    if tp.fingers >= 3 and not tp.click_bit:
                        tp.swipe += my
                        if (tp.swipe < -SWIPE_UP_MM and not tp.swiped and not tp.three_done
                                and config.SWIPE_UP_HOME):
                            tp.swiped = True
                            self.click(SWIPE_UP_BUTTON)
                            log("three-finger swipe up -> Home")
                    elif tp.fingers >= 2 and not tp.click_bit:
                        self.two_finger_gesture(tp, mx, my)
                    else:
                        self.dx += mx * TOUCHPAD_SPEED * self.phone.speed
                        self.dy += my * TOUCHPAD_SPEED * self.phone.speed
                tp.last = (tp.x, tp.y)
            self.flush_mouse()

    def two_finger_gesture(self, tp, mx, my):
        """Scroll up/down or left/right (locked to the first direction), or pinch to zoom."""
        pair, pinch = tp.two_fingers(), 0.0
        if pair and tp.pair:  # the midpoint of both fingers, so a pinch does not read as a scroll
            mx, my, pinch = (a - b for a, b in zip(pair, tp.pair))
        tp.pair = pair
        if tp.gesture is None:
            tp.move[0] += mx
            tp.move[1] += my
            tp.zoom += pinch
            travel = math.hypot(*tp.move)
            if max(travel, abs(tp.zoom)) < GESTURE_LOCK_MM:
                return
            if abs(tp.zoom) > travel:
                tp.gesture = "pinch"
            else:
                tp.gesture = "h" if abs(tp.move[0]) > abs(tp.move[1]) else "v"
            (mx, my), pinch, tp.zoom = tp.move, tp.zoom, 0.0  # the deciding travel counts too
        if tp.gesture == "pinch":
            tp.zoom += pinch
            steps = int(tp.zoom / PINCH_STEP_MM)
            tp.zoom -= steps * PINCH_STEP_MM
            zoom_in, zoom_out = (APP_ZOOM_IN, APP_ZOOM_OUT) if self.mods & CTRL else (ZOOM_IN, ZOOM_OUT)
            for _ in range(abs(steps)):
                self.tap_key(*(zoom_in if steps > 0 else zoom_out))
            return
        if tp.gesture == "h" and config.SWIPE_FLICK:
            tp.scroll += mx
            if not tp.flicked and abs(tp.scroll) >= FLICK_MM:
                tp.flicked = True
                self.flick((1 if tp.scroll > 0 else -1) * (1 if config.NATURAL_SCROLL else -1))
            return
        tp.scroll += mx if tp.gesture == "h" else my
        steps = int(tp.scroll / SCROLL_STEP_MM)
        tp.scroll -= steps * SCROLL_STEP_MM
        if tp.gesture == "h":  # AC Pan counts right as positive, the wheel counts up as positive
            self.pan += steps if config.NATURAL_SCROLL else -steps
        else:
            self.wheel += -steps if config.NATURAL_SCROLL else steps

    def flick(self, direction):
        """Turn one page: drag the content fast towards `direction` (-1 left, +1 right) and let go, then
        bring the pointer back the same way, report by report."""
        h = self.handoff
        if h and h.edge is not None:  # where the pointer is known: make room before the edge first
            w, _ = self.phone.size()
            need = FLICK_ROOM * FLICK_STEP * FLICK_REPORTS
            room = h.vx if direction < 0 else w - h.vx
            if room < need:
                away = -direction * round(need - room)
                self.glide(away, 0, pause=0.0)
                h.vx += away
        out = (direction * FLICK_STEP, 0, None)
        self.steps.extend([(0, 0, True), *[out] * (FLICK_REPORTS - 1), (out[0], 0, False), FLICK_PAUSE_S,
                           *[(-out[0], 0, None)] * FLICK_REPORTS])

    def end_gesture(self, tp):
        tp.reset_gesture()

    def tap_key(self, mods, usage):
        """Press and release one shortcut on the phone, then restore the keys really held."""
        self.phone.send(1, [mods, 0, usage, 0, 0, 0, 0, 0])
        self.phone.send(1, [self.mods, 0] + (self.keys + [0] * 6)[:6])

    def type_text(self, text):
        """The GUI's answer to a paste chord: type the computer's clipboard's text on the device in
        control (Share Clipboard). With nothing to type, the chord goes to the device after all."""
        chord, self.paste_replay = self.paste_replay, None
        keys, skipped, cut = type_keys(text or "", TYPE_MAX)
        if cut:
            log(f"clipboard text cut to {TYPE_MAX} characters ({cut} left out)")
        if skipped:
            log(f"{skipped} clipboard character(s) skipped - no key for them on a US keyboard")
        if not keys:
            if chord:
                self.tap_key(*chord)  # nothing to type: the chord passes to the device as usual
            return
        if self.typing and self.typing_phone is self.phone:
            self.typing += keys
        else:
            self.typing, self.typing_phone, self.typing_at = keys, self.phone, time.monotonic()
        log(f"typing {len(keys)} characters on {self.phone.alias}")

    def step_typing(self):
        """Type one clipboard character per TYPE_DELAY_S: the loop must never block for a whole text.
        The keys are ours alone, so the pointer model of the hand-off never hears of them. Stops when
        the device loses control or disconnects."""
        if not self.typing:
            return
        if not (self.on_phone and self.phone is self.typing_phone and self.typing_phone.connected):
            log("typing stopped: the device no longer has the keyboard")
            self.typing = []
            return
        now = time.monotonic()
        if now < self.typing_at:
            return
        mods, usage = self.typing.pop(0)
        self.typing_at = now + TYPE_DELAY_S
        self.phone.send(1, [mods, 0, usage, 0, 0, 0, 0, 0])
        self.phone.send(1, bytes(8))  # key up: the all-keys-released report
        if not self.typing:
            self.phone.send(1, [self.mods, 0] + (self.keys + [0] * 6)[:6])  # the keys really held

    def mouse_pending(self):
        return self.on_phone and (abs(self.dx) >= 1 or abs(self.dy) >= 1 or self.wheel or self.pan
                                  or bool(self.steps))

    def glide(self, dx, dy, pause=GLIDE_PAUSE_S):
        """Move the device pointer (dx, dy) counts at an even pace, GLIDE_STEP counts per report, after
        `pause` seconds."""
        n = math.ceil(max(abs(dx), abs(dy)) / GLIDE_STEP)
        step = lambda v, i: round(v * i / n) - round(v * (i - 1) / n)
        if n:
            self.steps.extend([pause, *((step(dx, i), step(dy, i), None) for i in range(1, n + 1))])

    def flush_mouse(self, force=False):
        now = time.monotonic()
        due = now - self.last_mouse >= MOUSE_INTERVAL
        sx = sy = 0  # one of our own steps: already accounted for in the hand-off model
        press = None
        if self.steps and not force and due and now >= self.steps_at and not self.phone.backlogged():
            step = self.steps.popleft()
            if isinstance(step, float):
                self.steps_at = now + step
            else:
                sx, sy, press = step
                if press is not None:
                    self.buttons = self.buttons | 1 if press else self.buttons & ~1
        ix, iy, w, p = int(self.dx), int(self.dy), self.wheel, self.pan
        if not (force or ix or iy or sx or sy or w or p or press is not None):
            return
        # Only motion is held back; button changes always go out immediately
        if not force and (not due or self.phone.backlogged()):
            return
        self.dx -= ix
        self.dy -= iy
        self.wheel = self.pan = 0
        self.last_mouse = now
        self.send_mouse(ix + sx, iy + sy, w, p)
        if self.handoff and (ix or iy):
            self.handoff.phone_motion(ix, iy)

    def send_mouse(self, ix, iy, w=0, p=0):
        """Send a relative move, split into reports of at most 127 counts per axis."""
        while True:
            cx, cy, cw, cp = (max(-127, min(127, v)) for v in (ix, iy, w, p))
            self.phone.send(2, [self.buttons, cx & 0xFF, cy & 0xFF, cw & 0xFF, cp & 0xFF])
            ix, iy, w, p = ix - cx, iy - cy, w - cw, p - cp
            if not (ix or iy or w or p):
                break
