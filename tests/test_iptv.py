import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch


class IPTVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DVB_PANEL_DATA_DIR=self.tmp.name)
        self.env.start()
        spec = importlib.util.spec_from_file_location("iptv_test_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        self.panel._auth.set_admin("admin", "admin-test-password")
        self.server = self.panel.ThreadingHTTPServer(("127.0.0.1", 0), self.panel.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port
        self.store = self.panel._iptv
        self.user = {"username": "viewer", "password": "viewer-test-password", "channels": ["tp:10"], "enabled": True}
        self.store.save_user(self.user)
        self.token = self.store.token("viewer")
        self.panel.STATE["transponders"] = [{"id": "tp", "scan": {"services": [{"sid": 10, "name": "Allowed"}, {"sid": 20, "name": "Forbidden"}]}}]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.env.stop()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {},
            data=None if body is None else json.dumps(body).encode())
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode(), response.headers

    def test_private_disk_and_reload(self):
        path = Path(self.tmp.name) / "iptv.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.user["password"], path.read_text())
        reloaded = self.panel.IPTVStore(str(path))
        self.assertTrue(reloaded.allowed(self.token, "tp:10"))
        self.assertNotIn("account", reloaded.listing()[0])

    def test_credentials_and_playlist(self):
        auth = "Basic " + base64.b64encode(b"viewer:viewer-test-password").decode()
        status, text, _ = self.request("/iptv/playlist.m3u8", headers={"Authorization": auth})
        self.assertEqual(status, 200)
        self.assertIn("Allowed", text)
        self.assertNotIn("Forbidden", text)
        self.assertNotIn(self.user["password"], text)
        self.assertIn("/iptv/stream/tp:10?profile=pass&token=", text)
        self.assertEqual(self.request("/iptv/playlist.m3u8")[0], 401)
        self.assertEqual(self.request("/iptv/playlist.m3u8?username=viewer&password=bad")[0], 401)

    def test_multiple_inactive_channels_and_raw_m3u(self):
        self.panel.STATE["transponders"].append({"id": "other", "scan": {"services": [{"sid": 30, "name": "Other mux"}]}})
        self.store.save_user({**self.user, "channels": ["tp:10", "tp:20", "other:30"]})
        token = self.store.token("viewer")
        self.assertFalse(self.panel._streams.status())
        status, text, headers = self.request("/iptv/playlist.m3u8?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn('attachment; filename="canales.m3u8"', headers["Content-Disposition"])
        self.assertEqual(text.count("#EXTINF:"), 3)
        urls = [line for line in text.splitlines() if line.startswith("http")]
        self.assertEqual(len(urls), 3)
        self.assertNotIn("&amp;", text)
        self.assertNotIn("](http", text)
        for url in urls:
            from urllib.parse import urlparse, parse_qs
            self.assertEqual(parse_qs(urlparse(url).query)["token"], [token])
        self.assertFalse(self.panel._streams.status())

    def test_no_admin_or_legacy_access(self):
        for path in ("/api/iptv/users", "/api/transponders", "/api/epg.xml", "/stream/channel/tp:10"):
            self.assertEqual(self.request(path + "?token=" + self.token)[0], 401)
        self.assertEqual(self.request("/api/iptv/users", self.user)[0], 401)

    def test_stream_scope(self):
        self.assertEqual(self.request("/iptv/stream/tp:20?token=" + self.token)[0], 403)
        with patch.object(self.panel.Handler, "_stream_channel", lambda handler, identity: handler._send(200, identity)):
            self.assertEqual(self.request("/iptv/stream/tp:10?token=" + self.token)[:2], (200, "tp:10"))
            self.assertEqual(self.request("/iptv/stream/10?token=" + self.token)[0], 403)

    def test_revocation_expiry_and_tampering(self):
        self.assertFalse(self.store.allowed(self.token + "x", "tp:10"))
        self.store.save_user({**self.user, "password": "", "channels": ["tp:20"]})
        self.assertFalse(self.store.allowed(self.token, "tp:10"))
        token = self.store.token("viewer")
        self.assertTrue(self.store.allowed(token, "tp:20"))
        self.store.save_user({**self.user, "enabled": False})
        self.assertFalse(self.store.allowed(token))
        self.store.save_user({**self.user, "expires": time.time() - 1})
        self.assertIsNone(self.store.credentials("viewer", self.user["password"], "test"))

    def test_flussonic_contract(self):
        backend = "/iptv/flussonic/" + self.store.data["backend_key"]
        status, _, headers = self.request(backend + "?name=tp:10&token=" + self.token + "&request_type=new_session")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-AuthDuration"], "30")
        self.assertEqual(self.request(backend + "?name=dvb_" + "tp:10".encode().hex() + "&token=" + self.token)[0], 200)
        self.assertEqual(self.request(backend + "?name=tp:20&token=" + self.token)[0], 403)
        self.assertEqual(self.request("/iptv/flussonic/wrong?name=tp:10&token=" + self.token)[0], 403)
        self.assertEqual(self.request(backend + "?name=tp:10&token=viewer:viewer-test-password")[0], 200)
        self.store.save_user({**self.user, "enabled": False})
        self.assertEqual(self.request(backend + "?name=tp:10&token=" + self.token + "&request_type=update_session")[0], 403)

    def test_admin_csrf(self):
        status, _, headers = self.request("/api/auth/login", {"username": "admin", "password": "admin-test-password"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/iptv/users", self.user, {"Cookie": cookie})[0], 403)
        _, text, _ = self.request("/api/auth/session", headers={"Cookie": cookie})
        csrf = json.loads(text)["csrf"]
        status, text, _ = self.request("/api/iptv/users", self.user, {"Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(status, 200)
        self.assertTrue(self.store.allowed(json.loads(text)["token"], "tp:10"))

    def test_rate_limit(self):
        for _ in range(10):
            self.assertIsNone(self.store.credentials("viewer", "wrong", "same-ip"))
        self.assertIsNone(self.store.credentials("viewer", self.user["password"], "same-ip"))
        self.assertIsNotNone(self.store.credentials("viewer", self.user["password"], "other-ip"))

    def test_validation(self):
        for fields in ({"username": "bad:name"}, {"channels": "*"}, {"expires": -1}, {"enabled": "false"}, {"password": "short"}):
            with self.assertRaises(ValueError):
                self.store.save_user({**self.user, **fields})
