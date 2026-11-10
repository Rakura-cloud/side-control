# AGENTS.md

Notes for coding agents (and humans) working on Side Control. Read [README.md](README.md) first for what
the app does.

## Layout

Two programs in one package, `side_control/`, sharing only `side_control/__init__.py` (`VERSION`) and
`side_control/monitors.py` (the monitors' outer edges, used by hand-off and the edge indicator alike).

| Path | What it is |
|---|---|
| `side-control`, `side-control-backend` | Launchers: put the checkout on `sys.path` and run `side_control.app` / `side_control.backend`. Files, not `python -m`, because the service starts the backend in another directory. |
| `side_control/app/` | The GTK 4 / libadwaita app. Runs as the user. |
| `side_control/backend/` | The backend: Bluetooth HID keyboard + mouse, input, hand-off, gestures. Runs as the user, through a system service. |
| `side_control/monitors.py` | Outer edges of the monitor layout and positions along them. No GTK, no compositor. |
| `side_control/data/` | Icons (app icons and Material Symbols Rounded as `sc-*-symbolic.svg`), `bluetooth-no-input.conf`, the systemd drop-in for "Own HID Profile", and `side-control@.service` and `side-control.rules`, the backend's service and the polkit rule that lets its user start it. |
| `tests/` | Tests that need no Bluetooth, input devices, display or root. |
| `tools/screenshots.py`, `tools/screenshots.sh` | Render every page against a fake backend (headless with the `.sh`). |
| `docs/screenshots/` | README images, made by `tools/screenshots.sh`. |
| `packaging/` | Desktop entry, and `appimage/build.sh` (quick-sharun, bundles everything down to glibc). |
| `.github/workflows/` | `ci.yml`: lint + tests. `release.yml`: on a `v*` tag, build the AppImage and publish a release; on a push to `dev`, rebuild the `dev` pre-release. |

### `side_control/app/`

| Module | What it is |
|---|---|
| `__main__.py` | Entry point. Re-executes itself with gtk4-layer-shell in `LD_PRELOAD` (it must load before libwayland). |
| `__init__.py` | Picks the GTK versions (`gi.require_version`) before any module imports `gi.repository`. |
| `application.py` | `App`: single instance, background mode, `main()`. |
| `window.py` | `Window`: navigation and reacting to the backend (`on_status`, `on_state`, `layout_changed`). Built from the mixins below. |
| `pages.py` | `PagesMixin`: the five pages. |
| `dialogs.py` | `DialogsMixin`: pairing, forgetting, the AssistiveTouch guide. |
| `bluetooth.py` | `BluetoothMixin`: adapter, name, Own HID Profile, installing (`pkexec`) and starting the backend's service. |
| `config.py` | Paths, defaults, the settings file, Launch at Login, `BACKEND_COMMAND` (differs inside the AppImage), `SERVICE`. |
| `system.py` | bluetoothd's plugins, whether the service is installed and the root commands that install it, adapter names, Hyprland IPC. |
| `backend_link.py` | `Backend`: the socket client, reconnects by itself. |
| `clipboard.py` | `ClipboardServer` behind Share Clipboard: the HTTP server the device's Shortcut talks to, and the setup code (address + token) its QR holds. |
| `geometry.py` | Arrangement maths, ported from the design. No GTK: tested in `tests/test_geometry.py`. |
| `qrcode.py` | The QR encoder (byte mode, error correction M, versions 1 to 10): tested in `tests/test_qrcode.py`. |
| `arrangement.py`, `widgets.py`, `overlay.py`, `tray.py` | The canvas, shared widgets, the edge indicator, the tray icon and menu. |
| `style.css` | The app's CSS. |

### `side_control/backend/`

| Module | What it is |
|---|---|
| `__main__.py`, `main.py` | Entry point; startup and the select loop. |
| `config.py` | Every tunable, with why it has its value. |
| `events.py` | `log()` and `EVENTS`, the queue the app reads. |
| `hid.py` | Report descriptor, SDP record, key tables, L2CAP `listen` / `dial`. |
| `bluez.py` | BlueZ over D-Bus: profile, pairing agent, paired and nearby devices. |
| `gatt.py` | Model, OS and battery from GATT over BR/EDR. |
| `devices.py` | `Phone` (one device and its connection), `Fleet`, screen sizes per model. |
| `proximity.py` | Signal strength from LE advertisements. |
| `compositor.py` | `Hyprland`: where the PC pointer is, the monitors, moving it. |
| `handoff.py` | `Handoff`: edge hand-off and the model of the device pointer. |
| `inputs.py` | Finding input devices, `Touchpad` finger tracking, `Shake`. |
| `bridge.py` | `Bridge`: routes keyboard and touchpad to the device in control; gestures and mouse reports. |
| `control.py` | `Control`: the app's socket. |

## Architecture

- The app talks to the backend through newline-delimited JSON on `$XDG_RUNTIME_DIR/side-control.sock`
  (`SIDE_CONTROL_SOCK` overrides it). See the `Control` docstring in `backend/control.py`.
- App → backend commands: `set_target`, `set_handoff`, `forget`, `set_options`, `discover`, `pair`,
  `quit`, `calibrate`, `action`.
- Backend → app: `state` snapshots (devices, target, sharing, nearby, ...) and events `log`, `rssi`,
  `paired`, `shake`, `open`, `calibration`.
- The backend runs as the user, never as root: `side-control@<uid>.service` gives it the `input` group
  and `CAP_NET_BIND_SERVICE` (listen on PSM 17/19), `CAP_NET_RAW` (`hid.disable_sniff`) and
  `CAP_NET_ADMIN` (`btmgmt class`). Without them it still runs, with less: dial-only, no inputs, sniff
  mode on. The app writes the command to run (`BACKEND_COMMAND --adapter ...`) to
  `/run/user/<uid>/side-control/backend` and runs `systemctl start --wait`, which a polkit rule allows
  without a password. If the unit in `/etc` differs from `data/side-control@.service`, it first installs
  both through `pkexec`, in the same prompt as an Own HID Profile change if there is one. Its log:
  `journalctl -u side-control@<uid>`.
- Backend options the app can change at runtime (`config.OPTIONS`) and `DIAL_ONLY` are read as
  `config.NAME`, never imported by name, so a change reaches every module. Other constants are imported
  by name.
- iOS gives no pointer position back. `Handoff` keeps a model of the device pointer (`vx`, `vy` in
  mouse counts, screen size from `SCREEN_POINTS` by model), pinned on entry and clamped at the edges.
  Motion the backend makes up itself (the entry glide, swipe flicks) goes through `Bridge.steps` and
  must not reach `Handoff.phone_motion`. When a change moves the device pointer, keep that model right.
- If bluetoothd's input plugin holds PSM 17/19, the backend runs "dial-only" (`config.DIAL_ONLY`): it
  connects to devices instead of accepting their connections.
- Inside the AppImage, the service runs `<AppImage> side-control-backend` (the backend outlives the
  app's mount of it), and root cannot read the user's mount, so the files `pkexec` installs are written
  from their text.

## Design

The UI follows a Claude Design mockup one to one: pages, rows, wording and icons (Material Symbols
Rounded, as `data/icons/sc-*`). Don't add UI that isn't in it; put new tunables in
`backend/config.py` instead. Behaviour fixes are welcome, visual changes need the maintainer's approval.

## Code style

- Write code that reads like the code around it: same naming, same comment density, same idioms.
- One responsibility per module; keep GTK out of modules that don't draw (like `geometry.py`), so they
  can be tested.
- Tunables are constants in `backend/config.py`, each with a comment saying what it does and why.
  Comments explain why, not what.
- Standard library plus PyGObject, pycairo, dbus-python and python-evdev only. Ask before adding a
  dependency.
- Every source file starts with `# SPDX-License-Identifier: GPL-3.0-or-later`.

## Checks

Run before every commit:

```sh
ruff check --select F,E9 .              # undefined names, syntax errors
python -m unittest discover tests       # no hardware needed
tools/screenshots.sh /tmp/shots         # UI against a fake backend; look at the PNGs
```

`tools/screenshots.sh` needs `mutter`; it also prints every command the window sent, which is a quick
way to check a UI change reaches the backend correctly. Regenerate `docs/screenshots/` with it when the
UI changes. `packaging/appimage/build.sh` builds the AppImage locally (Arch Linux, no root).

## Releasing

Changes flow feature branch → `dev` → `main` → tag. Open pull requests against `dev`; merge `dev` into
`main` when it is ready for a release.

There are two channels, each with its own AppImage update information (zsync), so an AppImage only
updates within its channel:

- **Dev:** every push to `dev` makes `release.yml` build the AppImage as version
  `<VERSION>-dev.<short commit>`, move the `dev` tag to that commit and replace the assets of the `dev`
  GitHub pre-release (never "Latest"). Nothing else feeds it: merges to `main` stay unreleased until
  they are tagged.
- **Stable:** a `v*` tag on `main`:
  1. Set `VERSION` in `side_control/__init__.py`, commit.
  2. Tag and push: `git tag v<VERSION> && git push origin v<VERSION>`.
  3. `release.yml` checks the tag matches `VERSION`, runs the tests, builds the AppImage and publishes
     the GitHub release with it.

`CHANNEL=dev packaging/appimage/build.sh` builds a dev AppImage locally. The build writes its full
version to `side_control/version` in the AppImage, which Settings and the sidebar show.

## Things to know

- Nothing here can be fully tested without real hardware: Bluetooth, iOS behaviour and input
  grabbing need a person with an iPhone or iPad. Say what you verified and what you couldn't.
- The backend grabs the keyboard and touchpad while a device has the cursor. A bug there can leave the
  computer without input: Super+Esc and Ctrl+Alt+P bring control back; stopping the backend
  (`systemctl stop side-control@<uid>`) always does.
- Don't start the backend, restart bluetoothd or change `/etc` on someone's machine without asking.
- Never commit `~/.config/side-control/settings.json` or other personal data (device names, Bluetooth
  addresses).
