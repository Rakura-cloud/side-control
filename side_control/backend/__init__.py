# SPDX-License-Identifier: GPL-3.0-or-later
"""The backend: makes this computer a Bluetooth keyboard + mouse for each paired device, reads the
keyboard and touchpad, hands the pointer over at the screen edges, and serves the app's socket.

Ctrl+Alt+P switches the keyboard and touchpad between the PC and the connected devices (PC -> first
device -> next device ... -> PC). With bluetoothd started with `-P input` (data/bluetooth-no-input.conf)
devices can also connect to it; otherwise it connects to them.

It runs as the user, through side-control@<uid>.service (data/side-control@.service), which gives it
the input group and the few capabilities its Bluetooth calls need.
"""
