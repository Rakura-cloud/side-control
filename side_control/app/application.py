# SPDX-License-Identifier: GPL-3.0-or-later
"""The application: single instance, background mode, shortcuts."""

import sys

from gi.repository import Adw, Gio

from .config import APP_ID
from .window import Window


class App(Adw.Application):
    def __init__(self, hidden=False):
        super().__init__(application_id=APP_ID)
        self.hidden = hidden
        self.held = False
        self.window = None
        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def keep_running(self):
        if not self.held:
            self.held = True
            self.hold()

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
        if self.hidden and self.window.settings["run_in_background"]:
            self.hidden = False
            self.keep_running()
            return
        self.hidden = False
        self.window.present()


def main():
    hidden = "--hidden" in sys.argv
    sys.exit(App(hidden).run([a for a in sys.argv if a != "--hidden"]))
