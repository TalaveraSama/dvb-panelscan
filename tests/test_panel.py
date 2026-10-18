import importlib.util
import os
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from unittest.mock import patch

class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DVB_PANEL_DATA_DIR=self.tmp.name)
        self.env.start()
        spec = importlib.util.spec_from_file_location("panel_server", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_private_atomic_configuration(self):
        self.panel._save("newcamd")
        path = os.path.join(self.tmp.name, "newcamd.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(path + ".tmp"))
        self.assertEqual(self.panel.STATE["newcamd"]["pass"], "")

    def test_http_validation(self):
        server = self.panel.ThreadingHTTPServer(("127.0.0.1", 0), self.panel.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            for path, body, headers, code in [
                ("/static/../server.py", None, {}, 403),
                ("/static/%2e%2e/server.py", None, {}, 403),
                ("/api/newcamd", b'{"port":"bad"}', {}, 400),
                ("/api/newcamd", b'[]', {}, 400),
                ("/api/newcamd", b'{}', {"Origin":"https://evil.example"}, 403),
            ]:
                req = urllib.request.Request(base + path, data=body, headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(req, timeout=2)
                self.assertEqual(caught.exception.code, code)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_no_hardware(self):
        self.assertFalse(self.panel.stream_status()["running"])

if __name__ == "__main__":
    unittest.main()
