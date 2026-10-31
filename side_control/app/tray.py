# SPDX-License-Identifier: GPL-3.0-or-later
"""The tray icon and its menu (StatusNotifierItem + com.canonical.dbusmenu)."""

import os

from gi.repository import Adw, GLib, GdkPixbuf, Gio

from .config import APP_ICON, ICONS_DIR


TRAY_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
  </interface>
</node>
"""


def icon_pixmaps(name, color):
    """[(w, h, ARGB32 bytes)] of one of the bundled tray icons, in `color`, for a tray host to draw."""
    with open(os.path.join(ICONS_DIR, f"{name}.svg")) as f:
        svg = f.read().replace("#222222", color).encode()
    pixmaps = []
    for size in (16, 22, 24, 32, 48):
        loader = GdkPixbuf.PixbufLoader()
        loader.set_size(size, size)
        loader.write(svg)
        loader.close()
        pixbuf = loader.get_pixbuf().add_alpha(False, 0, 0, 0)
        data, stride = pixbuf.get_pixels(), pixbuf.get_rowstride()
        argb = bytearray()
        for y in range(size):
            row = data[y * stride:y * stride + size * 4]
            for x in range(0, size * 4, 4):
                r, g, b, a = row[x:x + 4]
                argb += bytes((a, r, g, b))
        pixmaps.append((size, size, bytes(argb)))
    return pixmaps


class Tray:
    """Side Control in the system tray (StatusNotifierItem + com.canonical.dbusmenu, as Waybar and
    other StatusNotifier hosts expect): where the cursor is, and a menu to move it."""

    PATH, MENU_PATH = "/StatusNotifierItem", "/MenuBar"

    def __init__(self, window):
        self.window = window
        self.icon = "side-control-off-symbolic"
        self.pixmaps = []
        self.tooltip = ("Side Control", "")
        self.items = []  # [(props, callback)] - menu item id = index + 1
        self.revision = 0
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error:
            self.bus = None
            return
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        node = Gio.DBusNodeInfo.new_for_xml(TRAY_XML)
        self.bus.register_object(self.PATH, node.lookup_interface("org.kde.StatusNotifierItem"),
                                 self.on_item_call, self.on_item_property, None)
        self.bus.register_object(self.MENU_PATH, node.lookup_interface("com.canonical.dbusmenu"),
                                 self.on_menu_call, self.on_menu_property, None)
        Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # (re-)register whenever a tray host's watcher appears, e.g. after Waybar restarts
        Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self.register(), None)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update(force_icon=True))

    def register(self):
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                      "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)), None,
                      Gio.DBusCallFlags.NONE, -1, None, None)

    def emit(self, path, interface, signal, params=None):
        self.bus.emit_signal(None, path, interface, signal, params)

    # --- what it shows ---

    def update(self, force_icon=False):
        if self.bus is None:
            return
        w = self.window
        target = w.devs.get(w.target) if w.alive and w.target else None
        connected = [d for d in w.devs.values() if d.get("connected")]
        if not w.alive:
            icon, text = "side-control-off-symbolic", "Not running"
        elif target:
            icon, text = "side-control-active-symbolic", f"Cursor on {target['name']}"
        else:
            icon = APP_ICON + "-symbolic" if w.settings["share_cursor"] else "side-control-off-symbolic"
            text = "Cursor on this computer" + ("" if w.settings["share_cursor"] else " · sharing off")
        if w.alive:
            text += f"\n{len(connected)} device{'s' if len(connected) != 1 else ''} connected"
            if w.locked:
                text += " · locked to this screen"
        if icon != self.icon or force_icon or not self.pixmaps:
            self.icon = icon
            color = "#ffffff" if Adw.StyleManager.get_default().get_dark() else "#000000"
            self.pixmaps = icon_pixmaps(icon, color)
            self.emit(self.PATH, "org.kde.StatusNotifierItem", "NewIcon")
        if ("Side Control", text) != self.tooltip:
            self.tooltip = ("Side Control", text)
            self.emit(self.PATH, "org.kde.StatusNotifierItem", "NewToolTip")
        self.build_menu(target, connected)

    def build_menu(self, target, connected):
        w = self.window
        items = []

        def add(label=None, callback=None, enabled=True, toggle=None, state=False):
            props = {"type": "separator"} if label is None else {"label": label, "enabled": enabled}
            if toggle:
                props.update({"toggle-type": toggle, "toggle-state": 1 if state else 0})
            items.append((props, callback))

        if w.alive:
            add("This Computer", lambda: w.backend.send(cmd="set_target", address=None),
                toggle="radio", state=target is None)
            for dev in sorted(w.devs.values(), key=lambda d: d["name"].lower()):
                add(dev["name"].replace("_", "__"),
                    lambda a=dev["address"]: w.backend.send(cmd="set_target", address=a),
                    enabled=bool(dev.get("connected")), toggle="radio", state=dev is target)
            add()
            add("Share Cursor", lambda: w.share_row.set_active(not w.settings["share_cursor"]),
                toggle="checkmark", state=w.settings["share_cursor"])
            add("Lock Cursor to Current Screen", lambda: w.backend.send(cmd="set_options", locked=not w.locked),
                toggle="checkmark", state=w.locked)
            add()
            add("Pair Device…", w.open_pair)
        add("Open Side Control", w.present)
        add("Quit", w.get_application().quit)
        if [p for p, _ in items] != [p for p, _ in self.items]:
            self.items = items
            self.revision += 1
            self.emit(self.MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                      GLib.Variant("(ui)", (self.revision, 0)))
        else:
            self.items = items  # same look, fresh callbacks

    # --- org.kde.StatusNotifierItem ---

    def on_item_property(self, _bus, _sender, _path, _iface, name):
        values = {"Category": GLib.Variant("s", "ApplicationStatus"), "Id": GLib.Variant("s", "side-control"),
                  "Title": GLib.Variant("s", "Side Control"), "Status": GLib.Variant("s", "Active"),
                  "WindowId": GLib.Variant("i", 0), "IconName": GLib.Variant("s", ""),
                  "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
                  "IconThemePath": GLib.Variant("s", ""),
                  "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], *self.tooltip)),
                  "ItemIsMenu": GLib.Variant("b", False), "Menu": GLib.Variant("o", self.MENU_PATH)}
        return values.get(name)

    def on_item_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        if method == "Activate":
            self.window.present()
        elif method == "SecondaryActivate":
            self.window.backend.send(cmd="set_target", address=None)
        invocation.return_value(None)

    # --- com.canonical.dbusmenu ---

    def on_menu_property(self, _bus, _sender, _path, _iface, name):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(name)

    def item_props(self, item_id):
        if item_id == 0:
            return {"children-display": GLib.Variant("s", "submenu")}
        props, _callback = self.items[item_id - 1]
        return {k: GLib.Variant({"label": "s", "type": "s", "toggle-type": "s"}.get(k, "b" if k == "enabled" else "i"),
                                v) for k, v in props.items()}

    def on_menu_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (i, self.item_props(i), []))
                        for i in range(1, len(self.items) + 1)]
            invocation.return_value(GLib.Variant("(u(ia{sv}av))",
                                                 (self.revision, (0, self.item_props(0), children))))
        elif method == "GetGroupProperties":
            ids = [i for i in args[0] if 0 <= i <= len(self.items)] or range(len(self.items) + 1)
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([(i, self.item_props(i)) for i in ids],)))
        elif method == "GetProperty":
            item_id, name = args
            value = self.item_props(item_id).get(name) if 0 <= item_id <= len(self.items) else None
            invocation.return_value(GLib.Variant("(v)", (value or GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event = args[0], args[1]
            if event == "clicked" and 1 <= item_id <= len(self.items) and self.items[item_id - 1][1]:
                GLib.idle_add(lambda cb=self.items[item_id - 1][1]: (cb(), False)[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event, _data, _time in args[0]:
                if event == "clicked" and 1 <= item_id <= len(self.items) and self.items[item_id - 1][1]:
                    GLib.idle_add(lambda cb=self.items[item_id - 1][1]: (cb(), False)[1])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)
