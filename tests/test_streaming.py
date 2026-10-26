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


# Simulates the three CLI roles, not three independent tuner processes.
FAKE_ENGINE = r'''import os,sys,time,socket
if "--ca-broker" in sys.argv:
 s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[sys.argv.index("--ca-broker")+1]); s.listen()
 while True: time.sleep(1)
elif "--stdin" in sys.argv:
 while True:
  data=os.read(0,188*256)
  if not data: break
  sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
else:
 p=bytes([0x47,0,0,0x10])+bytes(184)
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

    def test_reconnect_grace_reuses_worker_and_ignores_old_expiry(self):
        self.manager.reconnect_grace = 10
        a = self.acquire()
        pid = a.process.pid
        self.manager.release(a)
        token = self.manager._idle[(0, "tp:1")][0]
        self.assertTrue(a.status()["reconnect_waiting"])
        b = self.acquire()
        self.assertIs(a, b)
        self.assertEqual(b.process.pid, pid)
        self.assertFalse(b.status()["reconnect_waiting"])
        self.manager._expire_idle(a, token)
        self.assertIsNone(b.process.poll())
        self.manager.release(b)
        new_token = self.manager._idle[(0, "tp:1")][0]
        self.manager._expire_idle(a, token)  # cannot expire the renewed idle period
        self.assertIsNone(b.process.poll())
        self.manager._expire_idle(b, new_token)
        self.assertIsNotNone(b.process.poll())
        self.assertEqual(self.manager.sessions, {})
        self.assertEqual(self.manager._idle, {})

    def test_reconnect_timer_expires_without_another_http_request(self):
        self.manager.reconnect_grace = .1
        a = self.acquire()
        self.manager.release(a)
        a.process.wait(timeout=3)
        with self.manager.lock:
            self.assertEqual(self.manager.sessions, {})
            self.assertEqual(self.manager._idle, {})

    def test_explicit_stop_cancels_retention_and_stale_release_is_safe(self):
        self.manager.reconnect_grace = 10
        a = self.acquire()
        self.manager.release(a)
        token = self.manager._idle[(0, "tp:1")][0]
        self.manager.stop_channel("tp:1")
        self.assertIsNotNone(a.process.poll())
        b = self.acquire()
        self.manager.release(b)
        self.manager.release(a)
        self.manager._expire_idle(a, token)
        self.assertIsNone(b.process.poll())
        self.assertIn((0, "tp:1"), self.manager._idle)
        self.manager.stop_all()
        self.assertEqual(self.manager._idle, {})

    def test_scan_evicts_idle_but_does_not_evict_a_live_subscription(self):
        self.manager.reconnect_grace = 10
        a = self.acquire()
        with self.assertRaises(StreamError): self.manager.reserve_scan(0)
        self.manager.release(a)
        self.manager.reserve_scan(0)
        self.assertIsNotNone(a.process.poll())
        self.manager.release_scan(0)

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

    def test_stop_pinned_channel_allows_another_sid(self):
        a = self.acquire(pinned=True)
        self.manager.release(a)
        with self.assertRaises(StreamError) as error:
            self.acquire("tp:2")
        self.assertIn("tp:1", str(error.exception))
        self.assertIn("manual fijo", str(error.exception))
        self.assertTrue(self.manager.stop_channel("tp:1"))
        self.assertIsNotNone(a.process.poll())
        b = self.acquire("tp:2")
        self.assertEqual(b.key, "tp:2")
        self.assertFalse(self.manager.stop_channel("missing"))
        self.manager.release(b)

    def test_individual_stop_keeps_other_adapter(self):
        a = self.acquire(pinned=True)
        b = self.manager.acquire("other:2", 1, self.command,
                                 os.path.join(self.tmp.name, "other.log"), timeout=2)
        self.assertTrue(self.manager.stop_channel("tp:1"))
        self.assertIsNone(b.process.poll())
        self.manager.release(a)
        self.manager.release(b)

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
        self.assertFalse(os.path.exists(self.log))
        self.manager.release(a)


class MuxTests(unittest.TestCase):
    setUp = StreamingTests.setUp
    tearDown = StreamingTests.tearDown
    def acquire_mux(self, key="tp:1", adapter=0, mux_id="mux-a", **kwargs):
        return self.manager.acquire(key, adapter,
            [sys.executable, "-u", "-c", "import sys;\nwhile True:\n b=sys.stdin.buffer.read(188*7)\n if not b: break\n sys.stdout.buffer.write(b); sys.stdout.buffer.flush()"],
            self.log, mux_id=mux_id, mux_command=self.command,
            mux_log=os.path.join(self.tmp.name, "mux-%s.log" % adapter), timeout=2, **kwargs)

    def test_two_services_one_capture_and_independent_stop(self):
        a = self.acquire_mux()
        b = self.acquire_mux("tp:2")
        self.assertIs(a.source, b.source)
        self.assertNotEqual(a.process.pid, b.process.pid)
        self.assertEqual(a.status()["mux_pid"], b.status()["mux_pid"])
        self.manager.stop_channel("tp:1")
        self.assertIsNone(b.source.process.poll())
        self.assertEqual(b.read(b.cursor(), timeout=1)[1][0], 0x47)
        source = b.source
        self.manager.release(b)
        self.assertIsNotNone(source.process.poll())
        self.assertEqual(self.manager.muxes, {})

    def test_channel_change_does_not_keep_old_idle_ecm_requests(self):
        self.manager.reconnect_grace = 10
        a = self.acquire_mux()
        self.manager.release(a)
        b = self.acquire_mux("tp:2")
        self.assertIsNotNone(a.process.poll())
        self.assertEqual(len(self.manager.sessions), 1)
        # Also cover the new HTTP request arriving before the old disconnect.
        c = self.acquire_mux("tp:3")
        self.manager.release(b)
        self.assertIsNotNone(b.process.poll())
        self.assertIsNone(c.process.poll())
        self.assertNotIn((0, "tp:2"), self.manager._idle)

    def test_concurrent_different_services_share_one_capture(self):
        results, errors = [], []
        def start(index):
            try: results.append(self.acquire_mux("tp:%s" % (index % 2 + 1)))
            except Exception as error: errors.append(error)
        threads = [threading.Thread(target=start, args=(i,)) for i in range(10)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 10)
        self.assertEqual(len({s.source.process.pid for s in results}), 1)
        self.assertEqual(len({s.process.pid for s in results}), 2)
        for session in results: self.manager.release(session)
        self.assertEqual(self.manager.muxes, {})

    def test_distinct_muxes_and_scan_still_conflict_on_same_adapter(self):
        a = self.acquire_mux()
        with self.assertRaises(StreamError) as error:
            self.acquire_mux("other:2", mux_id="mux-b")
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(StreamError): self.manager.reserve_scan(0)
        b = self.acquire_mux("other:2", adapter=1, mux_id="mux-b")
        self.assertIsNot(a.source, b.source)

    def test_mux_process_failure_ends_all_its_workers(self):
        a = self.acquire_mux()
        b = self.acquire_mux("tp:2")
        a.source.stop()
        a.worker.join(timeout=2); b.worker.join(timeout=2)
        self.assertTrue(a.ended and b.ended)
        self.assertTrue(a.input_error and b.input_error)

    def test_slow_worker_does_not_block_mux_or_other_service(self):
        a = self.acquire_mux()
        b = self.acquire_mux("tp:2")
        import signal
        os.kill(a.process.pid, signal.SIGSTOP)
        try:
            # Give its feeder a full pipe, but keep the source bounded.
            for _ in range(100): a.source._append((bytes([0x47])+bytes(187))*64)
            before = b.bytes_received
            deadline = time.monotonic() + 2
            while b.bytes_received <= before and time.monotonic() < deadline: time.sleep(.01)
            self.assertGreater(b.bytes_received, before)
            self.assertIsNone(b.source.process.poll())
        finally:
            os.kill(a.process.pid, signal.SIGCONT)

    def test_failed_worker_does_not_leave_capture(self):
        with self.assertRaises(StreamError):
            self.manager.acquire("tp:1", 0, [sys.executable,"-c","raise SystemExit(1)"],
                self.log, mux_id="mux-a", mux_command=self.command,
                mux_log=os.path.join(self.tmp.name,"mux.log"), timeout=2)
        self.assertEqual(self.manager.muxes, {})


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DVB_PANEL_DATA_DIR"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("review_panel", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel); self.panel._auth = None  # Auth is covered separately in test_auth.py
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
        self.assertIn('channel id="tp-b:672"', xml)
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
        spec.loader.exec_module(self.panel); self.panel._auth = None  # Auth is covered separately in test_auth.py
        self.binary = os.path.join(self.tmp.name, "producer")
        with open(self.binary, "w") as script:
            script.write("#!" + sys.executable + "\n" + FAKE_ENGINE)
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
                import urllib.error
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request.urlopen(self.base + "/api/stream/log?channel=tp-a:672")
                self.assertEqual(error.exception.code, 410)

    def test_stop_specific_http_session_and_switch(self):
        import json
        tp = self.panel.STATE["transponders"][0]
        tp["scan"]["services"].append({"sid": 643})
        result = self.panel.stream_start(tp, None, [], 0, 672)
        self.assertTrue(result["ok"])
        request = self.request.Request(self.base + "/api/stream/stop",
            data=b'{"channel_id":"tp-a:672"}', headers={"Content-Type":"application/json"})
        with self.request.urlopen(request, timeout=2) as response:
            self.assertTrue(json.load(response)["ok"])
        with self.request.urlopen(self.base + "/stream/channel/tp-a:643", timeout=2) as stream:
            self.assertEqual(stream.read(188)[0], 0x47)
            self.assertEqual(self.panel.stream_status()["channel_id"], "tp-a:643")

    def test_two_http_channels_share_mux_and_ca(self):
        tp = self.panel.STATE["transponders"][0]
        tp["scan"]["services"].append({"sid": 643})
        with self.request.urlopen(self.base + "/stream/channel/tp-a:672", timeout=2) as a:
            self.assertEqual(a.read(188)[0], 0x47)
            with self.request.urlopen(self.base + "/stream/channel/tp-a:643", timeout=2) as b:
                self.assertEqual(b.read(188)[0], 0x47)
                sessions = self.panel.stream_status()["sessions"]
                self.assertEqual(len(sessions), 2)
                self.assertEqual(len({s["mux_pid"] for s in sessions}), 1)
                self.assertEqual(len({s["ca_pid"] for s in sessions}), 1)
                self.assertTrue(all(s["ca_pid"] for s in sessions))
                self.panel._streams.stop_channel("tp-a:672")
                self.assertEqual(b.read(188)[0], 0x47)

    def test_http_reconnect_preserves_service_mux_and_broker_pids(self):
        url = self.base + "/stream/channel/tp-a:672"
        with self.request.urlopen(url, timeout=2) as stream:
            self.assertEqual(stream.read(188)[0], 0x47)
            initial = self.panel.stream_status()["sessions"][0]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.panel.stream_status()["sessions"]
            if status and status[0]["reconnect_waiting"]: break
            time.sleep(.01)
        self.assertTrue(status[0]["reconnect_waiting"])
        with self.request.urlopen(url, timeout=2) as stream:
            self.assertEqual(stream.read(188)[0], 0x47)
            renewed = self.panel.stream_status()["sessions"][0]
            for key in ("pid", "mux_pid", "ca_pid"):
                self.assertEqual(initial[key], renewed[key])
            self.assertEqual(len(self.panel._streams.brokers), 1)
            self.assertFalse(renewed["reconnect_waiting"])

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
