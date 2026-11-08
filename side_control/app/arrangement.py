# SPDX-License-Identifier: GPL-3.0-or-later
"""The Layout page's canvas: device tiles dragged around the screen."""

from gi.repository import Adw, Gdk, Gtk, Pango

from .geometry import CANVAS_H, CANVAS_W, SH, SW, SX, SY, TILE, Screen
from .widgets import device_icon


class DeviceTile(Gtk.Box):
    def __init__(self, address, form, cfg):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["device-tile"])
        self.address, self.form, self.cfg = address, form, cfg
        w, h = TILE[form]
        self.set_size_request(w, h)
        self.append(Gtk.Image(icon_name=device_icon(form), pixel_size=20, vexpand=True, valign=Gtk.Align.END))
        self.label = Gtk.Label(vexpand=True, valign=Gtk.Align.START, justify=Gtk.Justification.CENTER,
                               wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               margin_start=4, margin_end=4)
        self.append(self.label)
        self.set_cursor_from_name("grab")


class ArrangementView(Gtk.Fixed):
    """Drag a device to the edge of this computer's screen where the pointer should cross over."""

    def __init__(self, on_moved):
        super().__init__(css_classes=["arrangement"], halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        self.set_size_request(CANVAS_W, CANVAS_H)
        self.on_moved = on_moved
        self.tiles = {}  # address -> DeviceTile
        self.indicators = []
        self.monitors = []  # one tile per monitor, when there are several
        self.layout = Screen()
        self.dragging = None
        self.anim = None

        self.screen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["screen-tile"])
        self.screen.set_size_request(SW, SH)
        self.screen.append(Gtk.Image(icon_name="sc-computer-symbolic", pixel_size=26, vexpand=True,
                                     valign=Gtk.Align.END))
        self.screen.append(Gtk.Label(label="This Computer", css_classes=["heading"]))
        self.resolution = Gtk.Label(css_classes=["caption", "dim-label"], vexpand=True, valign=Gtk.Align.START)
        self.screen.append(self.resolution)
        self.put(self.screen, SX, SY)
        self.empty = Gtk.Label(label="Pair a device to arrange it", css_classes=["dim-label", "caption"])
        self.empty.set_size_request(CANVAS_W, -1)
        self.put(self.empty, 0, CANVAS_H - 34)
        self.show_monitors()
        Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: self.monitors_changed())
        drag = Gtk.GestureDrag()  # on the canvas: offsets measured on a moving tile would jitter
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

    def show_monitors(self):
        """One screen tile for one monitor; with several, each drawn where it is in the layout."""
        for tile in self.monitors:
            self.remove(tile)
        self.monitors = []
        monitors = list(Gdk.Display.get_default().get_monitors())
        if not monitors:
            return
        geos = [m.get_geometry() for m in monitors]
        rects = [(g.x, g.y, g.x + g.width, g.y + g.height) for g in geos]
        self.layout = Screen(rects)
        self.screen.set_visible(len(monitors) == 1)
        resolution = lambda m, g: f"{round(g.width * m.get_scale())} × {round(g.height * m.get_scale())}"  # noqa: E731
        if len(monitors) == 1:
            self.resolution.set_label(resolution(monitors[0], geos[0]))
            return
        below = self.screen
        for monitor, geo, (left, top, right, bottom) in zip(monitors, geos, self.layout.monitors):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["screen-tile", "monitor-tile"],
                           tooltip_text=monitor.get_description() or monitor.get_connector())
            tile.set_size_request(round(right - left) - 2, round(bottom - top) - 2)  # a gap between them
            tile.append(Gtk.Label(label=monitor.get_connector() or "Monitor", css_classes=["heading"],
                                  vexpand=True, valign=Gtk.Align.END, ellipsize=Pango.EllipsizeMode.END))
            tile.append(Gtk.Label(label=resolution(monitor, geo), css_classes=["caption", "dim-label"],
                                  vexpand=True, valign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END))
            self.put(tile, left + 1, top + 1)
            tile.insert_after(self, below)  # under the tiles
            below = tile
            self.monitors.append(tile)

    def monitors_changed(self):
        self.show_monitors()
        for tile in self.tiles.values():
            self.layout.settle(tile.form, tile.cfg)
            self.move(tile, tile.cfg["x"], tile.cfg["y"])
        self.on_moved()

    def add(self, address, form, cfg):
        self.layout.settle(form, cfg)  # saved with other monitors
        tile = DeviceTile(address, form, cfg)
        self.put(tile, cfg["x"], cfg["y"])
        self.tiles[address] = tile
        self.empty.set_visible(False)

    def remove_device(self, address):
        self.remove(self.tiles.pop(address))
        self.empty.set_visible(not self.tiles)

    def set_state(self, address, short, name, active):
        tile = self.tiles[address]
        tile.label.set_label(short)
        tile.set_tooltip_text(name)
        if active:
            tile.remove_css_class("inactive")
        else:
            tile.add_css_class("inactive")

    def refresh(self, active, corner_dead):
        """Redraw the crossing indicators; returns {address: span} for the active devices."""
        for widget in self.indicators:
            self.remove(widget)
        self.indicators = []
        spans = self.layout.crossings({a: (t.form, t.cfg) for a, t in self.tiles.items() if a in active},
                                      corner_dead)
        if self.dragging is None:
            below = self.monitors[-1] if self.monitors else self.screen
            for address, span in spans.items():
                for x, y, w, h in self.layout.indicator_rects(self.tiles[address].cfg["edge"], span):
                    if w <= 0 or h <= 0:
                        continue
                    bar = Gtk.Box(css_classes=["edge-indicator"], can_target=False)
                    bar.set_size_request(round(w), round(h))
                    self.put(bar, x, y)
                    bar.insert_after(self, below)  # under the tiles
                    self.indicators.append(bar)
        return spans

    def on_drag_begin(self, gesture, x, y):
        tile = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while tile is not None and not isinstance(tile, DeviceTile):
            tile = tile.get_parent() if tile is not self else None
        if tile is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.anim:
            self.anim.skip()
        self.dragging = tile
        tile.start = (tile.cfg["x"], tile.cfg["y"])
        tile.add_css_class("dragging")
        tile.set_cursor_from_name("grabbing")
        tile.insert_before(self, None)  # on top
        for widget in self.indicators:
            widget.set_visible(False)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_drag_update(self, _gesture, dx, dy):
        tile = self.dragging
        if tile is None:
            return
        w, h = TILE[tile.form]
        tile.cfg["x"] = max(0, min(CANVAS_W - w, tile.start[0] + dx))
        tile.cfg["y"] = max(0, min(CANVAS_H - h, tile.start[1] + dy))
        self.move(tile, tile.cfg["x"], tile.cfg["y"])

    def on_drag_end(self, _gesture, _dx, _dy):
        tile = self.dragging
        if tile is None:
            return
        w, h = TILE[tile.form]
        tile.remove_css_class("dragging")
        tile.set_cursor_from_name("grab")
        before = {a: (t.cfg["x"], t.cfg["y"]) for a, t in self.tiles.items()}
        tile.cfg["edge"], tile.cfg["x"], tile.cfg["y"] = self.layout.snap(tile.form, tile.cfg["x"] + w / 2,
                                                                          tile.cfg["y"] + h / 2)
        edge = tile.cfg["edge"]
        if "-" not in edge:
            against = {seg[0] for _i, seg in self.layout.attached(tile.form, tile.cfg)}
            row = [(t.form, t.cfg) for t in self.tiles.values() if t.cfg["edge"] == edge
                   and against & {seg[0] for _i, seg in self.layout.attached(t.form, t.cfg)}]
            if len(row) > 1:
                self.layout.spread(row, edge)
        self.dragging = None
        self.animate(before)
        self.on_moved()

    def animate(self, before):
        moves = {t: (before[a], (t.cfg["x"], t.cfg["y"])) for a, t in self.tiles.items()}

        def step(value):
            for tile, ((x0, y0), (x1, y1)) in moves.items():
                self.move(tile, x0 + (x1 - x0) * value, y0 + (y1 - y0) * value)

        self.anim = Adw.TimedAnimation(widget=self, value_from=0, value_to=1, duration=180,
                                       easing=Adw.Easing.EASE_OUT_CUBIC,
                                       target=Adw.CallbackAnimationTarget.new(step))
        self.anim.play()
