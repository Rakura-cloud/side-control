# SPDX-License-Identifier: GPL-3.0-or-later
"""Side Control: use a Linux computer's keyboard and touchpad on iPhones and iPads.

app/      the GTK window and tray, running as the user
backend/  the Bluetooth keyboard + mouse, running as root
They talk JSON over a unix socket: see side_control.backend.control.
"""

VERSION = "0.1.0"
