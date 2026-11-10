# SPDX-License-Identifier: GPL-3.0-or-later
"""The shipped KWin script and GNOME Shell extension, checked for syntax where node is installed:
python -m unittest discover tests"""
import json
import os
import shutil
import subprocess
import unittest

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "side_control", "data")
KWIN_SCRIPT = os.path.join(DATA, "kwin", "side-control-cursor.js")
EXTENSION = os.path.join(DATA, "gnome-shell", "side-control@sidecontrol.github.io")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Syntax(unittest.TestCase):
    def test_kwin_script(self):  # a plain script: node --check reads it as CommonJS
        subprocess.run(["node", "--check", KWIN_SCRIPT], check=True)

    def test_extension(self):  # ES modules: node --check reads CommonJS only
        with open(os.path.join(EXTENSION, "extension.js")) as f:
            subprocess.run(["node", "--input-type=module", "--check"], stdin=f, check=True)


class Metadata(unittest.TestCase):
    def test_uuid_is_the_folder_and_gnome_45_and_later(self):
        with open(os.path.join(EXTENSION, "metadata.json")) as f:
            metadata = json.load(f)
        self.assertEqual(metadata["uuid"], os.path.basename(EXTENSION))
        for version in ("45", "48"):
            self.assertIn(version, metadata["shell-version"])


if __name__ == "__main__":
    unittest.main()
