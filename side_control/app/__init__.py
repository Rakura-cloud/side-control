# SPDX-License-Identifier: GPL-3.0-or-later
"""The GTK 4 / libadwaita app: window, tray icon and edge indicator. Runs as the user and starts the
root backend through pkexec when it is not running. Start it with __main__ (the side-control launcher).

Importing this package picks the GTK versions, before any module of the app imports gi.repository.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
