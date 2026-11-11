# SPDX-License-Identifier: GPL-3.0-or-later
"""Side Control: use a Linux computer's keyboard and touchpad on iPhones and iPads.

app/      the GTK window and tray, running as the user
backend/  the Bluetooth keyboard + mouse, running as the user with a few capabilities
They talk JSON over a unix socket: see side_control.backend.control.
"""

import os

VERSION = "0.1.0"

# The AppImage build (packaging/appimage/build.sh) writes its full version here, so a dev build shows
# which commit it is (0.1.0-dev.abc1234); a checkout has no such file and shows VERSION.
try:
    with open(os.path.join(os.path.dirname(__file__), "version")) as file:
        VERSION = file.read().strip() or VERSION
except OSError:
    pass
