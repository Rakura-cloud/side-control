# SPDX-License-Identifier: GPL-3.0-or-later
"""The clipboard server behind Share Clipboard: what the Side Control Shortcut on a device talks to,
and the setup code (address and token) the dialog shows as a QR code.

Text only, over plain HTTP on the LAN. The server is off until the user turns Share Clipboard on;
every request needs the per-install token, checked in constant time. The PC's clipboard is read and
written on the GTK main thread (Gdk), or through wl-paste / wl-copy when they are on PATH: Gdk cannot
read the clipboard of a background window on GNOME. Neither is a dependency.
"""

import hmac
import json
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gi.repository import Gdk, GLib, GObject

from .config import CLIPBOARD_PORT, save_settings

MAX_BODY = 1024 * 1024  # 1 MB of text is plenty; a longer body gets 413 without being read
CLIP_TIMEOUT_S = 2.0    # how long a request waits for the GTK main loop or wl-paste before giving up


def setup_host():
    """The address the Shortcut should use: <hostname>.local while mDNS (avahi) runs, since iOS
    resolves it and DHCP likes to change IPs. The LAN IPv4 address otherwise, found by letting the
    kernel pick a route (a UDP connect sends no packet)."""
    try:
        mdns = subprocess.run(["systemctl", "is-active", "--quiet", "avahi-daemon"],
                              timeout=CLIP_TIMEOUT_S).returncode == 0
    except (OSError, subprocess.SubprocessError):
        mdns = False
    if mdns:
        return socket.gethostname() + ".local"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: routed nowhere, so nothing leaves the machine
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostname()


def setup_url():
    return f"http://{setup_host()}:{CLIPBOARD_PORT}"


def setup_string(url, token):
    """What the setup QR holds: the server's URL and token, as JSON the Shortcut parses."""
    return json.dumps({"url": url, "token": token}, separators=(",", ":"))


def new_token(settings):
    """The per-install token, random once and then kept in the settings."""
    if not settings.get("clipboard_token"):
        settings["clipboard_token"] = secrets.token_urlsafe(16)
        save_settings(settings)
    return settings["clipboard_token"]


def _gtk_call(start, timeout=CLIP_TIMEOUT_S):
    """Run `start(done)` on the GTK main thread, which owns the clipboard, and return what it passes
    to `done`. From another thread an Event waits for the main loop; on the main thread itself (the
    dialog's buttons) the events are delivered here, since waiting would wait for oneself."""
    event, result = threading.Event(), [None]

    def done(value):
        result[0] = value
        event.set()

    def run():
        try:
            start(done)
        except (GLib.Error, AttributeError):
            done(None)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(run)
    if threading.current_thread() is threading.main_thread():
        context, deadline = GLib.MainContext.default(), time.monotonic() + timeout
        while not event.is_set() and time.monotonic() < deadline:
            if not context.iteration(False):
                time.sleep(0.002)
    else:
        event.wait(timeout)
    return result[0]


def read_text():
    """The PC clipboard's text, callable from any thread."""
    paste = shutil.which("wl-paste")
    if paste:
        try:
            done = subprocess.run([paste, "--no-newline", "--type", "text"], capture_output=True,
                                  timeout=CLIP_TIMEOUT_S)  # text only: not an image's bytes
            if done.returncode == 0:
                return done.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            pass

    def start(done):
        clipboard = Gdk.Display.get_default().get_clipboard()

        def got(_clipboard, res):
            try:
                done(clipboard.read_text_finish(res))
            except GLib.Error:
                done(None)
        clipboard.read_text_async(None, got)
    return _gtk_call(start) or ""


def write_text(text):
    """`text` onto the PC clipboard, callable from any thread."""
    copy = shutil.which("wl-copy")
    if copy:
        try:
            done = subprocess.run([copy], input=text.encode(), timeout=CLIP_TIMEOUT_S)
            if done.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass

    def start(done):
        Gdk.Display.get_default().get_clipboard().set(GObject.Value(GObject.TYPE_STRING, text))
        done(None)
    _gtk_call(start)


class _Handler(BaseHTTPRequestHandler):
    """One request from the Shortcut: the token first, then the endpoint."""

    protocol_version = "HTTP/1.1"
    timeout = 10  # seconds a connection may sit idle: one that never sends would hold a thread

    def do_GET(self):
        if not self.token_ok():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/ping":
            self.reply(200, b"pong")
        elif path == "/clipboard":
            self.reply(200, self.server.owner.read().encode("utf-8"))
        else:
            self.reply(404, b"not found")

    def do_POST(self):
        if not self.token_ok():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/return":
            self.server.owner.send(cmd="set_target", address=None)  # the pointer goes back to the PC
            self.reply(200, b"ok")
            return
        if path != "/clipboard":
            self.reply(404, b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.reply(413, b"too much text")
            return
        try:
            text = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            self.reply(400, b"not UTF-8 text")
            return
        self.server.owner.write(text)
        self.reply(200, b"ok")

    def token_ok(self):
        sent = self.headers.get("X-Side-Control-Token")
        if sent is None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sent = (query.get("token") or [None])[0]
        try:
            if sent is not None and hmac.compare_digest(sent, self.server.owner.token):
                return True
        except TypeError:  # compare_digest wants ASCII strings
            pass
        self.reply(401, b"missing or wrong token")
        return False

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # a 413 or 401 leaves the body unread; start over
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except OSError:  # the Shortcut went away
            pass

    def log_message(self, *args):  # not every request is worth a line on the terminal
        pass


class ClipboardServer:
    """The HTTP server, in a daemon thread. `read` and `write` touch the PC's clipboard, `send` talks
    to the backend. `port` 0 picks a free one (the tests); the dialog always shows CLIPBOARD_PORT."""

    def __init__(self, token, read, write, send, port=CLIPBOARD_PORT):
        self.token, self.read, self.write, self.send = token, read, write, send
        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self.httpd.owner = self

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
