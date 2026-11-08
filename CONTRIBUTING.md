# Contributing to Side Control

Thanks for helping! Bug reports, device reports and code are all welcome.

## Reporting a bug

Open an [issue](https://github.com/Rakura-cloud/side-control/issues) with:

- your distribution, desktop or compositor (e.g. Hyprland), and Bluetooth adapter;
- the device: model and iOS / iPadOS / Android version;
- what you did, what you expected, and what happened;
- the backend's log, if you can: reproduce the problem, then paste what
  `journalctl -u "side-control@$(id -u)" -b` prints.

Reports of devices and setups that work are useful too: say so in an issue.

## Working on the code

```sh
git clone https://github.com/Rakura-cloud/side-control.git
cd side-control
./side-control
```

Install the dependencies listed in the [README](README.md#requirements). The backend runs as the
service `side-control@<uid>.service`, which runs whatever `./side-control` last started it with: your
checkout. To work on the backend, restart it after a change (no password needed) and follow its log:

```sh
systemctl restart "side-control@$(id -u)"
journalctl -f -u "side-control@$(id -u)"
```

[AGENTS.md](AGENTS.md) maps out the code: which module does what, and how the app and the backend
talk.

**Careful:** the backend takes over your keyboard and touchpad while a device has the cursor. If it gets
stuck, Super+Esc or Ctrl+Alt+P bring control back, and `systemctl stop "side-control@$(id -u)"` from
another terminal or session always does.

## Before you open a pull request

```sh
ruff check --select F,E9 .
python -m unittest discover tests
tools/screenshots.sh /tmp/shots    # if you changed the UI; needs mutter
```

- Keep pull requests focused: one fix or feature each.
- Match the style of the code around your change; see [AGENTS.md](AGENTS.md) for conventions.
- The UI follows a fixed design; open an issue before changing how it looks.
- Say how you tested, including on which devices, and what you couldn't test.
- If you changed what the UI shows, regenerate `docs/screenshots/` with `tools/screenshots.sh`.

## Releases

Maintainers: set `VERSION` in `side_control/__init__.py`, then push a tag `v<VERSION>`. GitHub
Actions builds the AppImage and publishes the release (see [AGENTS.md](AGENTS.md#releasing)). To try
the AppImage build locally, run `packaging/appimage/build.sh` on Arch Linux.

## License

Side Control is licensed under the [GPL-3.0-or-later](LICENSE). By contributing, you agree your
contributions are licensed the same way.
