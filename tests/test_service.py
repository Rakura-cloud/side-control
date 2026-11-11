# SPDX-License-Identifier: GPL-3.0-or-later
"""Installing the backend's service, without root: python -m unittest discover tests"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import side_control.app  # noqa: E402,F401  (picks the GTK versions)
from side_control.app import system  # noqa: E402
from side_control.app.config import GNOME_EXTENSION, NO_INPUT_CONF, SERVICE_RULES, SERVICE_UNIT  # noqa: E402


class InstallFile(unittest.TestCase):
    def test_writes_text_exactly(self):
        """The unit has %i, the rules quotes and $: all must reach the file as they are."""
        with tempfile.TemporaryDirectory() as tmp:
            for source in (SERVICE_UNIT, SERVICE_RULES, NO_INPUT_CONF):
                with open(source) as f:
                    text = f.read()
                path = os.path.join(tmp, "a dir", "it's here", os.path.basename(source))
                subprocess.run(["sh", "-c", system.install_file(text, path)], check=True)
                with open(path) as f:
                    self.assertEqual(f.read(), text)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)


class Installed(unittest.TestCase):
    def test_only_the_same_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "side-control@.service")
            with mock.patch.object(system, "UNIT_PATH", path):
                self.assertFalse(system.service_installed())
                with open(SERVICE_UNIT) as f, open(path, "w") as out:
                    out.write(f.read())
                self.assertTrue(system.service_installed())
                with open(path, "a") as out:
                    out.write("# an older version\n")
                self.assertFalse(system.service_installed())


class GnomeExtension(unittest.TestCase):
    """install_gnome_extension: the GNOME Shell extension copied into the user's extensions
    directory and enabled, no root; the tests use a temporary HOME and a mocked gnome-extensions."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"HOME": self.tmp.name}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        run = mock.patch.object(system.subprocess, "run")
        self.run = run.start()
        self.addCleanup(run.stop)
        self.run.return_value = mock.Mock(returncode=0, stderr="")

    def ext(self):
        return os.path.join(self.tmp.name, ".local", "share", "gnome-shell", "extensions",
                            os.path.basename(GNOME_EXTENSION))

    def test_copied_and_enabled(self):
        system.install_gnome_extension()
        for name in ("extension.js", "metadata.json"):
            with open(os.path.join(self.ext(), name)) as f:
                self.assertTrue(f.read())
        self.run.assert_called_once_with(
            ["gnome-extensions", "enable", os.path.basename(GNOME_EXTENSION)],
            capture_output=True, text=True, timeout=10)

    def test_an_extension_that_will_not_enable_says_why(self):
        self.run.return_value = mock.Mock(returncode=1, stderr="no such extension")
        with self.assertRaises(OSError) as caught:
            system.install_gnome_extension()
        self.assertIn("no such extension", caught.exception.strerror)


class CompositorExports(unittest.TestCase):
    """The compositor's environment, for the command the service runs: the service starts without
    the session's variables, and the backend picks its compositor from these."""

    def test_only_the_set_variables_and_unchanged_by_a_shell(self):
        env = {"NIRI_SOCKET": "/run/user/1000/niri wayland", "XDG_CURRENT_DESKTOP": "niri"}
        with mock.patch.dict(os.environ, env, clear=True):
            text = system.compositor_exports()
        self.assertNotIn("HYPRLAND", text)
        self.assertIn("export XDG_CURRENT_DESKTOP=niri", text)
        run = subprocess.run(["sh", "-c", text + '\nprintf %s "$NIRI_SOCKET"'],
                             capture_output=True, text=True)
        self.assertEqual(run.stdout, "/run/user/1000/niri wayland")  # quoted as it was

    def test_nothing_set_nothing_exported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(system.compositor_exports(), "")


if __name__ == "__main__":
    unittest.main()
