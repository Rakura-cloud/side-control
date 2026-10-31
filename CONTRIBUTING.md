# Contributing to Side Control

Thanks for helping! Bug reports, device reports and code are all welcome.

## Reporting a bug

Open an [issue](https://github.com/Rakura-cloud/side-control/issues) with:

- your distribution, desktop or compositor (e.g. Hyprland), and Bluetooth adapter;
- the device: model and iOS / iPadOS / Android version;
- what you did, what you expected, and what happened;
- the backend's output, if you can: stop it, run `sudo python side_control_backend.py` in a terminal,
  reproduce the problem, and paste what it printed.

Reports of devices and setups that work are useful too: say so in an issue.

## Working on the code

```sh
git clone https://github.com/Rakura-cloud/side-control.git
cd side-control
python side_control.py
```

Install the dependencies listed in the [README](README.md#requirements). To work on the backend, stop
the running one and start yours in a terminal, so you see its log:

```sh
sudo pkill -f side_control_backend.py
sudo python side_control_backend.py
```

Then start `python side_control.py` in another terminal; it connects to your backend.

**Careful:** the backend takes over your keyboard and touchpad while a device has the cursor. If it gets
stuck, Super+Esc or Ctrl+Alt+P bring control back, and `sudo pkill -f side_control_backend.py` from
another session always does.

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

## License

Side Control is licensed under the [GPL-3.0-or-later](LICENSE). By contributing, you agree your
contributions are licensed the same way.
