import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from auth import AuthStore


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [1000]
        self.auth = AuthStore(
            os.path.join(self.tmp.name, "auth.json"), clock=lambda: self.now[0]
        )
        self.auth.iterations = 1000  # HTTP tests below use production PBKDF2 settings.
        self.auth.set_admin("admin", "a-long-test-password")

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_private_file_and_restart(self):
        saved = Path(self.auth.path).read_text()
        self.assertNotIn("a-long-test-password", saved)
        self.assertEqual(os.stat(self.auth.path).st_mode & 0o777, 0o600)
        restored = AuthStore(self.auth.path)
        token, status = restored.login("admin", "a-long-test-password", "ip")
        self.assertEqual(status, 200)
        self.assertTrue(restored.session("dvb_session=" + token))

    def test_expiry_logout_reset_and_cookies(self):
        token, _ = self.auth.login("admin", "a-long-test-password", "ip")
        cookie = "dvb_session=" + token
        self.assertTrue(self.auth.session(cookie))
        self.assertIn("HttpOnly; SameSite=Strict", self.auth.cookie(token))
        self.auth.secure = True
        self.assertIn("; Secure", self.auth.cookie(token))
        self.now[0] += self.auth.ttl
        self.assertIsNone(self.auth.session(cookie))
        token, _ = self.auth.login("admin", "a-long-test-password", "ip")
        self.auth.logout("dvb_session=" + token)
        self.assertIsNone(self.auth.session("dvb_session=" + token))
        token, _ = self.auth.login("admin", "a-long-test-password", "ip")
        self.auth.set_admin("admin", "another-long-password")
        self.assertIsNone(self.auth.session("dvb_session=" + token))

    def test_rate_limit_and_recovery(self):
        for _ in range(10):
            self.assertEqual(self.auth.login("wrong", "wrong", "ip")[1], 401)
        self.assertEqual(self.auth.login("admin", "a-long-test-password", "ip")[1], 429)
        self.now[0] += 601
        self.assertEqual(self.auth.login("admin", "a-long-test-password", "ip")[1], 200)

    def test_media_rotation_hash_and_scope(self):
        old = self.auth.rotate_media_token()
        self.assertTrue(self.auth.media_allowed(old))
        self.assertNotIn(old, Path(self.auth.path).read_text())
        new = self.auth.rotate_media_token()
        self.assertFalse(self.auth.media_allowed(old))
        self.assertTrue(AuthStore(self.auth.path).media_allowed(new))
        self.assertIsNone(self.auth.session("dvb_session=" + new))
        with self.assertRaises(ValueError):
            self.auth.set_admin("admin", "short")


class AuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DVB_PANEL_DATA_DIR": self.tmp.name})
        self.env.start()
        spec = importlib.util.spec_from_file_location("secure_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        self.panel._auth = AuthStore(os.path.join(self.tmp.name, "auth.json"))
        self.panel._auth.set_admin("admin", "a-long-test-password")
        self.server = self.panel.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.panel.Handler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.env.stop()
        self.tmp.cleanup()

    def request(self, path, body=None, cookie="", csrf="", extra=None):
        headers = {
            "Content-Type": "application/json",
            "Cookie": cookie,
            "X-CSRF-Token": csrf,
        }
        headers.update(extra or {})
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def login(self):
        status, headers, _ = self.request(
            "/api/auth/login", {"username": "admin", "password": "a-long-test-password"}
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";")[0]
        status, _, body = self.request("/api/auth/session", cookie=cookie)
        return cookie, json.loads(body)["csrf"]

    def test_protected_routes_and_public_login(self):
        for path in (
            "/api/status",
            "/api/diag",
            "/api/newcamd",
            "/api/events",
            "/api/playlist.m3u8",
            "/api/epg.xml",
            "/stream/channel/a:1",
            "/static/app.js",
            "/static/../server.py",
        ):
            self.assertEqual(self.request(path)[0], 401, path)
        self.assertEqual(self.request("/api/stream/stop", {})[0], 401)
        status, headers, body = self.request("/login")
        self.assertEqual(status, 200)
        self.assertIn(b"loginForm", body)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/static/login.js")[0], 200)
        self.assertEqual(self.request("/static/logo-discovery-kids.png")[0], 200)

    def test_import_requires_admin_and_csrf(self):
        self.assertEqual(self.request('/api/import/astra/preview',{})[0],401)
        cookie, csrf=self.login()
        self.assertEqual(self.request('/api/import/astra/preview',{},cookie)[0],403)

    def test_diagnostics_authenticated_csrf_local_only_and_no_parameters(self):
        from types import SimpleNamespace
        self.assertEqual(self.request("/api/diag/selftest", {})[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("/api/diag/selftest", {}, cookie)[0], 403)
        self.assertEqual(self.request("/api/diag/selftest", {}, cookie, csrf,
                                     {"Origin": "https://evil.test"})[0], 403)
        token = self.panel._auth.rotate_media_token()
        self.assertEqual(self.request("/api/diag?token=" + token)[0], 401)
        with patch.object(self.panel.subprocess, "run") as run:
            code, _, body = self.request("/api/diag", cookie=cookie)
            self.assertEqual(code, 200)
            self.assertIs(json.loads(body)["network_probe"], False)
            run.assert_not_called()
            self.assertEqual(self.request("/api/diag/selftest", {"host": "evil.test"}, cookie, csrf)[0], 400)
            run.assert_not_called()
        with patch.object(self.panel, "find_ncdiag", return_value="/fixed/dvb-ncdiag"), patch.object(
                self.panel.subprocess, "run", return_value=SimpleNamespace(returncode=0,
                    stdout=b'{"ok":true,"csa":true,"network_probe":false}')) as run:
            code, _, body = self.request("/api/diag/selftest", {}, cookie, csrf)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["csa"])
            self.assertEqual(run.call_args.args[0], ["/fixed/dvb-ncdiag", "--selftest", "--json"])
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertEqual(run.call_args.kwargs["stderr"], self.panel.subprocess.DEVNULL)
        self.assertEqual(list(Path(self.tmp.name).glob("*.log")), [])

    def test_selftest_errors_sanitized_and_concurrency_bounded(self):
        from types import SimpleNamespace
        cookie, csrf = self.login()
        with patch.object(self.panel, "find_ncdiag", return_value=None):
            self.assertEqual(self.request("/api/diag/selftest", {}, cookie, csrf)[0], 503)
        with patch.object(self.panel, "find_ncdiag", return_value="/fixed/dvb-ncdiag"), patch.object(
                self.panel.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"SECRET_SENTINEL")):
            code, _, body = self.request("/api/diag/selftest", {}, cookie, csrf)
            self.assertEqual(code, 502)
            self.assertNotIn(b"SECRET_SENTINEL", body)
        self.panel._diag_lock.acquire()
        try:
            self.assertEqual(self.request("/api/diag/selftest", {}, cookie, csrf)[0], 429)
        finally:
            self.panel._diag_lock.release()

    def test_login_csrf_origin_and_logout(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("/api/status", cookie=cookie)[0], 200)
        self.assertEqual(self.request("/api/epg/settings", {"days": 2}, cookie)[0], 403)
        self.assertEqual(
            self.request(
                "/api/epg/settings",
                {"days": 2},
                cookie,
                csrf,
                {"Origin": "https://evil.test"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request("/api/epg/settings", {"days": 2}, cookie, csrf)[0], 200
        )
        self.assertEqual(self.request("/api/auth/logout", {}, cookie, csrf)[0], 200)
        self.assertEqual(self.request("/api/status", cookie=cookie)[0], 401)

    def test_token_read_only_private_playlist_and_revocation(self):
        cookie, csrf = self.login()
        status, _, body = self.request("/api/auth/media-token", {}, cookie, csrf)
        token = json.loads(body)["token"]
        self.panel.STATE["transponders"] = [
            {"id": "a", "scan": {"services": [{"sid": 1, "name": "Test"}]}}
        ]
        self.assertEqual(self.request("/api/status?token=" + token)[0], 401)
        status, _, playlist = self.request("/api/playlist.m3u8?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn(("token=" + token).encode(), playlist)
        self.assertEqual(self.request("/api/epg.xml?token=" + token)[0], 200)
        self.assertEqual(
            self.request(
                "/api/playlist.m3u8?token="
                + token
                + "&base_url=https://evil.test/stream"
            )[0],
            400,
        )
        self.request("/api/auth/media-token", {}, cookie, csrf)
        self.assertEqual(self.request("/api/epg.xml?token=" + token)[0], 401)

    def test_https_cookie_and_export_scheme(self):
        self.panel._auth.secure = True
        status, headers, body = self.request(
            "/api/auth/login", {"username": "admin", "password": "a-long-test-password"}
        )
        self.assertIn("; Secure", headers["Set-Cookie"])
        self.assertIn("max-age=", headers["Strict-Transport-Security"])
        token = self.panel._auth.rotate_media_token()
        self.panel.STATE["transponders"] = [
            {"id": "a", "scan": {"services": [{"sid": 1, "name": "Discovery Kids HD"}]}}
        ]
        status, _, playlist = self.request("/api/playlist.m3u8?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn(
            (
                "https://127.0.0.1:" + str(self.server.server_port) + "/stream/channel/"
            ).encode(),
            playlist,
        )
        self.assertNotIn(b"http://", playlist)
