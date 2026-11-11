# SPDX-License-Identifier: GPL-3.0-or-later
"""Bluetooth settings, and starting the backend's service."""

import os
import shlex

from gi.repository import Adw, GLib, Gio

from .config import (
    BACKEND_COMMAND,
    BACKEND_SCRIPT,
    DROPIN_DIR,
    HID_DROPIN,
    NO_INPUT_CONF,
    SERVICE,
    save_settings,
)
from .system import (
    adapter_label,
    hid_owned,
    install_file,
    last_log_line,
    no_input_dropins,
    service_installed,
    setup_script,
)


class BluetoothMixin:
    """Bluetooth settings and the backend's service; part of Window."""

    # --- Bluetooth adapter ---

    def _connect_adapter(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
            objects = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                                    "GetManagedObjects", None, None, Gio.DBusCallFlags.NONE, 2000, None)
            paths = sorted(p for p, ifaces in objects.unpack()[0].items() if "org.bluez.Adapter1" in ifaces)
        except GLib.Error:
            paths = []
        self.adapters = [p.rsplit("/", 1)[-1] for p in paths]
        self._updating_adapter = True
        for hci in self.adapters:
            self.adapter_names.append(adapter_label(hci))
        chosen = self.adapters.index(self.settings["adapter"]) if self.settings["adapter"] in self.adapters else 0
        if self.adapters:
            self.adapter_row.set_selected(chosen)
        self._updating_adapter = False
        if not paths:
            return
        self.adapter = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.bluez", paths[chosen],
                                              "org.bluez.Adapter1", None)
        self.adapter.connect("g-properties-changed", lambda *_: self.show_alias())
        self.show_alias()

    def show_alias(self):
        alias = self.adapter.get_cached_property("Alias") if self.adapter else None
        if alias is not None and not self.name_row.has_focus():
            self.name_row.set_text(alias.unpack())

    def on_device_name(self, row):
        name = row.get_text().strip()
        if name:
            self.settings["device_name"] = name
            save_settings(self.settings)
            self.backend.send(cmd="set_options", device_name=name)

    def on_adapter(self, row, _pspec):
        if self._updating_adapter or row.get_selected() >= len(self.adapters):
            return
        self.settings["adapter"] = self.adapters[row.get_selected()]
        save_settings(self.settings)
        if self.alive:
            self.restart_backend = True
            self.backend.send(cmd="quit")  # on_status starts it again on the new adapter

    # --- HID profile (bluetoothd -P input) ---

    def refresh_hid(self):
        owned = hid_owned()
        self._updating_hid = True
        self.hid_row.set_active(bool(owned))
        self.hid_row.set_sensitive(owned is not None)
        self._updating_hid = False
        self.hid_banner.set_revealed(bool(owned) and not self.settings["hid_banner_dismissed"])
        return GLib.SOURCE_REMOVE

    def dismiss_hid_banner(self, _banner):
        self.settings["hid_banner_dismissed"] = True
        save_settings(self.settings)
        self.hid_banner.set_revealed(False)

    def on_hid(self, row, _pspec):
        """Owning HID means bluetoothd runs with -P input. Either way the backend must not run meanwhile
        (it holds the HID channels when it can), so: stop it, change bluetoothd (one pkexec), start it
        again."""
        if self._updating_hid:
            return
        if row.get_active():  # (written from its text: root cannot read inside the AppImage)
            with open(NO_INPUT_CONF) as f:
                change = install_file(f.read(), HID_DROPIN)
            self.settings["hid_banner_dismissed"] = False
            save_settings(self.settings)
        else:
            files = no_input_dropins()
            if not files:
                self.toasts.add_toast(Adw.Toast(title=f"bluetoothd gets -P input from outside {DROPIN_DIR}"))
                self.refresh_hid()
                return
            change = "rm -f -- " + shlex.join(files)
        change += " && systemctl daemon-reload && systemctl restart bluetooth && sleep 1"
        if self.alive:
            self.hid_change = change
            self.backend.send(cmd="quit")  # on_status runs it once the backend is gone
        else:
            self.run_as_root(change)

    # --- the backend's service ---

    def start_backend(self):
        self.run_as_root(None)

    def run_as_root(self, change):
        """Run the shell command `change` through pkexec, then start the backend. The backend's service
        is installed (or updated) in the same pkexec if it needs to be: one password prompt, and none
        at all once it is installed and there is nothing to change."""
        self.hid_change = None
        self.started_backend = True
        steps = ([] if service_installed() else [setup_script()]) + ([change] if change else [])
        if not steps:
            self.start_service()
            return
        try:
            proc = Gio.Subprocess.new(["pkexec", "sh", "-c", " && ".join(steps)], Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not run pkexec: {err.message}"))
            self.refresh_hid()
            return

        def done(proc, result):
            self.refresh_hid()
            if proc.get_exit_status() == 0:
                proc.communicate_utf8_finish(result)
                self.start_service()
            else:
                self._backend_exited(proc, result)
        proc.communicate_utf8_async(None, None, done)
        GLib.timeout_add(3000, self.refresh_hid)

    def start_service(self):
        """Write the command the service runs (this checkout or AppImage, on the chosen adapter), and start
        it. systemctl --wait returns when the backend stops, as the backend's own process would."""
        command = [*BACKEND_COMMAND, *(["--adapter", self.settings["adapter"]] if self.settings["adapter"] else [])]
        try:
            os.makedirs(os.path.dirname(BACKEND_SCRIPT), exist_ok=True)
            with open(os.open(BACKEND_SCRIPT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700), "w") as f:
                f.write(f"#!/bin/sh\n# what {SERVICE} runs, written by Side Control\nexec {shlex.join(command)}\n")
            proc = Gio.Subprocess.new(["systemctl", "start", "--wait", "--no-ask-password", SERVICE],
                                      Gio.SubprocessFlags.STDERR_PIPE)
        except (OSError, GLib.Error) as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not start the backend: {err}"))
            return
        proc.communicate_utf8_async(None, None, self._backend_exited)

    def _backend_exited(self, proc, result):
        try:
            _ok, _out, err = proc.communicate_utf8_finish(result)
        except GLib.Error as error:
            err = error.message
        if proc.get_exit_status() != 0 and not self.alive and not self.restart_backend \
                and not self.restarting:
            # pkexec and systemctl say why they failed; why the backend stopped is in its log
            last = (err or "").strip().splitlines()[-1:] or [last_log_line(SERVICE) or f"see journalctl -u {SERVICE}"]
            self.toasts.add_toast(Adw.Toast(title=f"Backend stopped: {last[0]}"[:160]))
