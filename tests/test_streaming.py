import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from streaming import Session, StreamError, StreamManager

# Real child processes and pipes; no tuner, server or secrets required.
PRODUCER = '''import sys,time
p=bytes([0x47,0,0x100 & 255,0x10])+bytes(184)
while True:
 sys.stdout.buffer.write(p*7); sys.stdout.buffer.flush(); time.sleep(.02)
'''


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = StreamManager()
        self.command = [sys.executable, "-u", "-c", PRODUCER]
        self.log = os.path.join(self.tmp.name, "stream.log")

    def tearDown(self):
        self.manager.stop_all()
        self.tmp.cleanup()

    def acquire(self, key="tp:1", **kwargs):
        return self.manager.acquire(key, 0, self.command, self.log, timeout=2, **kwargs)

    def test_same_channel_reuses_process_and_refcounts(self):
        a = self.acquire()
        b = self.acquire()
        self.assertIs(a, b)
        self.assertEqual(a.clients, 2)
        cursor, data = a.read(a.cursor(), timeout=1)
        self.assertEqual(data[0], 0x47)
        self.assertEqual(len(data) % 188, 0)
        self.manager.release(a)
        self.assertIsNone(b.process.poll())
        self.manager.release(b)
        self.assertIsNotNone(b.process.poll())
        self.assertEqual(self.manager.status(), [])

    def test_concurrent_starts_share_one_process(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.acquire())) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(results), 5)
        self.assertEqual(len({r.process.pid for r in results}), 1)
        for r in results: self.manager.release(r)

    def test_adapter_and_scan_conflicts(self):
        a = self.acquire()
        with self.assertRaises(StreamError) as error:
            self.acquire("other:2")
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(StreamError): self.manager.reserve_scan(0)
        self.manager.release(a)
        self.manager.reserve_scan(0)
        with self.assertRaises(StreamError): self.acquire()
        self.manager.release_scan(0)

    def test_manual_pin_and_stop(self):
        a = self.acquire(pinned=True)
        self.manager.release(a)
        self.assertEqual(a.clients, 0)
        self.assertIsNone(a.process.poll())
        b = self.acquire()
        self.assertIs(a, b)
        self.manager.release(b)
        self.manager.stop_all()
        self.assertIsNotNone(a.process.poll())

    def test_start_failure_reaps_process(self):
        with self.assertRaises(StreamError):
            self.manager.acquire("tp:1", 0, [sys.executable, "-c", "raise SystemExit(1)"],
                                 self.log, timeout=2)
        self.assertEqual(self.manager.status(), [])

    def test_start_timeout_reaps_process(self):
        with self.assertRaises(StreamError) as error:
            self.manager.acquire("tp:1", 0, [sys.executable, "-c", "import time; time.sleep(30)"],
                                 self.log, timeout=.1)
        self.assertEqual(error.exception.status, 504)
        self.assertEqual(self.manager.status(), [])

    def test_slow_subscriber_does_not_block_producer(self):
        a = self.acquire()
        with a.condition:
            a.blocks.clear()
            a.buffered = 0
            a.first = a.next
            old = a.first
            a.max_bytes = 188
        a._append(bytes([0x47]) + bytes(187))
        a._append(bytes([0x47]) + bytes(187))
        with self.assertRaises(StreamError): a.read(old)
        self.assertEqual(a.overruns, 1)
        self.manager.release(a)

    def test_command_not_in_status(self):
        a = self.acquire()
        self.assertNotIn("command", a.status())
        self.assertEqual(os.stat(self.log).st_mode & 0o777, 0o600)
        self.manager.release(a)


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DVB_PANEL_DATA_DIR"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("review_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        self.panel.STATE["transponders"] = [
            {"id": tid, "adapter": i, "freq_mhz": 10970,
             "scan": {"services": [{"sid": 672, "name": 'Name"\nInjected', "now": "Show"}]}}
            for i, tid in enumerate(("tp-a", "tp-b"))]

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DVB_PANEL_DATA_DIR", None)

    def test_identity_and_xmltv_agree(self):
        playlist = self.panel.build_m3u8("http://host/stream/channel/{channel_id}")
        self.assertIn('tvg-id="tp-a:672"', playlist)
        self.assertIn('tvg-id="tp-b:672"', playlist)
        self.assertIn("http://host/stream/channel/tp-a:672?profile=pass", playlist)
        self.assertEqual(len(playlist.splitlines()), 5)
        xml = self.panel.build_xmltv()
        self.assertIn('channel id="tp-a:672"', xml)
        self.assertIn('channel="tp-b:672"', xml)
        with self.assertRaises(StreamError) as error: self.panel.resolve_channel("672")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.panel.resolve_channel("tp-b:672")[0]["adapter"], 1)

    def test_legacy_unique_sid_and_custom_template(self):
        self.panel.STATE["transponders"].pop()
        self.assertEqual(self.panel.resolve_channel("672")[1], 672)
        playlist = self.panel.build_m3u8("http://host/{transponder}/{sid}?profile=pass")
        self.assertIn("http://host/tp-a/672?profile=pass", playlist)
        self.assertNotIn("profile=pass&profile=pass", playlist)


class HttpSubscriptionTests(unittest.TestCase):
    def setUp(self):
        import urllib.request
        self.request = urllib.request
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = os.environ.get("DVB_PANEL_DATA_DIR")
        os.environ["DVB_PANEL_DATA_DIR"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("http_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        self.binary = os.path.join(self.tmp.name, "producer")
        with open(self.binary, "w") as script:
            script.write("#!" + sys.executable + "\n" + PRODUCER)
        os.chmod(self.binary, 0o700)
        self.panel._cfg["tbsstream"] = self.binary
        self.panel.STATE["transponders"] = [{"id": "tp-a", "adapter": 0,
            "type": "dvbs2", "freq_mhz": 10970, "symbol_rate": 27900000,
            "scan": {"services": [{"sid": 672}]}}]
        self.server = self.panel.ThreadingHTTPServer(("127.0.0.1", 0), self.panel.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.panel.stream_stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.old_env is None: os.environ.pop("DVB_PANEL_DATA_DIR", None)
        else: os.environ["DVB_PANEL_DATA_DIR"] = self.old_env
        self.tmp.cleanup()

    def test_http_and_manual_share_session(self):
        result = self.panel.stream_start(self.panel.STATE["transponders"][0], None, [], 0, 672)
        self.assertTrue(result["ok"], result)
        with self.request.urlopen(self.base + "/stream/channel/tp-a:672?profile=pass", timeout=2) as a:
            self.assertEqual(a.headers["Content-Type"], "video/mp2t")
            self.assertEqual(a.read(188)[0], 0x47)
            with self.request.urlopen(self.base + "/stream/channelid/672", timeout=2) as b:
                self.assertEqual(b.read(188)[0], 0x47)
                status = self.panel.stream_status()
                self.assertEqual(len(status["sessions"]), 1)
                self.assertEqual(status["pid"], result["pid"])
                self.assertEqual(status["clients"], 2)
                with self.request.urlopen(self.base + "/api/stream/log?channel=tp-a:672") as log:
                    self.assertIn(b"New shared TS session", log.read())

    def test_failed_start_is_not_http_200(self):
        import urllib.error
        with open(self.binary, "w") as script:
            script.write("#!" + sys.executable + "\nraise SystemExit(1)\n")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request.urlopen(self.base + "/stream/channel/tp-a:672", timeout=2)
        self.assertEqual(error.exception.code, 503)
        self.assertFalse(self.panel.stream_status()["running"])

    def test_unknown_profile_rejected(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request.urlopen(self.base + "/stream/channel/tp-a:672?profile=transcode", timeout=2)
        self.assertEqual(error.exception.code, 400)
