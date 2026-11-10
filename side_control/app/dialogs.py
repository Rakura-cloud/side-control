# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing, forgetting and the AssistiveTouch guide."""

import json

from gi.repository import Adw, GLib, Gtk

from .widgets import crumbs, device_icon, dialog_shell, step_row


PAIR_STEPS = {  # what the backend reports about a pairing, in the pair dialog's status line
    "searching": "Searching for {name}…",
    "found": "Found {name}.",
    "confirm": "Confirm the pairing on your phone or tablet.",
    "paired": "Paired with {name}. Connecting…",
}
PAIR_ERRORS = {  # the backend's error codes in plain English, each with a Retry
    "not_found": "Couldn’t find {name}. Move it closer and keep Bluetooth settings open on it, then try again.",
    "rejected": "The pairing was cancelled on {name}. Confirm it when the device asks, then try again.",
    "timeout": "{name} did not answer in time. Keep Bluetooth settings open on it, then try again.",
    "exists": "{name} is already paired with this computer. If it does not connect, forget it on both sides and pair again.",
    "failed": "Pairing with {name} failed. Try again.",
}


class DialogsMixin:
    """The window's dialogs; part of Window."""

    def ask_forget(self, address):
        dialog = Adw.AlertDialog(heading=f"Forget {self.devs.get(address, {}).get('name', 'Device')}?",
                                 body="You’ll need to pair it again to move the cursor onto it.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget")
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "forget" and self.forget(address))
        dialog.present(self)

    def forget(self, address):
        self.backend.send(cmd="forget", address=address)
        self.settings["devices"].pop(address, None)
        if address in self.rows:
            self.remove_device(address)
        self.layout_changed()
        self.devices_stack.set_visible_child_name("list" if self.rows else "empty")

    # --- pairing ---

    def show_pair(self):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        status = Gtk.Box(spacing=10, css_classes=["dim-label"])
        self.pair_spinner = Adw.Spinner()
        self.pair_label = Gtk.Label(label="Searching for nearby devices…", xalign=0, wrap=True, hexpand=True)
        self.pair_retry = Gtk.Button(label="Retry", valign=Gtk.Align.CENTER, visible=False)
        self.pair_retry.connect("clicked", self.on_pair_retry)
        for widget in (self.pair_spinner, self.pair_label, self.pair_retry):
            status.append(widget)
        body.append(status)
        self.nearby_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.nearby_list.set_placeholder(Gtk.Label(label="No other devices found", margin_top=18,
                                                   margin_bottom=18, css_classes=["dim-label"]))
        body.append(self.nearby_list)
        body.append(Gtk.Label(label="Keep Bluetooth settings open on your phone or tablet so it can be found. "
                                    "iOS devices pair as a Bluetooth pointer — no app needed.",
                              xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
        self.pair_dialog = dialog_shell("Pair a Device", 420, body)
        self.nearby, self.nearby_names = None, {}
        self.pair_dialog.connect("closed", self.on_pair_closed)
        self.backend.send(cmd="discover", on=True)
        self.backend.send(cmd="set_options", discoverable=True)  # findable while pairing
        self.pair_dialog.present(self)

    def on_pair_closed(self, _dialog):
        self.pair_dialog = None
        self.backend.send(cmd="discover", on=False)
        self.backend.send(cmd="set_options", discoverable=self.settings["visible"])  # back to the setting

    def open_pair(self):
        self.present()
        self.show_page(0)
        self.show_pair()

    def show_nearby(self, nearby, pairing):
        key = (json.dumps(nearby), pairing)
        if self.pair_dialog is None or key == self.nearby:
            return
        self.nearby = key
        self.nearby_names = {dev["address"]: dev["name"] for dev in nearby}
        self.nearby_list.remove_all()
        for dev in nearby:
            row = Adw.ActionRow(title=GLib.markup_escape_text(dev["name"]), subtitle=dev["os"])
            row.add_prefix(Gtk.Image(icon_name=device_icon(dev["form"]), pixel_size=22))
            if pairing == dev["address"]:
                row.add_suffix(Adw.Spinner(margin_start=12, margin_end=12))
            else:
                button = Gtk.Button(label="Pair", valign=Gtk.Align.CENTER, css_classes=["suggested-action"],
                                    sensitive=pairing is None)
                button.connect("clicked", lambda _b, a=dev["address"]: self.backend.send(cmd="pair", address=a))
                row.add_suffix(button)
            self.nearby_list.append(row)

    # --- pairing status ---

    def pair_name(self, address):
        return self.nearby_names.get(address) or self.devs.get(address, {}).get("name") or "the device"

    def show_pair_step(self, step, address):
        """The step the backend reports: searching, found, confirm on the device, paired."""
        if self.pair_dialog is None or step not in PAIR_STEPS:
            return
        self.pair_address = address
        self.pair_spinner.set_visible(step != "paired")
        self.pair_retry.set_visible(False)
        self.pair_label.set_label(PAIR_STEPS[step].format(name=self.pair_name(address)))

    def show_pair_error(self, address, code, _message):
        if self.pair_dialog is None:
            return
        self.pair_address = address
        self.pair_spinner.set_visible(False)
        self.pair_retry.set_visible(True)
        self.pair_label.set_label(PAIR_ERRORS.get(code, PAIR_ERRORS["failed"]).format(name=self.pair_name(address)))

    def on_pair_retry(self, _button):
        self.show_pair_step("searching", self.pair_address)
        self.backend.send(cmd="pair", address=self.pair_address)

    def on_pairing(self, msg):
        self.show_pair_step(msg["step"], msg["address"])

    def on_paired(self, msg):
        if not msg["ok"]:
            if self.pair_dialog is not None:
                self.show_pair_error(msg["address"], msg.get("error_code"), msg.get("error"))
            else:
                self.toasts.add_toast(Adw.Toast(title=f"Pairing failed: {msg.get('error') or 'unknown error'}"))
            return
        self.pending_guide = msg["address"]
        if self.pair_dialog is not None:
            self.show_pair_step("paired", msg["address"])
            GLib.timeout_add(600, self.close_pair)  # let "Paired" be seen first

    def close_pair(self):
        if self.pair_dialog is not None:
            self.pair_dialog.close()
        self.open_pending_guide()
        return False

    def open_pending_guide(self):
        """The AssistiveTouch guide of the device just paired, once it appears in the device list."""
        if self.pending_guide in self.devs:
            address, self.pending_guide = self.pending_guide, None
            if self.devs[address]["os"] in ("iOS", "iPadOS"):
                self.show_guide(address)

    def show_guide(self, address):
        dev = self.devs.get(address, {})
        kind = "iPad" if dev.get("form") == "tablet" else "iPhone"
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.append(Gtk.Label(label="iPhone and iPad only show a cursor when AssistiveTouch is on. "
                                    "Do this once on the device:", xalign=0, wrap=True, css_classes=["dim-label"]))
        steps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        steps.append(step_row(1, "Open Settings and go to", extra=crumbs("Accessibility", "Touch", "AssistiveTouch")))
        steps.append(step_row(2, "Turn on AssistiveTouch", "A round cursor appears on the screen"))
        steps.append(step_row(3, "Optional: turn off Always Show Menu",
                              "Hides the floating button while keeping the cursor"))
        body.append(steps)
        done = Gtk.Button(label="Done", css_classes=["suggested-action"], halign=Gtk.Align.END)
        body.append(done)
        dialog = dialog_shell(f"Set Up {kind}", 460, body)
        done.connect("clicked", lambda _b: dialog.close())
        dialog.present(self)
