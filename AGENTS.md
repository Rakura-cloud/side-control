# AGENTS.md

Notes for coding agents (and humans) working on Side Control. Read [README.md](README.md) first for what
the app does.

## Layout

| Path | What it is |
|---|---|
| `side_control.py` | GTK 4 / libadwaita app: window, tray icon (StatusNotifierItem), edge indicator overlay (gtk4-layer-shell). Runs as the user. |
| `side_control_backend.py` | Root backend: Bluetooth HID keyboard + mouse (BlueZ, L2CAP PSM 17/19), evdev input, edge hand-off, gestures, control socket. |
| `bluetooth-no-input.conf` | systemd drop-in that starts bluetoothd with `-P input` ("Own HID Profile" in Settings). |
| `icons/` | App icons (`io.github.sidecontrol.SideControl*`, `side-control-*`) and Material Symbols Rounded as `sc-*-symbolic.svg`. |
| `tests/` | Backend logic tests that need no Bluetooth, input devices or root. |
| `tools/screenshots.py`, `tools/screenshots.sh` | Render every page against a fake backend (headless with the `.sh`). |
| `docs/screenshots/` | README images, made by `tools/screenshots.sh`. |

## Architecture

- The app talks to the backend through newline-delimited JSON on `/run/side-control.sock`
  (`SIDE_CONTROL_SOCK` overrides it). See the `Control` class docstring in the backend.
- App → backend commands: `set_target`, `set_handoff`, `forget`, `set_options`, `discover`, `pair`,
  `quit`, `calibrate`, `action`.
- Backend → app: `state` snapshots (devices, target, sharing, nearby, ...) and events `log`, `rssi`,
  `paired`, `shake`, `open`, `calibration`.
- The app starts the backend with `pkexec` when it is not running. The backend can also be run on its
  own with `sudo python side_control_backend.py [--adapter hciX]`.
- iOS gives no pointer position back. `Handoff` keeps a model of the device pointer (`vx`, `vy` in
  mouse counts, screen size from `SCREEN_POINTS` by model), pinned on entry and clamped at the edges.
  Motion the backend makes up itself (the entry glide, swipe flicks) goes through `Bridge.steps` and
  must not reach `Handoff.phone_motion`. When a change moves the device pointer, keep that model right.
- If bluetoothd's input plugin holds PSM 17/19, the backend runs "dial-only" (`DIAL_ONLY`): it connects
  to devices instead of accepting their connections.

## Design

The UI follows a Claude Design mockup one to one: pages, rows, wording and icons (Material Symbols
Rounded, as `icons/sc-*`). Don't add UI that isn't in it; put new tunables in the backend's constants
instead. Behaviour fixes are welcome, visual changes need the maintainer's approval.

## Code style

- Write code that reads like the code around it: same naming, same comment density, same idioms.
- Tunables are module constants at the top of the backend, each with a comment saying what it does and
  why. Comments explain why, not what.
- Standard library plus PyGObject, pycairo, dbus-python and python-evdev only. Ask before adding a
  dependency.
- Every source file starts with `# SPDX-License-Identifier: GPL-3.0-or-later`.

## Checks

Run before every commit:

```sh
ruff check --select F,E9 .              # undefined names, syntax errors
python -m unittest discover tests       # backend logic, no hardware needed
tools/screenshots.sh /tmp/shots         # UI against a fake backend; look at the PNGs
```

`tools/screenshots.sh` needs `mutter`; it also prints every command the window sent, which is a quick
way to check a UI change reaches the backend correctly. Regenerate `docs/screenshots/` with it when the
UI changes.

## Things to know

- Nothing here can be fully tested without real hardware: Bluetooth, iOS behaviour and input
  grabbing need a person with an iPhone or iPad. Say what you verified and what you couldn't.
- The backend runs as root and grabs the keyboard and touchpad while a device has the cursor. A bug
  there can leave the computer without input: Super+Esc and Ctrl+Alt+P bring control back; stopping the
  backend (`sudo pkill -f side_control_backend.py`) always does.
- Don't start the backend, restart bluetoothd or change `/etc` on someone's machine without asking.
- Never commit `~/.config/side-control/settings.json` or other personal data (device names, Bluetooth
  addresses).
