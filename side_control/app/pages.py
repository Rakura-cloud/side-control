# SPDX-License-Identifier: GPL-3.0-or-later
"""The five pages of the window."""

import os

from gi.repository import Adw, Gtk

from .arrangement import ArrangementView
from .. import VERSION
from .config import AUTOSTART_PATH, ISSUES_URL, save_settings, set_autostart


SHORTCUTS = [("Toggle Cursor Sharing", "<Super><Shift>s"), ("Send Cursor to Next Device", "<Super>Right"),
             ("Return Cursor to Computer", "<Super>Escape"), ("Lock Cursor to Current Screen", "Scroll_Lock"),
             ("Open Side Control", "<Super><Alt>d")]
INDICATORS = [("always", "Always Visible", "A thin line stays on every connected edge"),
              ("near", "When Cursor Is Near", "The edge lights up as the cursor approaches"),
              ("never", "Hidden", "No indicator; just push past the edge")]
class PagesMixin:
    """Builds the pages; part of Window."""

    # --- pages ---

    def _devices_page(self):
        self.devices_stack = Gtk.Stack()
        pair = Gtk.Button(label="Pair Device", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        pair.connect("clicked", lambda _b: self.show_pair())
        self.devices_stack.add_named(Adw.StatusPage(
            icon_name="sc-power-symbolic", title="No Devices", child=pair,
            description="Pair an iPhone, iPad or Android device over Bluetooth to move your cursor onto it."),
            "empty")
        page = Adw.PreferencesPage()
        self.devices_group = Adw.PreferencesGroup()
        page.add(self.devices_group)
        self.devices_stack.add_named(page, "list")
        self.devices_stack.set_visible_child_name("empty")
        return self.devices_stack

    def _layout_page(self):
        page = Adw.PreferencesPage()
        arrangement = Adw.PreferencesGroup(
            title="Arrangement",
            description="Drag a device to the edge of your screen where the cursor should cross over")
        self.arrangement = ArrangementView(self.layout_changed)
        arrangement.add(self.arrangement)
        page.add(arrangement)

        edges = Adw.PreferencesGroup(title="Edge Switching")
        edges.add(self.stepper_row("switch_delay_ms", "Switch Delay",
                                   "How long to push against an edge before crossing", 0, 1000, 50, "ms",
                                   lambda: self.send_option("switch_delay_ms")))
        edges.add(self.switch_row("corner_dead_zones", "Corner Dead Zones",
                                  "Ignore screen corners so hot corners keep working", self.layout_changed))
        edges.add(self.switch_row("match_speed", "Match Pointer Speed",
                                  "Scale movement to each device’s screen density", self.layout_changed))
        page.add(edges)
        return page

    def _gestures_page(self):
        page = Adw.PreferencesPage()
        keys = Adw.PreferencesGroup(title="Keyboard Shortcuts")
        for title, accel in SHORTCUTS:
            row = Adw.ActionRow(title=title, use_markup=False)
            row.add_suffix(Adw.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
            keys.add(row)
        page.add(keys)

        gestures = Adw.PreferencesGroup(title="Gestures")
        for key, icon, title, subtitle in (
                ("three_finger_switch", "sc-swipe-symbolic", "Three-Finger Swipe",
                 "Swipe on the touchpad to jump to the next device"),
                ("shake_to_locate", "sc-ads-click-symbolic", "Shake to Locate",
                 "Briefly enlarge the cursor on whichever screen it’s on"),
                ("tap_to_click", "sc-touch-app-symbolic", "Tap to Click on Device",
                 "Touchpad taps become taps on the phone or tablet"),
                ("mouse_natural_scroll", "sc-mouse-symbolic", "Natural Mouse Scrolling",
                 "The mouse wheel moves the content the other way on the device")):
            row = self.switch_row(key, title, subtitle, lambda k=key: self.send_option(k))
            row.add_prefix(Gtk.Image(icon_name=icon, pixel_size=20))
            gestures.add(row)
        page.add(gestures)
        return page

    def _appearance_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Edge Indicator",
                                     description="Show where the cursor can cross to another device")
        first = None
        for key, title, subtitle in INDICATORS:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=self.settings["indicator"] == key)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", self.on_indicator, key)
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
        page.add(group)

        options = Adw.PreferencesGroup()
        self.within_row = self.stepper_row("indicator_within", "Show When Within",
                                           "Distance from the edge before the indicator appears",
                                           8, 200, 8, "px", self.update_overlay)
        self.within_row.set_sensitive(self.settings["indicator"] == "near")
        options.add(self.within_row)
        options.add(self.switch_row("indicator_names", "Show Device Name",
                                    "Label the indicator with the device it leads to", self.update_overlay))
        page.add(options)
        return page

    def _settings_page(self):
        page = Adw.PreferencesPage()
        cursor = Adw.PreferencesGroup(title="Cursor")
        self.share_row = self.switch_row("share_cursor", "Share Cursor",
                                         "Move past a screen edge to control the device there",
                                         lambda: (self.send_option("share_cursor"), self.layout_changed()))
        cursor.add(self.share_row)
        cursor.add(self.switch_row("share_clipboard", "Share Clipboard",
                                   "Copied text follows the cursor between devices"))
        page.add(cursor)

        general = Adw.PreferencesGroup(title="General")
        self.login_row = Adw.SwitchRow(title="Launch at Login", active=os.path.exists(AUTOSTART_PATH))
        self.login_row.connect("notify::active", self.on_login)
        general.add(self.login_row)
        general.add(self.switch_row("run_in_background", "Run in Background",
                                    "Keep devices connected when the window is closed"))
        page.add(general)

        bluetooth = Adw.PreferencesGroup(title="Bluetooth")
        self.adapter_names = Gtk.StringList()
        self.adapter_row = Adw.ComboRow(title="Adapter", use_subtitle=True, model=self.adapter_names)
        self.adapter_row.connect("notify::selected", self.on_adapter)
        self.name_row = Adw.EntryRow(title="Device Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_device_name)
        self.visible_row = self.switch_row("visible", "Visible to Nearby Devices",
                                           "Required while pairing a new phone or tablet",
                                           lambda: self.send_option("visible"))
        self.hid_row = Adw.SwitchRow(
            title="Own HID Profile",
            subtitle="Disables bluetoothd’s input plugin so phones and tablets can also connect to Side "
                     "Control by themselves. Bluetooth mice and keyboards can’t connect to this computer "
                     "while on.")
        self.hid_row.connect("notify::active", self.on_hid)
        for row in (self.adapter_row, self.name_row, self.visible_row, self.hid_row):
            bluetooth.add(row)
        page.add(bluetooth)

        about = Adw.PreferencesGroup(title="About")
        version = Adw.ActionRow(title="Version")
        version.add_suffix(Gtk.Label(label=VERSION, css_classes=["dim-label"]))
        report = Adw.ActionRow(title="Report an Issue", activatable=True)
        report.add_suffix(Gtk.Image(icon_name="sc-open-in-new-symbolic"))
        report.connect("activated", self.on_report)
        about.add(version)
        about.add(report)
        page.add(about)
        return page

    def switch_row(self, key, title, subtitle=None, changed=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=self.settings[key])
        row.updating = False

        def toggled(row, _pspec):
            if row.updating:
                return
            self.settings[key] = row.get_active()
            save_settings(self.settings)
            if changed:
                changed()
        row.connect("notify::active", toggled)
        return row

    def stepper_row(self, key, title, subtitle, lower, upper, step, unit, changed):
        row = Adw.SpinRow(title=title, subtitle=subtitle, numeric=False,
                          adjustment=Gtk.Adjustment(value=self.settings[key], lower=lower, upper=upper,
                                                    step_increment=step, page_increment=step))

        def output(row):
            row.set_text(f"{int(row.get_value())} {unit}")
            return True

        def parse(row):
            try:
                return True, float(row.get_text().split()[0])
            except (ValueError, IndexError):
                return False, row.get_value()

        def value_changed(row, _pspec):
            self.settings[key] = int(row.get_value())
            save_settings(self.settings)
            changed()
        row.connect("output", output)
        row.connect("input", parse)
        row.connect("notify::value", value_changed)
        return row

    def on_indicator(self, check, key):
        if check.get_active():
            self.settings["indicator"] = key
            save_settings(self.settings)
            self.within_row.set_sensitive(key == "near")
            self.update_overlay()

    def on_login(self, row, _pspec):
        try:
            set_autostart(row.get_active())
        except OSError as err:
            self.toasts.add_toast(Adw.Toast(title=f"Could not change autostart: {err.strerror}"))

    def on_report(self, _row):
        if ISSUES_URL:
            Gtk.UriLauncher(uri=ISSUES_URL).launch(self, None, None, None)
        else:
            self.toasts.add_toast(Adw.Toast(title="No issue tracker set (ISSUES_URL in side_control/app/config.py)"))
