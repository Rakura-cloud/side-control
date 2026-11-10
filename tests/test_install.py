# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing the AppImage, without GTK or a display: python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import side_control.app  # noqa: E402,F401  (picks the GTK versions)
from side_control.app import install  # noqa: E402
from side_control.app.config import APP_ICON, ICONS_DIR, desktop_exec  # noqa: E402


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home with spaces")
        self.source = os.path.join(self.tmp.name, "Downloads", "Side_Control 0.1.0-x86_64.AppImage")
        os.makedirs(os.path.dirname(self.source))
        with open(self.source, "w") as f:
            f.write("#!/bin/sh\necho Side Control\n")
        os.chmod(self.source, 0o755)
        env = mock.patch.dict(os.environ, {
            "HOME": self.home,
            "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
            "XDG_DATA_HOME": os.path.join(self.home, ".local", "share"),
            "APPIMAGE": self.source})
        env.start()
        self.addCleanup(env.stop)
        self.target = os.path.join(self.home, ".local", "bin", "side-control.AppImage")
        self.desktop = os.path.join(self.home, ".local", "share", "applications", APP_ICON + ".desktop")
        self.icon = os.path.join(self.home, ".local", "share", "icons", "hicolor", "scalable", "apps",
                                 APP_ICON + ".svg")
        self.autostart = os.path.join(self.home, ".config", "autostart", APP_ICON + ".desktop")

    def read(self, path):
        with open(path) as f:
            return f.read()

    def test_writes_the_copy_the_menu_entry_and_the_icon(self):
        lines = install.install()
        self.assertTrue(lines)
        self.assertEqual(self.read(self.target), self.read(self.source))
        self.assertTrue(os.access(self.target, os.X_OK))
        self.assertEqual(self.read(self.icon), self.read(os.path.join(ICONS_DIR, APP_ICON + ".svg")))

        entry = self.read(self.desktop)
        self.assertIn(f'Exec="{self.target}"\n', entry)
        self.assertIn(f"Icon={APP_ICON}\n", entry)
        self.assertIn("Name=Side Control\n", entry)
        self.assertFalse(os.path.exists(self.autostart))

    def test_launch_at_login_follows_the_copy(self):
        os.makedirs(os.path.dirname(self.autostart))
        with open(self.autostart, "w") as f:
            f.write("[Desktop Entry]\nExec=/somewhere/else --hidden\n")
        install.install()
        entry = self.read(self.autostart)
        self.assertIn(f'Exec="{self.target}" --hidden\n', entry)
        self.assertIn(f"Icon={self.icon}\n", entry)
        install.uninstall()
        self.assertFalse(os.path.exists(self.autostart))

    def test_symlinks_are_resolved(self):
        link = os.path.join(self.tmp.name, "a link to it.AppImage")
        os.symlink(self.source, link)
        os.environ["APPIMAGE"] = link
        install.install()
        self.assertEqual(self.read(self.target), self.read(self.source))
        self.assertIn(f'Exec="{self.target}"\n', self.read(self.desktop))

    def test_install_twice_writes_the_same_files(self):
        install.install()
        first = (self.read(self.target), self.read(self.desktop), self.read(self.icon))
        install.install()
        self.assertEqual((self.read(self.target), self.read(self.desktop), self.read(self.icon)), first)

    def test_uninstall_removes_the_install_and_keeps_the_settings(self):
        install.install()
        settings = os.path.join(self.home, ".config", "side-control", "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w") as f:
            f.write("{}")
        install.uninstall()
        for path in (self.target, self.desktop, self.icon, self.autostart):
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.exists(settings))
        install.uninstall()  # and again: nothing to remove, no error

    def test_uninstall_keeps_an_autostart_for_another_copy(self):
        os.makedirs(os.path.dirname(self.autostart))
        with open(self.autostart, "w") as f:
            f.write("[Desktop Entry]\nExec=/somewhere/else --hidden\n")
        install.uninstall()
        self.assertTrue(os.path.exists(self.autostart))

    def test_outside_an_appimage(self):
        del os.environ["APPIMAGE"]
        with self.assertRaises(ValueError):
            install.install()


class DesktopExec(unittest.TestCase):
    def test_quotes_what_the_desktop_entry_spec_reserves(self):
        self.assertEqual(desktop_exec("/home/me/.local/bin/side-control.AppImage"),
                         "/home/me/.local/bin/side-control.AppImage")
        self.assertEqual(desktop_exec("/home/me/My Apps/side-control"), '"/home/me/My Apps/side-control"')
        # the Exec quoting's backslash, then doubled by the string escaping of every desktop entry value
        self.assertEqual(desktop_exec("/home/me/$dir/side-control"), '"/home/me/\\\\$dir/side-control"')
        self.assertEqual(desktop_exec('/home/me/a"b'), '"/home/me/a\\\\"b"')
        self.assertEqual(desktop_exec("/home/me/100%/side-control"), "/home/me/100%%/side-control")


class NoExec(unittest.TestCase):
    def test_reads_the_mount_flag(self):
        with mock.patch.object(install.os, "statvfs") as statvfs:
            statvfs.return_value = mock.Mock(f_flag=os.ST_NOEXEC)
            self.assertTrue(install.noexec("/"))
            statvfs.return_value = mock.Mock(f_flag=0)
            self.assertFalse(install.noexec("/"))


if __name__ == "__main__":
    unittest.main()
