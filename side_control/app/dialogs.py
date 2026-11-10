# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing, forgetting and the AssistiveTouch guide."""

import json

from gi.repository import Adw, GLib, Gtk

from . import clipboard, config
from .widgets import crumbs, device_icon, dialog_shell, qr_view, step_row


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
        searching = Gtk.Box(spacing=10, css_classes=["dim-label"])
        searching.append(Adw.Spinner())
        searching.append(Gtk.Label(label="Searching for nearby devices…"))
        body.append(searching)
        self.nearby_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.nearby_list.set_placeholder(Gtk.Label(label="No other devices found", margin_top=18,
                                                   margin_bottom=18, css_classes=["dim-label"]))
        body.append(self.nearby_list)
        body.append(Gtk.Label(label="Keep Bluetooth settings open on your phone or tablet so it can be found. "
                                    "iOS devices pair as a Bluetooth pointer — no app needed.",
                              xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
        self.pair_dialog = dialog_shell("Pair a Device", 420, body)
        self.nearby = None
        self.pair_dialog.connect("closed", self.on_pair_closed)
        self.backend.send(cmd="discover", on=True)
        self.pair_dialog.present(self)

    def on_pair_closed(self, _dialog):
        self.pair_dialog = None
        self.backend.send(cmd="discover", on=False)

    def open_pair(self):
        self.present()
        self.show_page(0)
        self.show_pair()

    def show_nearby(self, nearby, pairing):
        key = (json.dumps(nearby), pairing)
        if self.pair_dialog is None or key == self.nearby:
            return
        self.nearby = key
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

    def on_paired(self, msg):
        if not msg["ok"]:
            self.toasts.add_toast(Adw.Toast(title=f"Pairing failed: {msg.get('error') or 'unknown error'}"))
            return
        if self.pair_dialog is not None:
            self.pair_dialog.close()
        self.pending_guide = msg["address"]

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
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        add = Gtk.Button(label="Add the Side Control Shortcut")
        done = Gtk.Button(label="Done", css_classes=["suggested-action"])
        buttons.append(add)
        buttons.append(done)
        body.append(buttons)
        dialog = dialog_shell(f"Set Up {kind}", 460, body)
        add.connect("clicked", lambda _b: (dialog.close(), self.show_add_to_iphone(address)))
        done.connect("clicked", lambda _b: dialog.close())
        dialog.present(self)

    def show_add_to_iphone(self, address=None):
        """Two QR codes for the device's camera: the Side Control Shortcut (an iCloud link), then the
        setup code with this computer's address and token (docs/shortcuts.md)."""
        dev = self.devs.get(address or "", {})
        kind = "iPad" if dev.get("form") == "tablet" else "iPhone"
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.append(Gtk.Label(label=f"Both codes scan with the {kind}’s camera. The first adds the "
                                   "Side Control Shortcut, the second tells it where this computer is.",
                              xalign=0, wrap=True, css_classes=["dim-label"]))
        code = clipboard.setup_string(clipboard.setup_url(), clipboard.new_token(self.settings))
        first = (qr_view(config.SHORTCUT_URL) if config.SHORTCUT_URL else
                 Gtk.Label(label="The Shortcut isn’t published yet", margin_top=12, margin_bottom=12,
                           css_classes=["dim-label"]))
        codes = Gtk.Box(spacing=18, homogeneous=True)  # both codes at once, so both scan in a row
        for number, title, extra in (
                (1, f"Scan with the {kind}’s camera, then tap Add Shortcut", first),
                (2, "Run the Shortcut once; it opens the camera — scan this code", qr_view(code))):
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            margin_top=12, margin_bottom=12, margin_start=14, margin_end=14)
            head = Gtk.Box(spacing=14)
            head.append(Gtk.Label(label=str(number), css_classes=["step-number"], valign=Gtk.Align.START))
            head.append(Gtk.Label(label=title, xalign=0, wrap=True, hexpand=True))
            inner.append(head)
            inner.append(extra)
            card = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            card.append(Gtk.ListBoxRow(activatable=False, child=inner))
            codes.append(card)
        body.append(codes)
        body.append(Gtk.Label(label="The first run asks iOS to allow Local Network access — tap Allow. "
                                    "The Shortcut only runs when it is started on the device (Action "
                                    "Button, Back Tap, Siri, share sheet or widget), not by itself.",
                              xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
        other = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        row = Adw.ActionRow(title="Type Clipboard on Device",
                            subtitle="Types the computer’s clipboard into the text field selected on "
                                     "the device (US layout, 256 characters)")
        button = Gtk.Button(label="Type", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.type_clipboard(address))
        row.add_suffix(button)
        other.append(row)
        body.append(other)
        done = Gtk.Button(label="Done", css_classes=["suggested-action"], halign=Gtk.Align.END)
        body.append(done)
        dialog = dialog_shell(f"Add to {kind}", 620, body)
        done.connect("clicked", lambda _b: dialog.close())
        dialog.present(self)
