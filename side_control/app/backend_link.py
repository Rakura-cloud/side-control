# SPDX-License-Identifier: GPL-3.0-or-later
"""The connection to the backend's unix socket."""

import json
import socket

from gi.repository import GLib

from .config import SOCK_PATH


class Backend:
    """Connection to the backend's control socket. Reconnects by itself."""

    def __init__(self, on_message, on_status):
        self.on_message, self.on_status = on_message, on_status
        self.sock = None
        self.buf = b""
        self.reason = None
        GLib.timeout_add_seconds(2, self._retry)
        self._retry()

    def _retry(self):
        if self.sock is None:
            self._connect()
        return GLib.SOURCE_CONTINUE

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(SOCK_PATH)
        except OSError as err:
            sock.close()
            reason = ("not running" if isinstance(err, (FileNotFoundError, ConnectionRefusedError))
                      else str(err))
            if reason != self.reason:
                self.reason = reason
                self.on_status(False, reason)
            return
        sock.setblocking(False)
        self.sock, self.buf, self.reason = sock, b"", None
        GLib.io_add_watch(sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._readable)
        self.on_status(True, None)

    def _readable(self, fd, condition):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            data = b""
        if not data:
            self.sock.close()
            self.sock = None
            self.reason = "not running"
            self.on_status(False, self.reason)
            return GLib.SOURCE_REMOVE
        *lines, self.buf = (self.buf + data).split(b"\n")
        for line in lines:
            try:
                self.on_message(json.loads(line))
            except ValueError:
                pass
        return GLib.SOURCE_CONTINUE

    def send(self, **msg):
        if self.sock:
            try:
                self.sock.send((json.dumps(msg) + "\n").encode())
            except OSError:
                pass
