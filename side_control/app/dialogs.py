# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing, forgetting, the AssistiveTouch guide, and setting a shortcut."""

import json

from evdev import ecodes as e
from gi.repository import Adw, Gdk, GLib, Gtk

from .config import SHORTCUTS, save_settings, shortcut_clash, shortcut_problem
from .system import hyprland_binding
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

    # --- shortcuts ---

    def ask_shortcut(self, action):
        """"Press the new shortcut": captures a combination for one of the shortcuts on the
        Gestures page, warns when it clashes, and applies it on Set (Esc cancels)."""
        title = SHORTCUTS[action][0]
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.append(Gtk.Label(label=f"Press the new shortcut for “{title}”, then click Set Shortcut. "
                                    "Escape cancels.", xalign=0, wrap=True, css_classes=["dim-label"]))
        self.captured = None
        self.capture_preview = Adw.ShortcutLabel(accelerator=self.shortcut_accel(action),
                                                 halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                                 margin_top=16, margin_bottom=16, focusable=True)
        body.append(self.capture_preview)
        self.capture_banner = Adw.Banner()
        body.append(self.capture_banner)
        self.capture_reset = Gtk.Button(label="Reset to Default", css_classes=["flat"],
                                        visible=bool(self.settings["shortcuts"].get(action)))
        self.capture_reset.connect("clicked", self.on_shortcut_reset, action)
        cancel = Gtk.Button(label="Cancel", css_classes=["flat"])
        cancel.connect("clicked", lambda _b: self.capture_dialog.close())
        okay = Gtk.Button(label="Set Shortcut", css_classes=["suggested-action"], sensitive=False)
        okay.connect("clicked", self.on_shortcut_set, action)
        buttons = Gtk.Box(spacing=12)
        buttons.append(self.capture_reset)
        spacer = Gtk.Box(hexpand=True)
        buttons.append(spacer)
        buttons.append(cancel)
        buttons.append(okay)
        body.append(buttons)
        self.capture_dialog = dialog = dialog_shell("Set Shortcut", 440, body)
        ctrl = Gtk.EventControllerKey()
        body.add_controller(ctrl)
        ctrl.connect("key-pressed", self.on_capture_key, action, okay)
        dialog.connect("closed", self.on_capture_closed)
        dialog.present(self)
        self.capture_preview.grab_focus()

    def on_capture_key(self, _ctrl, keyval, keycode, state, action, okay):
        """A key while capturing: Esc cancels, a modifier only is waited on, anything else is the
        new shortcut (previewed, with its clashes), until Set applies it."""
        if keyval == Gdk.KEY_Escape:
            self.capture_dialog.close()
            return True
        if not keycode or Gdk.keyval_is_modifier(keyval):
            return True
        mods = state & Gtk.accelerator_get_default_mod_mask()
        key = keycode - 8  # evdev counts keys as the kernel does: the hardware code minus 8
        if not 1 <= key <= e.KEY_MAX:
            return True
        names = [name for bit, name in ((Gdk.ModifierType.CONTROL_MASK, "ctrl"),
                                        (Gdk.ModifierType.ALT_MASK, "alt"),
                                        (Gdk.ModifierType.SHIFT_MASK, "shift"),
                                        (Gdk.ModifierType.SUPER_MASK, "super")) if mods & bit]
        self.captured = {"accel": Gtk.accelerator_name(keyval, mods), "mods": names, "key": key}
        self.capture_preview.set_accelerator(self.captured["accel"])
        problem = shortcut_problem(action, names, key)
        if problem:
            self.capture_banner.set_title(problem)
            self.capture_banner.set_revealed(True)
            okay.set_sensitive(False)
            return True
        warnings = []
        clash = shortcut_clash(action, names, key, self.settings["shortcuts"])
        if clash:
            warnings.append(f"Also used by “{clash}”.")
        if hyprland_binding(Gdk.keyval_name(keyval), names):
            warnings.append("Also bound in Hyprland: it would happen there too.")
        self.capture_banner.set_title(" ".join(warnings))
        self.capture_banner.set_revealed(bool(warnings))
        okay.set_sensitive(True)
        return True

    def on_shortcut_set(self, _button, action):
        if not self.captured:
            return
        self.settings["shortcuts"][action] = self.captured
        self.save_shortcuts(action)
        self.capture_dialog.close()

    def on_shortcut_reset(self, _button, action):
        self.settings["shortcuts"].pop(action, None)
        self.save_shortcuts(action)
        self.capture_dialog.close()

    def save_shortcuts(self, action):
        """Save a shortcut change, send the bindings to the backend, refresh the page's label."""
        save_settings(self.settings)
        self.backend.send(cmd="set_options", shortcuts=self.settings["shortcuts"])
        self.shortcut_labels[action].set_accelerator(self.shortcut_accel(action))

    def on_capture_closed(self, _dialog):
        self.capture_dialog = None
