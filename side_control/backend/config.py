# SPDX-License-Identifier: GPL-3.0-or-later
"""Every tunable of the backend. The ones the app can change while it runs (OPTIONS, and
DIAL_ONLY) are read as config.NAME elsewhere, so a change reaches every module."""

import os

from evdev import ecodes as e


NAME = "Side Control"  # Bluetooth name until one is set in the app (Settings > Device Name)
# (modifiers held, key, action) - Side Control's shortcuts. The PC's keyboard is not grabbed while the
# PC has control, so the compositor sees these too. set_shortcuts() rebuilds HOTKEYS, TOGGLE and
# ACTIONS when the GUI sends new bindings; they are read as config.NAME elsewhere, so a change
# reaches every module.
HOTKEYS = [({"super", "shift"}, e.KEY_S, "toggle_sharing"),
           ({"super"}, e.KEY_RIGHT, "next_device"),
           ({"super"}, e.KEY_ESC, "return_to_pc"),
           (set(), e.KEY_SCROLLLOCK, "lock"),
           ({"super", "alt"}, e.KEY_D, "open_gui")]
HOTKEY_ACTIONS = ("toggle_sharing", "next_device", "return_to_pc", "lock", "open_gui")
TOGGLE = {e.KEY_LEFTCTRL, e.KEY_LEFTALT, e.KEY_P}  # the "cycle" shortcut: any extra keys held count too
# Ctrl+Alt+<key> while controlling the phone presses an extra mouse button; assign each one
# in AssistiveTouch > Devices > (this PC) > Customize Additional Buttons.
ACTION_MODS = {e.KEY_LEFTCTRL, e.KEY_LEFTALT}
ACTIONS = {e.KEY_H: 0x08, e.KEY_C: 0x10, e.KEY_N: 0x20, e.KEY_S: 0x40}  # buttons 4-7
# Every shortcut's default, by action id: what set_shortcuts() keeps when the GUI does not name
# an action, or names it badly.
DEFAULT_SHORTCUTS = {action: (mods, key) for mods, key, action in HOTKEYS}
DEFAULT_SHORTCUTS.update({"cycle": ({"ctrl", "alt"}, e.KEY_P),
                          "button4": ({"ctrl", "alt"}, e.KEY_H), "button5": ({"ctrl", "alt"}, e.KEY_C),
                          "button6": ({"ctrl", "alt"}, e.KEY_N), "button7": ({"ctrl", "alt"}, e.KEY_S)})
# Mouse button bits by action id: the buttons the GUI can set gestures and shortcuts to press.
# What each does on the device is assigned in AssistiveTouch > Devices > (this PC) >
# Customize Additional Buttons (button 8, the last one in the report, is the App Switcher's).
GESTURE_BUTTONS = {"button4": 0x08, "button5": 0x10, "button6": 0x20, "button7": 0x40, "button8": 0x80}
# What each configurable gesture does while a device has control (the GUI sets them with
# set_options, see OPTIONS below; the bridge reads them as config.GESTURE_ACTIONS). The defaults
# are what was hardcoded: swipe up = button 4 (Home), left/right = button 8 (App Switcher),
# two-finger left/right = the flick that turns one page, pinch = the Zoom magnifier.
GESTURE_ACTIONS = {"swipe_up": "button4", "swipe_side": "button8", "swipe_lr": "flick", "pinch": "zoom"}
# The choices the GUI offers for each gesture; buttons press that mouse button (see above)
GESTURE_CHOICES = {"swipe_up": ("none", *GESTURE_BUTTONS),
                   "swipe_side": ("none", *GESTURE_BUTTONS),
                   "swipe_lr": ("flick", "scroll", "none", *GESTURE_BUTTONS),
                   "pinch": ("zoom", "none", *GESTURE_BUTTONS)}
MOD_CODES = {"ctrl": e.KEY_LEFTCTRL, "alt": e.KEY_LEFTALT, "super": e.KEY_LEFTMETA,
             "shift": e.KEY_LEFTSHIFT}  # the one key per modifier TOGGLE is matched with
MOD_KEYS = {e.KEY_LEFTMETA: "super", e.KEY_RIGHTMETA: "super", e.KEY_LEFTSHIFT: "shift",
            e.KEY_RIGHTSHIFT: "shift", e.KEY_LEFTALT: "alt", e.KEY_RIGHTALT: "alt",
            e.KEY_LEFTCTRL: "ctrl", e.KEY_RIGHTCTRL: "ctrl"}
SHARE_CURSOR = True   # edge hand-off on (Super+Shift+S / the GUI toggles it)
THREE_FINGER_SWITCH = True  # three-finger swipe left/right: the device's App Switcher (SWIPE_SIDE_BUTTON)
# ...and while the PC has control, the previous / next device. Off: compositors use three-finger swipes
# for workspaces (Hyprland, GNOME, KDE), and the PC's touchpad is not grabbed, so one swipe would do both
THREE_FINGER_FROM_PC = False
SWITCH_SWIPE_MM = 25.0
SHAKE_TO_LOCATE = False  # shaking the PC pointer asks the GUI to enlarge it for a moment
SHAKE_TURNS = 4          # direction reversals...
SHAKE_WINDOW_S = 0.8     # ...within this long...
SHAKE_MIN_MM = 3.0       # ...each after at least this much travel
BATTERY_EVERY = 300      # seconds between battery readings of a connected device
NATURAL_SCROLL = True  # content follows the fingers, like on iOS itself
MOUSE_NATURAL_SCROLL = False  # the mouse wheel turned the other way: content follows the wheel
TAP_TO_CLICK = True   # a quick touchpad tap clicks (two fingers: secondary click)
TOUCHPAD_SPEED = 18.0 # mouse counts per mm of finger travel
SCROLL_STEP_MM = 2.5  # finger travel per wheel step (two-finger scroll, up/down and left/right)
GESTURE_LOCK_MM = 2.0  # two-finger travel before it is decided: scroll (and on which axis) or pinch
# Two-finger left/right swipe turns one page (Photos, the Home Screen, ...). A mouse cannot swipe on iOS,
# so it flicks: press, a short fast drag, let go - iOS pages on the speed - then the pointer goes back by
# the same reports, which pointer acceleration stretches exactly as much as the way out
SWIPE_FLICK = True    # False: two-finger left/right sends horizontal scroll instead (see the
                      # swipe_lr gesture action)
FLICK_MM = 4.0        # sideways finger travel that sets off the flick (once per swipe)
FLICK_REPORTS = 7     # the flick drags over this many reports...
FLICK_PAUSE_S = 0.04  # ...and, after letting go, comes back this long later
FLICK_ROOM = 1.3      # flick lengths of room needed towards the screen edge (acceleration stretches the
                      # flick; at the edge it would be cut short): the pointer is first moved away if not
# The flick's length, in mouse counts, by the Swipe Length setting the GUI can change (SWIPE_LENGTH, in
# OPTIONS below). A length that follows the fingers cannot work: the flick goes off after FLICK_MM, long
# before the swipe ends. Apps that page only on a long swipe (Instagram) need "long".
FLICK_LENGTHS = {"short": 96, "medium": 154, "long": 224}  # medium is 22 * FLICK_REPORTS, as before
SWIPE_LENGTH = "medium"


def flick_length():
    """Mouse counts the two-finger flick drags, by SWIPE_LENGTH."""
    return FLICK_LENGTHS.get(SWIPE_LENGTH, FLICK_LENGTHS["medium"])

PINCH_STEP_MM = 8.0  # change in finger spread per zoom step (two-finger pinch)
# Pinch out / in presses these. A mouse cannot pinch on iOS, so the default drives the Zoom magnifier
# (Accessibility > Zoom, with Keyboard Shortcuts on), which works in every app, Photos included.
ZOOM_IN = (0x0C, 46)  # Option+Cmd+=
ZOOM_OUT = (0x0C, 45)  # Option+Cmd+-
# Ctrl+pinch (Ctrl held on the keyboard) presses these instead: the app's own zoom (Safari, ...)
APP_ZOOM_IN = (0x08, 46)  # Cmd+=
APP_ZOOM_OUT = (0x08, 45)  # Cmd+-
CTRL = 0x11  # left or right Ctrl in the modifier byte
SWIPE_UP_HOME = True  # three-finger swipe up presses mouse button 4 (Home): the swipe_up gesture
                      # action, while the GUI has not set one
SWIPE_UP_MM = 15.0  # three-finger swipe up this far presses it
NEAR_RSSI = -45   # dBm: the phone counts as "very near" at or above this (smoothed)...
AWAY_RSSI = -55   # ...and as moved away again below this. Tune with the live panel.
GAUGE_MIN, GAUGE_MAX = -100, -25  # dBm range of the panel's gauge
REF_RSSI = -60.0  # dBm at 1 m, for the rough metre estimate: put the phone 1 m away and set this
PATH_LOSS = 2.2   # 2 = free space, 2.5-3.5 indoors
STALE_S = 4.0     # no advertisement for this long -> "phone asleep?"
HISTORY_S = 60
PANEL_ROWS = 3    # terminal rows pinned at the bottom for the proximity panel
PANEL_FPS = 5
ZONES = [(NEAR_RSSI, "VERY NEAR", 46), (-65, "NEAR", 42), (-78, "IN THE ROOM", 43), (-200, "FAR", 41)]
SPARK = " ▁▂▃▄▅▆▇█"
TAP_TIME = 0.2
TAP_TRAVEL_MM = 2.0
RECONNECT_EVERY = 10  # seconds between attempts to dial a paired device (doubling per failure, max 120)
DIAL_ONLY = False     # set when bluetoothd's input plugin holds the HID channels: devices cannot connect
DIAL_ONLY_MAX_S = 30  # to us then, so keep dialing them more often
MOUSE_INTERVAL = 0.008  # min seconds between motion reports; motion in between is merged
INPUT_RESCAN_S = 1.0    # seconds between looks for new input devices (a Bluetooth mouse waking up)

# Continuity-style hand-off between the PC pointer and the phone's pointer
HANDOFF_ZONES = ("top-left", "top", "top-right", "left", "right", "bottom-left", "bottom", "bottom-right")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
EDGE_MARGIN_PX = 1      # the pointer counts as "at the edge" within this many px of the last pixel
EDGE_PUSH_MM = 4.0      # finger travel against the edge before the pointer jumps to the phone
SWITCH_DELAY_S = 0.0    # ...and for at least this long
CORNER_FRAC = 0.3       # a corner zone covers this fraction of each edge next to the corner
HANDOFF_RSSI = -65      # with "only when near": the phone must be at least this strong (NEAR zone)
HANDOFF_STALE_S = 15    # ...and have been heard within this many seconds
DEFAULT_SCREEN = {"iPhone": (400, 860), "iPad": (1180, 820)}  # in mouse counts, for unknown models: phones
                                                                # upright, tablets on their side
COUNTS_PER_POINT = 1.0  # mouse counts that move the iOS pointer one point (at the default tracking speed)
GLIDE_STEP = 8          # counts per report when sliding the pointer into place after entering a device...
GLIDE_PAUSE_S = 0.08    # ...starting this long after the pin, so iOS doesn't count that burst as speed
PIN_EXTRA = 1200        # extra counts sent to pin the phone pointer against the screen edge
RETURN_PUSH = 200       # counts pushed past the phone's edge to bring the pointer back to the PC
RETURN_INSET_PX = 40    # where the pointer reappears, away from the PC screen edge
MOUSE_COUNTS_PER_MM = 40.0
MOUSE_SPEED = 1.0       # device pointer counts per mouse count before acceleration: 1:1, like the PC pointer
# Mouse acceleration, as libinput's default (adaptive, speed 0) does it on the PC: iOS adds none to a
# Bluetooth mouse, so without it a flick that crosses the PC screen barely moves the device pointer.
# Speeds in counts per ms (a 1000 DPI mouse), measured over MOUSE_ACCEL_WINDOW_S
MOUSE_ACCEL_THRESHOLD = 0.4  # 1:1 up to this speed (10 mm/s)...
MOUSE_ACCEL_INCLINE = 1.1    # ...then this much more gain per count/ms...
MOUSE_ACCEL_MAX = 1.0        # ...up to this much: 1.0 turns acceleration off (2.0 is libinput's)
MOUSE_ACCEL_SLOW = 0.0       # below this the gain drops towards 0.3, for precise small moves (0.07 in libinput)
MOUSE_ACCEL_WINDOW_S = 0.04  # long enough to span a few reports: Bluetooth delivers them in bursts
MOUSE_ACCEL_FIRST_S = 0.008  # the first report of a move counts as this much time of moving
MOUSE_PAUSE_S = 0.3     # a mouse at rest this long ends a push against the edge, like lifting the finger
                        # off the touchpad: nudges spread over minutes do not add up to a hand-off
CALIBRATE_RATE = 250    # counts per second while measuring a screen edge to edge
CALIBRATE_REACTION_S = 0.15  # subtract this much sweep: the time you need to press Stop
CALIBRATE_MAX_S = 25
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"  # the user's: we run as them
HIDE_CURSOR = True      # hide the PC pointer while a device has control: it would sit at the edge, visible
CURSOR_MARK = os.path.join(RUNTIME_DIR, "side-control-cursor-hidden")  # exists while the PC pointer is hidden by us
SCAN_WHILE_CONTROLLING = False  # keep the LE proximity scan on while a device has control? It shares the
                                # radio with the HID link, so it is paused for smoother pointer movement

SOCK_PATH = os.environ.get("SIDE_CONTROL_SOCK", os.path.join(RUNTIME_DIR, "side-control.sock"))  # GUI <-> this process


def set_gestures(gestures):
    """Gesture actions from the GUI (see OPTIONS): {gesture: action}. Each gesture keeps its
    default unless the action is one of its choices."""
    if not isinstance(gestures, dict):
        return dict(GESTURE_ACTIONS)
    return {**GESTURE_ACTIONS, **{g: a for g, a in gestures.items()
                                  if g in GESTURE_CHOICES and a in GESTURE_CHOICES[g]}}


# set_options from the GUI: option -> (global it sets, type)
OPTIONS = {"natural_scroll": ("NATURAL_SCROLL", bool), "mouse_natural_scroll": ("MOUSE_NATURAL_SCROLL", bool),
           "tap_to_click": ("TAP_TO_CLICK", bool),
           "share_cursor": ("SHARE_CURSOR", bool), "three_finger_switch": ("THREE_FINGER_SWITCH", bool),
           "shake_to_locate": ("SHAKE_TO_LOCATE", bool), "hide_cursor": ("HIDE_CURSOR", bool),
           "swipe_flick": ("SWIPE_FLICK", bool), "swipe_up_home": ("SWIPE_UP_HOME", bool),
           "gesture_actions": ("GESTURE_ACTIONS", set_gestures),
           "swipe_length": ("SWIPE_LENGTH", lambda v: v if v in FLICK_LENGTHS else "medium"),
           "switch_delay_ms": ("SWITCH_DELAY_S", lambda ms: max(0.0, min(2.0, float(ms) / 1000)))}


def set_shortcuts(shortcuts):
    """The bindings the GUI sends with set_options (control.py): {action: {"mods": [names],
    "key": evdev code}}. Each action it validly names replaces the default, the rest keep it, and
    HOTKEYS, TOGGLE and ACTIONS are rebuilt (read as config.NAME elsewhere, so a change reaches
    every module). Returns the actions accepted, for the log."""
    global HOTKEYS, TOGGLE, ACTIONS
    if not isinstance(shortcuts, dict):
        return "none accepted"
    given = {}
    for action, combo in shortcuts.items():
        try:
            mods, key = frozenset(combo["mods"]), int(combo["key"])
        except (TypeError, ValueError, KeyError):
            continue
        if not 1 <= key <= e.KEY_MAX or not mods <= set(MOD_KEYS.values()):
            continue
        if action in GESTURE_BUTTONS and not {"ctrl", "alt"} <= mods:
            continue  # the button chords stay Ctrl+Alt+<key>: only the key is free
        given[action] = (mods, key)
    merged = {**DEFAULT_SHORTCUTS, **given}
    HOTKEYS = [(*merged[action], action) for action in HOTKEY_ACTIONS]
    if merged["return_to_pc"] != DEFAULT_SHORTCUTS["return_to_pc"]:
        # Super+Esc always brings control back too: a new binding that does not work (or that the
        # compositor takes) must not leave the computer without its keyboard and touchpad
        HOTKEYS.append((*DEFAULT_SHORTCUTS["return_to_pc"], "return_to_pc"))
    mods, key = merged["cycle"]
    TOGGLE = {MOD_CODES[m] for m in mods} | {key}
    ACTIONS = {key: GESTURE_BUTTONS[action] for action, (mods, key) in merged.items()
               if action in GESTURE_BUTTONS}
    return ", ".join(sorted(given)) or "none accepted"
