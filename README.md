# Side Control

Use your Linux laptop's keyboard and touchpad on your iPhone and iPad. Push the cursor off the edge of
your screen and it appears on the phone next to it, push it back and it returns. Universal Control,
for Linux.

![Devices](docs/screenshots/devices.png)

Side Control makes your computer a Bluetooth keyboard and mouse for each paired device. No app runs on
the phone or tablet: it sees an ordinary Bluetooth keyboard and mouse.

> **Status: early release (0.1.0).** Developed and tested on Arch Linux with Hyprland, an iPhone and an
> iPad. Other distributions and compositors should mostly work, see [Requirements](#requirements).

## Supported devices

| Device | Status | Notes |
|---|---|---|
| iPhone | Supported | Needs **AssistiveTouch** turned on (Settings → Accessibility → Touch → AssistiveTouch): iOS only shows a pointer with it. |
| iPad | Supported | Works as is: iPadOS has its own pointer. AssistiveTouch is only needed for the extra mouse buttons (Home, Control Center, …). |
| Android phone / tablet | Untested | Pairs and should work as a Bluetooth keyboard and mouse. Reports are welcome. |

## Features

- **Edge hand-off.** Arrange your devices around your screen; the cursor crosses where you put them,
  entering at the same height it left, and comes back the same way. With several monitors, a side
  means the outer edges of every monitor on that side; edges between two monitors never hand off.
- **Keyboard and touchpad on the device.** Typing, clicks, tap to click, two-finger scrolling, and
  two-finger left/right swipes that turn the page (Photos, the Home Screen, ...).
- **Gestures.** On a device, three-finger swipe up goes Home and left/right opens the App Switcher. On
  the computer, three-finger swipes are left to your desktop (workspaces); `THREE_FINGER_FROM_PC` in
  `backend/config.py` makes left/right jump to the next device there.
- **Keyboard shortcuts.** Send the cursor to the next device or back, lock it to the current screen,
  turn sharing on and off.
- **Edge indicator.** A line on your screen edge shows where each device is reached. Choose its style (Subtle, Standard, Bold, Glow) and colour.
- **Cursor out of the way.** While a device has control, the computer's cursor is hidden (Hyprland); it
  comes back where you return. Turn it off in Appearance.
- **Tray icon.** Shows where the cursor is and switches between devices (StatusNotifierItem, e.g. Waybar).
- **Pairing and device info.** Pair from the app; see each device's OS version and battery.

| | |
|---|---|
| ![Layout](docs/screenshots/layout.png) | ![Gestures & Keybindings](docs/screenshots/gestures.png) |
| ![Appearance](docs/screenshots/appearance.png) | ![Settings](docs/screenshots/settings.png) |

## Install

### AppImage

Download `Side_Control-<version>-x86_64.AppImage` from the
[latest release](https://github.com/Rakura-cloud/side-control/releases/latest), then:

```sh
chmod +x Side_Control-*-x86_64.AppImage
./Side_Control-*-x86_64.AppImage
```

It bundles Python, GTK and everything else it needs, so it runs on any x86_64 distribution. Your system
still needs what Side Control talks to: BlueZ, polkit, and a compositor that says where the cursor is
(Hyprland, for edge hand-off; see [Desktops](#desktops)).

#### Dev build

To try fixes before they are released, download `Side_Control-<version>-dev.<commit>-x86_64.AppImage`
from the [dev pre-release](https://github.com/Rakura-cloud/side-control/releases/tag/dev), rebuilt on
every change to the `dev` branch. Settings shows which commit it is.

> **Warning:** dev builds are untested between releases and can break, including the input takeover
> (<kbd>Super</kbd>+<kbd>Esc</kbd> or <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> bring control back).
> Use the [latest release](https://github.com/Rakura-cloud/side-control/releases/latest) for everyday
> use.

### From source

Install the [requirements](#requirements), then:

```sh
git clone https://github.com/Rakura-cloud/side-control.git
cd side-control
./side-control
```

### First run

Side Control asks for your password once, to install the service its backend runs as (see
[Permissions](#permissions)); after that it starts without asking. Turn on **Settings → Launch at
Login** to start it with your session; it then stays in the tray.

Then, for each device:

1. Click **+** in Side Control, then open **Settings → Bluetooth** on the phone or tablet and pick it
   from the list in Side Control.
2. On an iPhone, turn on **Settings → Accessibility → Touch → AssistiveTouch** (see
   [Supported devices](#supported-devices)). You can turn off **Always Show Menu** to hide its floating
   button.
3. In Side Control's **Layout** page, drag the device to the side of your screen where it sits.

### Bluetooth mice and keyboards on the computer

The two Bluetooth channels a keyboard or mouse uses can only be held by one program, either Linux's
own Bluetooth input support or Side Control. **Settings → Own HID Profile** chooses:

- **Off** (Bluetooth mice and keyboards work): Side Control connects to your devices itself, within
  10 to 30 seconds after they come in range.
- **On:** your devices can also connect to Side Control by themselves, but classic Bluetooth mice and
  keyboards can't connect to this computer. It installs
  [`bluetooth-no-input.conf`](side_control/data/bluetooth-no-input.conf) as a drop-in for
  `bluetooth.service` (bluetoothd runs with `-P input`).

Bluetooth LE mice and keyboards (most recent ones, e.g. Logitech MX) use another plugin and work either
way. Mice and keyboards that connect after Side Control started, or reconnect after sleeping, are picked
up within a second.

## Requirements

Needed by the AppImage too:

- **Linux with BlueZ** and a Bluetooth adapter (Bluetooth Classic, which nearly all have).
- **systemd and polkit** (`pkexec`): the backend runs as a system service, as you, and Side Control
  installs that service once through `pkexec` (see [Permissions](#permissions)).
- **A Wayland desktop**: Hyprland, niri, KDE or GNOME, each with less than the one before (see
  [Desktops](#desktops)).

Bundled in the AppImage, needed when running from source:

- **Python 3** with PyGObject, pycairo, dbus-python and python-evdev.
- **GTK 4 and libadwaita.** Tested with GTK 4.24 and libadwaita 1.10; older versions may lack widgets
  the app uses.
- **gtk4-layer-shell** (optional) for the on-screen edge indicator.

On Arch Linux:

```sh
sudo pacman -S python python-gobject python-cairo python-dbus python-evdev gtk4 libadwaita \
    bluez bluez-utils polkit gtk4-layer-shell
```

### Desktops

What works on each desktop:

| | Hyprland | niri | KDE | GNOME |
|---|---|---|---|---|
| Switching by shortcut, gesture or from the app | yes | yes | yes | yes |
| Monitors (the hand-off geometry) | yes | yes | yes | yes |
| Edge hand-off (push the cursor off the edge) | yes | no | no | no |
| The cursor comes back at the edge | yes | no | no | no |
| PC pointer hidden while a device has control | yes | no | no | no |
| Edge indicator (follows the cursor) | yes | edges only¹ | edges only¹ | no² |
| Shake to Locate (enlarges the cursor) | yes | no | no | no |

Hyprland is the only compositor that says where the cursor is and moves it on request (its IPC
socket). niri's IPC has no pointer position and no way to move the pointer, and KDE and GNOME would
need a KWin script or a GNOME Shell extension for either, so there you switch with the shortcuts or
from the app instead of by pushing the cursor off an edge. The monitor layout comes from each
compositor (Hyprland's IPC, niri's IPC, `kscreen-doctor -j` on KDE, Mutter's
`org.gnome.Mutter.DisplayConfig` on GNOME).

¹ The indicator draws the edges, but "Near" mode needs the cursor position to follow it. ² It also
needs gtk4-layer-shell, which GNOME does not have.

Verified on Hyprland only so far. The niri, KDE and GNOME code paths are tested against fake replies
of their documented interfaces, but have not been run on those desktops yet.

## Usage

| Shortcut | Action |
|---|---|
| Push the cursor off a screen edge | Move to the device on that side |
| <kbd>Super</kbd>+<kbd>→</kbd> | Send the cursor to the next device |
| <kbd>Super</kbd>+<kbd>Esc</kbd> | Bring the cursor back to the computer |
| <kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> | Turn cursor sharing on or off |
| <kbd>Scroll Lock</kbd> | Lock the cursor to the screen it is on |
| <kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd> | Open Side Control |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> | Computer → first device → next device → … → computer |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>H</kbd> / <kbd>C</kbd> / <kbd>N</kbd> / <kbd>S</kbd> | Mouse buttons 4–7 on the device: assign them under AssistiveTouch → Devices → (this computer) → Customize Additional Buttons (e.g. Home, Control Center, Notification Center) |

| Touchpad, while a device has the cursor | Action |
|---|---|
| Two fingers up/down | Scroll |
| Two fingers left/right | Turn one page |
| Pinch | Zoom, through the Zoom shortcuts of Accessibility → Zoom (turn on Zoom and its Keyboard Shortcuts) |
| <kbd>Ctrl</kbd> + pinch | Zoom in the app (<kbd>⌘</kbd><kbd>+</kbd> / <kbd>⌘</kbd><kbd>−</kbd>, e.g. Safari) |
| Three fingers up | Home (mouse button 4, assign it in AssistiveTouch as above) |
| Three fingers left/right | App Switcher (mouse button 8: swipe once, then assign it in AssistiveTouch as above) |

A mouse works on the device too, one to one (no acceleration; `MOUSE_ACCEL_*` in
[`config.py`](side_control/backend/config.py) turn it on). Its wheel and tilt wheel scroll (**Natural
Mouse Scrolling** turns them the other way), and its back / forward buttons are mouse buttons 4 and 5
(assign them in AssistiveTouch as above).

## Known limitations

- **The cursor position on the device is estimated.** Bluetooth mice only send movement, and iOS never
  tells where its pointer is. Side Control keeps its own estimate using each model's screen size, and
  re-anchors it whenever the pointer is pushed against an edge. iOS pointer acceleration can still make
  it drift, so the cursor may come back to the computer a little early or late.
- **Swipes and pinches are imitated.** A mouse cannot swipe or pinch on iOS, so a swipe is a short,
  quick drag (one page per swipe; apps that need a longer swipe may not react) and pinching uses the
  Accessibility Zoom shortcuts.
- **Share Clipboard** is a saved setting only: a Bluetooth keyboard and mouse cannot carry a clipboard.
- **Shake to Locate** enlarges the computer's cursor (Hyprland only); the device's pointer is not
  affected.
- Shortcuts reach your compositor too, since the keyboard is only taken over while a device has the
  cursor.

## How it works

Side Control is two programs that talk JSON over the unix socket `$XDG_RUNTIME_DIR/side-control.sock`:

- **The app** ([`side_control/app/`](side_control/app/), started by `./side-control`) is the GTK window,
  tray icon and edge indicator, running as you. Settings are stored in
  `~/.config/side-control/settings.json`.
- **The backend** ([`side_control/backend/`](side_control/backend/), started by
  `./side-control-backend`) runs as you, through a system service (see [Permissions](#permissions)).
  It registers a Bluetooth HID keyboard and mouse with BlueZ, reads your keyboard and touchpad with
  evdev, takes them over while a device has the cursor, and sends HID reports to it.

[AGENTS.md](AGENTS.md) maps out every module.

### Permissions

The backend does not run as root. It runs as you, as `side-control@<your uid>.service`
([`side-control@.service`](side_control/data/side-control@.service)), with only:

- the `input` group, to read your keyboard, touchpad and mice, and take them over while a device has
  the cursor;
- `CAP_NET_BIND_SERVICE`, to listen on the two Bluetooth HID channels, so devices can connect to it;
- `CAP_NET_RAW`, to keep the link to a device out of Bluetooth's power-save mode, which delays every
  report;
- `CAP_NET_ADMIN`, to make the adapter show up as a keyboard and mouse (`btmgmt class`).

The service runs the command Side Control writes to `/run/user/<uid>/side-control/backend`, so it
follows your checkout or AppImage wherever you keep it. It stops when you log out. A polkit rule,
[`side-control.rules`](side_control/data/side-control.rules), lets you start, stop and restart your
own instance without a password; nobody else's, and nothing else. Side Control installs both the first
time it starts, with one `pkexec` prompt, and again if a new version changes them.

The service gives these rights to code you can edit, so anything running as you could use them too.

To undo it:

```sh
sudo systemctl stop "side-control@$(id -u)"
sudo rm /etc/systemd/system/side-control@.service /etc/polkit-1/rules.d/50-side-control.rules
sudo systemctl daemon-reload
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bugs and ideas: [issues](https://github.com/Rakura-cloud/side-control/issues).

## License

[GPL-3.0-or-later](LICENSE).

The `sc-*` icons in [`side_control/data/icons/`](side_control/data/icons/) are
[Material Symbols](https://github.com/google/material-design-icons) by Google, under the
[Apache License 2.0](side_control/data/icons/LICENSE-Material-Symbols).
