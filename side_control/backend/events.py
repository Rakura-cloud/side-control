# SPDX-License-Identifier: GPL-3.0-or-later
"""Log lines and events for the app, and the terminal they are printed on."""

import threading
import time
from collections import deque


TERM_LOCK = threading.RLock()  # log lines and the proximity panel share the terminal
EVENTS = deque(maxlen=2000)    # log lines and RSSI readings for GUI clients; any thread may append


def log(msg):
    stamp = time.strftime("%H:%M:%S")
    EVENTS.append({"type": "log", "time": stamp, "msg": msg})
    with TERM_LOCK:
        print(stamp, msg, flush=True)
