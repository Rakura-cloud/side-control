# SPDX-License-Identifier: GPL-3.0-or-later
"""Small widgets and helpers shared by the pages and dialogs."""

from gi.repository import Adw, Gtk, Pango


def device_icon(form):
    return "sc-tablet-android-symbolic" if form == "tablet" else "sc-smartphone-symbolic"


def battery_icon(percent):
    return f"sc-battery-{5 if percent > 60 else 3 if percent > 30 else 1}-bar-symbolic"


class DeviceRow(Adw.PreferencesRow):
    """One device on the Devices page: OS and battery, setup guide, on/off, forget."""

    def __init__(self, window, address):
        super().__init__(activatable=False, css_classes=["device-row"])
        self.window, self.address = window, address
        box = Gtk.Box(spacing=14, margin_top=8, margin_bottom=8, margin_start=14, margin_end=12)
        self.icon = Gtk.Image(icon_name="sc-smartphone-symbolic", pixel_size=22)
        box.append(self.icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(self.name)
        info = Gtk.Box(spacing=8, css_classes=["dim-label", "caption"])
        self.os = Gtk.Label(xalign=0)
        info.append(self.os)
        self.battery = Gtk.Box(spacing=2)
        self.battery.append(Gtk.Label(label="·", margin_end=6))
        self.battery_icon = Gtk.Image(pixel_size=15)
        self.battery_label = Gtk.Label()
        self.battery.append(self.battery_icon)
        self.battery.append(self.battery_label)
        info.append(self.battery)
        text.append(info)
        box.append(text)
        guide_slot = Gtk.Box(width_request=34)
        self.guide = Gtk.Button(icon_name="sc-help-symbolic", tooltip_text="Setup Guide", valign=Gtk.Align.CENTER)
        for cls in ("flat", "circular", "icon-20"):
            self.guide.add_css_class(cls)
        self.guide.connect("clicked", lambda _b: window.show_guide(address))
        guide_slot.append(self.guide)
        box.append(guide_slot)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_switch)
        box.append(self.switch)
        forget = Gtk.Button(icon_name="sc-delete-symbolic", tooltip_text="Forget Device", valign=Gtk.Align.CENTER)
        for cls in ("flat", "icon-20"):
            forget.add_css_class(cls)
        forget.connect("clicked", lambda _b: window.ask_forget(address))
        box.append(forget)
        self.set_child(box)
        self._updating = False

    def update(self, dev, enabled):
        self.icon.set_from_icon_name(device_icon(dev["form"]))
        self.name.set_label(dev["name"])
        self.set_title(dev["name"])
        version = dev.get("software") or ""
        self.os.set_label(f"{dev['os']} {version}" if version[:1].isdigit() else dev["os"])
        battery = dev.get("battery")
        self.battery.set_visible(battery is not None)
        if battery is not None:
            self.battery_icon.set_from_icon_name(battery_icon(battery))
            self.battery_label.set_label(f"{battery}%")
        self.guide.set_visible(dev["os"] in ("iOS", "iPadOS"))
        self._updating = True
        self.switch.set_active(enabled)
        self._updating = False

    def on_switch(self, switch, _pspec):
        if not self._updating:
            self.window.device_cfg(self.address)["enabled"] = switch.get_active()
            self.window.layout_changed()


def step_row(number, title, subtitle=None, extra=None):
    """A numbered instruction in the setup guide."""
    row = Gtk.Box(spacing=14, margin_top=12, margin_bottom=12, margin_start=14, margin_end=14)
    row.append(Gtk.Label(label=str(number), css_classes=["step-number"], valign=Gtk.Align.START))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    text.append(Gtk.Label(label=title, xalign=0, wrap=True))
    if subtitle:
        text.append(Gtk.Label(label=subtitle, xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
    if extra:
        text.append(extra)
    row.append(text)
    return Gtk.ListBoxRow(activatable=False, child=row)


def crumbs(*parts):
    box = Adw.WrapBox(child_spacing=4, line_spacing=4)
    for i, part in enumerate(parts):
        if i:
            box.append(Gtk.Image(icon_name="sc-chevron-right-symbolic", css_classes=["dim-label"]))
        box.append(Gtk.Label(label=part, css_classes=["crumb"]))
    return box


def dialog_shell(title, width, body):
    """Adw.Dialog with a header bar and `body` in the usual margins."""
    dialog = Adw.Dialog(title=title, content_width=width)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    for side in ("start", "end", "bottom"):
        getattr(body, f"set_margin_{side}")(22)
    body.set_margin_top(4)
    view.set_content(body)
    dialog.set_child(view)
    return dialog
