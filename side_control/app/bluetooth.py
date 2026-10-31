# SPDX-License-Identifier: GPL-3.0-or-later
"""Bluetooth settings, and starting the root backend."""

from gi.repository import Adw, GLib, Gio

from .config import BACKEND_COMMAND, DROPIN_DIR, HID_DROPIN, NO_INPUT_CONF, save_settings
from .system import adapter_label, hid_owned, no_input_dropins


class BluetoothMixin:
    """Bluetooth settings and the backend process; part of Window."""

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
        (it holds the HID channels when it can), so: stop it, change bluetoothd, start it again - all
        in one pkexec."""
        if self._updating_hid:
            return
        if row.get_active():  # (written from its text: root cannot read inside the AppImage)
            with open(NO_INPUT_CONF) as f:
                script, files = 'mkdir -p "${2%/*}" && printf %s "$1" > "$2" && chmod 644 "$2"', [f.read(), HID_DROPIN]
            self.settings["hid_banner_dismissed"] = False
            save_settings(self.settings)
        else:
            files = no_input_dropins()
            if not files:
                self.toasts.add_toast(Adw.Toast(title=f"bluetoothd gets -P input from outside {DROPIN_DIR}"))
                self.refresh_hid()
                return
            script = 'rm -f -- "$@"'
        if self.alive:
            self.hid_change = (script, files)
            self.backend.send(cmd="quit")  # on_status runs it once the backend is gone
        else:
            self.run_hid_change(script, files)

    def run_hid_change(self, script, files):
        self.hid_change = None
        self.started_backend = True
        backend = [*BACKEND_COMMAND, *(["--adapter", self.settings["adapter"]] if self.settings["adapter"] else [])]
        # sh -c SCRIPT sh N ARG1..ARGN BACKEND...: the change gets the first N arguments, then the rest
        # (the backend's command) replaces the shell
        script = ('change() { ' + script + '; }; n=$1; shift; refs=; i=1; '
                  r'while [ $i -le $n ]; do refs="$refs \"\${$i}\""; i=$((i+1)); done; '
                  'eval "change $refs" && shift "$n" && systemctl daemon-reload && systemctl restart bluetooth'
                  ' && sleep 1 && exec "$@"')
        argv = ["pkexec", "sh", "-c", script, "sh", str(len(files)), *files, *backend]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not run pkexec: {err.message}"))
            self.refresh_hid()
            return

        def done(proc, result):
            self._backend_exited(proc, result)
            self.refresh_hid()
        proc.communicate_utf8_async(None, None, done)
        GLib.timeout_add(3000, self.refresh_hid)

    # --- starting the root backend ---

    def start_backend(self):
        self.started_backend = True
        argv = ["pkexec", *BACKEND_COMMAND]
        if self.settings["adapter"]:
            argv += ["--adapter", self.settings["adapter"]]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not start the backend: {err.message}"))
            return
        proc.communicate_utf8_async(None, None, self._backend_exited)

    def _backend_exited(self, proc, result):
        try:
            _ok, _out, err = proc.communicate_utf8_finish(result)
        except GLib.Error as error:
            err = error.message
        if proc.get_exit_status() != 0 and not self.alive and not self.restart_backend:
            last = (err or "").strip().splitlines()[-1:] or [f"exit status {proc.get_exit_status()}"]
            self.toasts.add_toast(Adw.Toast(title=f"Backend stopped: {last[0]}"[:160]))
