# SPDX-License-Identifier: GPL-3.0-or-later
"""The clipboard server and the setup code, no hardware needed: python -m unittest discover tests"""
import http.client
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import side_control.app  # noqa: E402,F401  (picks the GTK versions)
from side_control.app import clipboard  # noqa: E402
from side_control.app.config import CLIPBOARD_PORT  # noqa: E402


class Server(unittest.TestCase):
    """A real server on 127.0.0.1, with a fake clipboard and a fake backend."""

    def setUp(self):
        self.text, self.sent = "on the PC", []
        self.server = clipboard.ClipboardServer("token-1", lambda: self.text,
                                                lambda t: setattr(self, "text", t),
                                                lambda **msg: self.sent.append(msg), port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def request(self, method, path, body=None, token="token-1", header=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        headers = {}
        if token is not None:
            if header:
                headers["X-Side-Control-Token"] = token
            else:
                path += ("&" if "?" in path else "?") + "token=" + token
        conn.request(method, path, body=body, headers=headers)
        reply = conn.getresponse()
        data = reply.read()
        conn.close()
        return reply.status, data

    def test_token_check(self):
        for token in (None, "", "wrong", "token-12"):
            with self.subTest(token=token):
                status, body = self.request("GET", "/ping", token=token)
                self.assertEqual(status, 401)
                self.assertEqual(body, b"missing or wrong token")
        self.assertEqual(self.request("GET", "/ping"), (200, b"pong"))

    def test_token_in_query(self):
        self.assertEqual(self.request("GET", "/clipboard", header=False), (200, b"on the PC"))

    def test_round_trip(self):
        self.assertEqual(self.request("POST", "/clipboard", body="from the phone".encode()), (200, b"ok"))
        self.assertEqual(self.text, "from the phone")
        self.assertEqual(self.request("GET", "/clipboard"), (200, b"from the phone"))

    def test_body_limit(self):
        status, _body = self.request("POST", "/clipboard", body=b"x" * (clipboard.MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertEqual(self.text, "on the PC")

    def test_body_must_be_utf8(self):
        status, _body = self.request("POST", "/clipboard", body=b"\xff\xfe")
        self.assertEqual(status, 400)

    def test_return_gives_the_pointer_back(self):
        self.assertEqual(self.request("POST", "/return"), (200, b"ok"))
        self.assertEqual(self.sent, [{"cmd": "set_target", "address": None}])

    def test_unknown_paths(self):
        self.assertEqual(self.request("GET", "/other")[0], 404)
        self.assertEqual(self.request("POST", "/other", body=b"")[0], 404)


class SetupCode(unittest.TestCase):
    def test_json_has_url_and_token(self):
        data = json.loads(clipboard.setup_string("http://pc.local:47913", "abc"))
        self.assertEqual(data, {"url": "http://pc.local:47913", "token": "abc"})

    def test_url_uses_the_fixed_port(self):
        with mock.patch.object(clipboard, "setup_host", return_value="pc.local"):
            self.assertEqual(clipboard.setup_url(), f"http://pc.local:{CLIPBOARD_PORT}")

    def test_host_is_mdns_while_avahi_runs(self):
        net = mock.MagicMock()
        net.run.return_value = mock.Mock(returncode=0)
        net.gethostname.return_value = "pc"
        with mock.patch.object(clipboard, "subprocess", net), mock.patch.object(clipboard, "socket", net):
            self.assertEqual(clipboard.setup_host(), "pc.local")

    def test_host_is_the_lan_address_without_avahi(self):
        net = mock.MagicMock()
        net.run.return_value = mock.Mock(returncode=1)
        sock = net.socket.return_value.__enter__.return_value
        sock.getsockname.return_value = ("192.168.1.5", 33333)
        with mock.patch.object(clipboard, "subprocess", net), mock.patch.object(clipboard, "socket", net):
            self.assertEqual(clipboard.setup_host(), "192.168.1.5")

    def test_token_is_kept(self):
        settings = {}
        with mock.patch.object(clipboard, "save_settings") as save:
            token = clipboard.new_token(settings)
            self.assertTrue(token)
            self.assertEqual(clipboard.new_token(settings), token)
            self.assertEqual(save.call_count, 1)


if __name__ == "__main__":
    unittest.main()
