# SPDX-License-Identifier: GPL-3.0-or-later
"""The main window: navigation, and reacting to the backend."""

import math
import os

from gi.repository import Adw, GLib, Gdk, Gio, Gtk

from .backend_link import Backend
from .bluetooth import BluetoothMixin
from .. import VERSION
from .config import (
    APP_ICON,
    BACKEND_OPTIONS,
    CSS_PATH,
    DEVICE_DEFAULTS,
    INDICATOR_STYLES,
    ICONS_DIR,
   
    load_settings,
    save_settings,
)
from .dialogs import DialogsMixin
from .geometry import BASE_SCREEN, OPPOSITE, edge_segments
from .overlay import EdgeOverlay
from .pages import PagesMixin
from .system import hyprland
from .tray import Tray
from .widgets import DeviceRow


PAGES = [("devices", "Devices", "sc-devices-symbolic"),
         ("layout", "Layout", "sc-space-dashboard-symbolic"),
         ("gestures", "Gestures & Keybindings", "sc-gesture-symbolic"),
         ("appearance", "Appearance", "sc-palette-symbolic"),
         ("settings", "Settings", "sc-settings-symbolic")]


class Window(PagesMixin, DialogsMixin, BluetoothMixin, Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Side Control", default_width=1040, default_height=700)
        self.settings = load_settings()
        self.alive = False
        self.resync = True
        self.started_backend = False  # tried pkexec once already
        self.restart_backend = False  # waiting for the backend to quit, then start it on another adapter
        self.hid_change = None  # (script, files) to run once the backend has quit: see on_hid
        self.devs = {}  # address -> last device dict from the backend
        self.rows = {}
        self.target = None
        self.pair_dialog = None
        self.nearby = None
        self.pending_guide = None  # address just paired: its setup guide opens once it appears
        self.adapter = None
        self.overlay = EdgeOverlay()
        self.spans = {}

        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(ICONS_DIR)
        Gtk.Window.set_default_icon_name(APP_ICON)
        provider = Gtk.CssProvider()
        provider.load_from_path(CSS_PATH)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.split = Adw.NavigationSplitView(min_sidebar_width=248, max_sidebar_width=248)
        self.split.set_sidebar(self._sidebar())
        self.split.set_content(self._content())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.connect("close-request", self.on_close)

        self._connect_adapter()
        self.refresh_hid()
        self.locked = False
        self.tray = Tray(self)
        self.backend = Backend(self.on_message, self.on_status)
        self.tray.update()

    # --- frame ---

    def _sidebar(self):
        """The pages on top, Settings pinned to the bottom above the version."""
        self.sidebar = Adw.Sidebar(vexpand=True, css_classes=["sc-sidebar"])
        self.sidebar_bottom = Adw.Sidebar(css_classes=["sc-sidebar"])
        for sidebar, pages in ((self.sidebar, PAGES[:-1]), (self.sidebar_bottom, PAGES[-1:])):
            section = Adw.SidebarSection()
            for _name, title, icon in pages:
                section.append(Adw.SidebarItem(title=title, icon_name=icon))
            sidebar.append(section)
        self.sidebar.connect("activated", lambda _s, index: self.show_page(index))
        self.sidebar_bottom.connect("activated", lambda _s, _index: self.show_page(len(PAGES) - 1))
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(Gtk.Separator())
        bottom.append(self.sidebar_bottom)
        bottom.append(Gtk.Label(label=VERSION, xalign=0, css_classes=["dim-label", "caption", "version"]))
        view = Adw.ToolbarView(content=self.sidebar)
        view.add_top_bar(Adw.HeaderBar())
        view.add_bottom_bar(bottom)
        return Adw.NavigationPage(title="Side Control", child=view)

    def _content(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._devices_page(), "devices")
        self.stack.add_named(self._layout_page(), "layout")
        self.stack.add_named(self._gestures_page(), "gestures")
        self.stack.add_named(self._appearance_page(), "appearance")
        self.stack.add_named(self._settings_page(), "settings")

        header = Adw.HeaderBar()
        self.add_button = Gtk.Button(icon_name="sc-add-symbolic", tooltip_text="Pair Device")
        self.add_button.add_css_class("icon-20")
        self.add_button.connect("clicked", lambda _b: self.show_pair())
        header.pack_start(self.add_button)
        self.hid_banner = Adw.Banner(
            title="System Bluetooth input is disabled — mice and keyboards can’t connect to this "
                  "computer while Side Control owns HID.", button_label="Dismiss")
        self.hid_banner.connect("button-clicked", self.dismiss_hid_banner)
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(self.hid_banner)
        self.content_page = Adw.NavigationPage(title="Devices", child=view)
        self.sidebar.set_selected(0)
        return self.content_page

    def show_page(self, index):
        name, title, _icon = PAGES[index]
        self.stack.set_visible_child_name(name)
        self.content_page.set_title(title)
        self.add_button.set_visible(name == "devices")
        settings = index == len(PAGES) - 1
        for sidebar, selected in ((self.sidebar, Gtk.INVALID_LIST_POSITION if settings else index),
                                  (self.sidebar_bottom, 0 if settings else Gtk.INVALID_LIST_POSITION)):
            if sidebar.get_selected() != selected:
                sidebar.set_selected(selected)
        self.split.set_show_content(True)

    # --- devices ---

    def device_cfg(self, address):
        cfg = self.settings["devices"].get(address)
        if cfg is None:
            cfg = self.settings["devices"][address] = dict(DEVICE_DEFAULTS)
        return cfg

    @staticmethod
    def short_name(dev):
        if dev["os"] in ("iOS", "iPadOS"):
            return "iPad" if dev["form"] == "tablet" else "iPhone"
        return dev["name"]

    def add_device(self, dev):
        address, form = dev["address"], dev["form"]
        cfg = self.device_cfg(address)
        others = [(t.form, t.cfg["edge"], t.cfg["x"], t.cfg["y"]) for t in self.arrangement.tiles.values()]
        if cfg["edge"] is None or cfg["x"] is None or cfg["y"] is None:
            used = {e for _f, e, _x, _y in others}
            edge = cfg["edge"] or next((e for e in OPPOSITE if e not in used), "bottom")
            cfg["edge"], cfg["x"], cfg["y"] = self.arrangement.layout.free_spot(form, edge, others)
        self.arrangement.add(address, form, cfg)
        row = DeviceRow(self, address)
        self.devices_group.add(row)
        self.rows[address] = row

    def remove_device(self, address):
        self.devices_group.remove(self.rows.pop(address))
        self.arrangement.remove_device(address)
        self.devs.pop(address, None)

    # --- backend events ---

    def on_status(self, alive, reason):
        self.alive = alive
        if alive:
            self.resync = True
            self.refresh_hid()
            return
        self.target, self.locked = None, False
        for address in list(self.rows):
            self.remove_device(address)
        self.devices_stack.set_visible_child_name("empty")
        self.update_overlay()
        self.tray.update()
        if self.hid_change:
            self.run_hid_change(*self.hid_change)
        elif self.restart_backend or (reason == "not running" and not self.started_backend):
            self.restart_backend = False
            self.start_backend()

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "state":
            self.on_state(msg)
        elif kind == "paired":
            self.on_paired(msg)
        elif kind == "shake":
            self.enlarge_cursor()
        elif kind == "open":
            self.present()

    def on_state(self, st):
        self.target = st["target"]
        self.locked = bool(st.get("locked"))
        seen = set()
        changed = False
        for dev in st["devices"]:
            address = dev["address"]
            seen.add(address)
            old = self.devs.get(address)
            self.devs[address] = dev
            if address not in self.rows:
                self.add_device(dev)
                changed = True
            elif ((old["form"], old["name"], old["os"], old["handoff"]["phone_w"], old["handoff"]["phone_h"])
                  != (dev["form"], dev["name"], dev["os"], dev["handoff"]["phone_w"], dev["handoff"]["phone_h"])):
                # (a new screen size, once the model is known, changes "Match Pointer Speed")
                changed = True
                if old["form"] != dev["form"]:  # the tile's size follows the device's form
                    self.arrangement.remove_device(address)
                    self.arrangement.add(address, dev["form"], self.device_cfg(address))
            self.rows[address].update(dev, self.device_cfg(address)["enabled"])
        for address in set(self.rows) - seen:
            self.remove_device(address)
            changed = True
        self.devices_stack.set_visible_child_name("list" if self.rows else "empty")

        if st.get("sharing") is not None and st["sharing"] != self.settings["share_cursor"]:
            self.settings["share_cursor"] = st["sharing"]  # toggled with Super+Shift+S
            save_settings(self.settings)
            self.share_row.updating = True
            self.share_row.set_active(st["sharing"])
            self.share_row.updating = False
            changed = True
        if st.get("nearby") is not None:
            self.show_nearby(st["nearby"], st.get("pairing"))
        if self.pending_guide in self.devs:
            address, self.pending_guide = self.pending_guide, None
            if self.devs[address]["os"] in ("iOS", "iPadOS"):
                self.show_guide(address)
        if self.resync:
            self.resync = False
            self.send_options()
            changed = True
        if changed:
            self.layout_changed()
        else:
            self.update_overlay()
        self.tray.update()

    # --- hand-off and options ---

    def active(self):
        return {a for a in self.rows if self.device_cfg(a)["enabled"] and self.settings["share_cursor"]}

    def layout_changed(self):
        """Save the arrangement and send every device's hand-off settings to the backend."""
        active = self.active()
        for address in self.rows:
            dev = self.devs[address]
            self.arrangement.set_state(address, self.short_name(dev), dev["name"],
                                       self.device_cfg(address)["enabled"])
        self.spans = self.arrangement.refresh(active, self.settings["corner_dead_zones"])
        save_settings(self.settings)
        for address in self.rows:
            cfg, dev = self.device_cfg(address), self.devs[address]
            w, h = dev["handoff"]["phone_w"], dev["handoff"]["phone_h"]
            speed = round(math.sqrt(w * h / BASE_SCREEN), 2) if self.settings["match_speed"] else 1.0
            self.backend.send(cmd="set_handoff", address=address,
                              zone=cfg["edge"] if address in active else None, span=self.spans.get(address),
                              require_near=False, speed=speed)
        self.update_overlay()

    def update_overlay(self):
        segments = []
        if self.alive and self.target is None:
            for address in self.active():
                cfg = self.device_cfg(address)
                if cfg["edge"]:
                    name = self.devs[address]["name"]
                    segments += [(*seg, name) for seg in edge_segments(cfg["edge"], self.spans.get(address))]
        self.overlay.update(segments, self.settings["indicator"], self.settings["indicator_within"],
                            self.settings["indicator_names"], self.indicator_look())

    def indicator_look(self):
        style, thickness, opacity = INDICATOR_STYLES.get(self.settings["indicator_style"],
                                                          INDICATOR_STYLES["Standard"])
        return {"style": style, "thickness": thickness, "opacity": opacity, "color": self.settings["indicator_color"]}

    def send_options(self):
        options = {k: self.settings[k] for k in BACKEND_OPTIONS}
        options["discoverable"] = self.settings["visible"]
        if self.settings["device_name"]:
            options["device_name"] = self.settings["device_name"]
        self.backend.send(cmd="set_options", **options)

    def send_option(self, key):
        value = self.settings[key]
        self.backend.send(cmd="set_options", **({"discoverable": value} if key == "visible" else {key: value}))

    def on_close(self, _window):
        app = self.get_application()
        if self.settings["run_in_background"]:
            app.keep_running()
            self.set_visible(False)
            return True
        app.quit()
        return False

    def enlarge_cursor(self):
        """Shake to Locate: make the cursor big for a moment (Hyprland)."""
        theme = os.environ.get("XCURSOR_THEME") or Gio.Settings(schema_id="org.gnome.desktop.interface") \
            .get_string("cursor-theme")
        size = int(os.environ.get("XCURSOR_SIZE") or 24)
        if hyprland(f"setcursor {theme} {size * 3}") is None:
            return
        GLib.timeout_add(1200, lambda: (hyprland(f"setcursor {theme} {size}"), False)[1])
