# SPDX-License-Identifier: GPL-3.0-or-later
"""The on-screen edge indicator (gtk4-layer-shell)."""

import json
import math

import cairo
import gi
from gi.repository import GLib, Gdk, Gtk, Pango, PangoCairo

from .system import hyprland

try:  # optional: without it there is no edge indicator
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
except (ValueError, ImportError):
    Gtk4LayerShell = None


class EdgeOverlay:
    """The on-screen edge indicator: thin lines (and device names) along the screen edges where the
    pointer crosses, drawn in click-through layer-shell surfaces over every monitor."""

    def __init__(self):
        self.windows = {}  # monitor -> (window, drawing area)
        self.segments = []  # [(edge, lo, hi, name)]
        self.mode, self.within, self.names = "near", 48, True
        self.cursor = None
        self.poll = None
        self.available = bool(Gtk4LayerShell and Gtk4LayerShell.is_supported())
        if self.available:
            Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: self.sync())

    def update(self, segments, mode, within, names):
        self.segments, self.mode, self.within, self.names = segments, mode, within, names
        self.sync()

    def sync(self):
        if not self.available:
            return
        show = self.mode != "never" and bool(self.segments)
        monitors = list(Gdk.Display.get_default().get_monitors()) if show else []
        for monitor in list(self.windows):
            if monitor not in monitors:
                self.windows.pop(monitor)[0].destroy()
        for monitor in monitors:
            if monitor not in self.windows:
                self.windows[monitor] = self._window(monitor)
        for _window, area in self.windows.values():
            area.queue_draw()
        polling = show and self.mode == "near"
        if polling and self.poll is None:
            self.poll = GLib.timeout_add(40, self._poll)
        elif not polling and self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll, self.cursor = None, None

    def _window(self, monitor):
        window = Gtk.Window(css_classes=["edge-overlay"], decorated=False)
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, "side-control-edges")
        Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_monitor(window, monitor)
        for edge in (Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT,
                     Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM):
            Gtk4LayerShell.set_anchor(window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(window, -1)
        Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.NONE)
        area = Gtk.DrawingArea(can_target=False)
        area.set_draw_func(self._draw, monitor)
        window.set_child(area)
        window.connect("realize", lambda w: w.get_surface().set_input_region(cairo.Region()))
        window.present()
        return window, area

    def _poll(self):
        reply = hyprland("j/cursorpos")
        try:
            pos = json.loads(reply) if reply else None
            cursor = (pos["x"], pos["y"]) if pos else None
        except (ValueError, KeyError, TypeError):
            cursor = None
        if cursor != self.cursor:
            self.cursor = cursor
            for _window, area in self.windows.values():
                area.queue_draw()
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _box():
        rects = [m.get_geometry() for m in Gdk.Display.get_default().get_monitors()]
        left, top = min(r.x for r in rects), min(r.y for r in rects)
        return left, top, max(r.x + r.width for r in rects), max(r.y + r.height for r in rects)

    def _draw(self, area, cr, w, h, monitor):
        geo = monitor.get_geometry()
        left, top, right, bottom = self._box()
        found, accent = area.get_style_context().lookup_color("accent_bg_color")
        if not found:
            accent = Gdk.RGBA(red=0.21, green=0.52, blue=0.89, alpha=1)
        for edge, lo, hi, name in self.segments:
            vert = edge in ("left", "right")
            on_edge = {"left": geo.x == left, "right": geo.x + geo.width == right,
                       "top": geo.y == top, "bottom": geo.y + geo.height == bottom}[edge]
            if not on_edge:
                continue
            a0, a1 = (top, bottom) if vert else (left, right)
            g0, g1 = a0 + lo * (a1 - a0), a0 + hi * (a1 - a0)  # the segment, in global coordinates
            m0, m1 = (geo.y, geo.y + geo.height) if vert else (geo.x, geo.x + geo.width)
            s0, s1 = max(g0, m0), min(g1, m1)
            if s1 <= s0:
                continue
            alpha = 1.0
            if self.mode == "near":
                if self.cursor is None:
                    continue
                cx, cy = self.cursor
                across = {"left": cx - left, "right": right - 1 - cx, "top": cy - top, "bottom": bottom - 1 - cy}[edge]
                along = cy if vert else cx
                dist = math.hypot(max(0.0, across), max(0.0, g0 - along, along - g1))
                if dist > self.within:
                    continue
                alpha = 1.0 - 0.7 * dist / max(1, self.within)
            p0, p1 = s0 - m0, s1 - m0  # monitor-local
            if vert:
                rect = (0 if edge == "left" else w - 4, p0, 4, p1 - p0)
            else:
                rect = (p0, 0 if edge == "top" else h - 4, p1 - p0, 4)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, alpha)
            cr.rectangle(*rect)
            cr.fill()
            if self.names and name:
                self._label(area, cr, name, edge, rect, w, h, alpha, accent)

    @staticmethod
    def _label(area, cr, text, edge, rect, w, h, alpha, accent):
        layout = area.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 9"))
        tw, th = (v / Pango.SCALE for v in layout.get_size())
        pad_x, pad_y = 8, 3
        bw, bh = tw + 2 * pad_x, th + 2 * pad_y
        x, y, rw, rh = rect
        bx = {"left": 10, "right": w - 10 - bw}.get(edge, x + rw / 2 - bw / 2)
        by = {"top": 10, "bottom": h - 10 - bh}.get(edge, y + rh / 2 - bh / 2)
        bx, by = max(4, min(w - bw - 4, bx)), max(4, min(h - bh - 4, by))
        r = bh / 2
        cr.new_sub_path()
        cr.arc(bx + bw - r, by + r, r, -math.pi / 2, math.pi / 2)
        cr.arc(bx + r, by + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.92 * alpha)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, alpha)
        cr.move_to(bx + pad_x, by + pad_y)
        PangoCairo.show_layout(cr, layout)
