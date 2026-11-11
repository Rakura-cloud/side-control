# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing, forgetting and the AssistiveTouch guide."""

import json

from gi.repository import Adw, GLib, Gtk

from .widgets import crumbs, device_icon, dialog_shell, step_row


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
        ipad = dev.get("form") == "tablet" or dev.get("os") == "iPadOS"  # unknown: the iPhone guide
        kind = "iPad" if ipad else "iPhone"
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        if ipad:
            intro = ("iPadOS has its own pointer, so an iPad works without AssistiveTouch. These settings are "
                     "optional; the extra buttons need AssistiveTouch on.")
        else:
            intro = "An iPhone shows a cursor only when AssistiveTouch is on. Do this once on the iPhone."
        body.append(Gtk.Label(label=f"{intro} iOS gives no way to check whether AssistiveTouch is on, so this "
                                    "guide can't verify it.", xalign=0, wrap=True,
                              css_classes=["dim-label"]))
        steps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        number = 1
        if not ipad:
            steps.append(step_row(number, "Open Settings and go to",
                                  extra=crumbs("Accessibility", "Touch", "AssistiveTouch")))
            number += 1
            steps.append(step_row(number, "Turn on AssistiveTouch",
                                  "A round cursor appears on the screen. Optional: turn off Always Show Menu "
                                  "to hide the floating button"))
            number += 1
        buttons = Adw.WrapBox(child_spacing=8, line_spacing=8)
        for key, button in (("h", 4), ("c", 5), ("n", 6), ("s", 7)):
            shortcut = Gtk.Button(label=f"Ctrl+Alt+{key.upper()}", tooltip_text=f"Mouse button {button}")
            shortcut.connect("clicked", lambda _b, k=key: self.press_button(address, k))
            buttons.append(shortcut)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        extra.append(crumbs("AssistiveTouch", "Devices", "(this computer)", "Customize Additional Buttons"))
        extra.append(buttons)
        steps.append(step_row(number, "Optional: add the extra mouse buttons",
                              "When iOS asks for a button, press a shortcut here, then choose its action (mouse buttons 4–7):",
                              extra=extra))
        number += 1
        steps.append(step_row(number, "Optional: turn on pinch zoom",
                              "Turn on Zoom and Keyboard Shortcuts; a two-finger pinch then zooms",
                              extra=crumbs("Accessibility", "Zoom")))
        body.append(steps)
        done = Gtk.Button(label="Done", css_classes=["suggested-action"], halign=Gtk.Align.END)
        body.append(done)
        dialog = dialog_shell(f"Set Up {kind}", 460, body)
        done.connect("clicked", lambda _b: dialog.close())
        dialog.present(self)

    def press_button(self, address, key):
        """The guide's extra-button step: press a mouse button on the device while iOS waits for one."""
        dev = self.devs.get(address, {})
        if not dev.get("connected"):
            self.toasts.add_toast(Adw.Toast(title=f"Connect {dev.get('name', 'the device')} first"))
            return
        self.backend.send(cmd="action", address=address, key=key)
