# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m side_control.app [--hidden]  (--hidden: start in the background, for Launch at Login)"""
import ctypes.util
import os
import sys

# gtk4-layer-shell (the on-screen edge indicator) must be loaded before libwayland: start over with it
# preloaded
LAYER_SHELL = ctypes.util.find_library("gtk4-layer-shell")
if (LAYER_SHELL and os.environ.get("WAYLAND_DISPLAY") and LAYER_SHELL not in os.environ.get("LD_PRELOAD", "")
        and not os.environ.get("SIDE_CONTROL_PRELOADED")):  # an AppImage may clear LD_PRELOAD: no loop
    os.environ["LD_PRELOAD"] = " ".join(filter(None, [LAYER_SHELL, os.environ.get("LD_PRELOAD")]))
    os.environ["SIDE_CONTROL_PRELOADED"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])

from .application import main  # noqa: E402

main()
