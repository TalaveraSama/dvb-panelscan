import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from events import EventBus, diagnostic_event
from streaming import StreamManager


class EventTests(unittest.TestCase):
    def test_no_history_without_viewers_and_no_replay(self):
        bus = EventBus()
        bus.publish("start", "Old event")
        first = bus.subscribe()
        self.assertEqual(first.read(timeout=0), [])
        bus.publish("start", "Live event")
        self.assertEqual(first.read(timeout=0)[0]["message"], "Live event")
        first.close()
        self.assertFalse(first.queue)
        bus.publish("stop", "Not observed")
        second = bus.subscribe()
        self.assertEqual(second.read(timeout=0), [])
        second.close()
        self.assertFalse(bus.subscribers)

    def test_bounded_per_viewer_and_slow_viewer_does_not_block(self):
        bus = EventBus(); slow = bus.subscribe(); fast = bus.subscribe()
        for number in range(1200):
            bus.publish("ecm", str(number))
            self.assertEqual(len(fast.read(timeout=0)), 1)
        self.assertEqual(len(slow.queue), 500)
        self.assertEqual(slow.dropped, 700)
        self.assertEqual(slow.read(timeout=0)[0]["kind"], "overflow")
        slow.close(); fast.close()

    def test_whitelist_never_forwards_passwords_keys_or_raw_lines(self):
        bus = EventBus(); viewer = bus.subscribe()
        diagnostic_event(bus, "password=secret DES=hidden CW=private", "ca")
        self.assertEqual(viewer.read(timeout=0), [])
        diagnostic_event(bus, "CA broker: connection failed: password=secret", "ca")
        diagnostic_event(bus, "ECM: sid=672 caid=1802 time_ms=184 result=0 cw_mask=3 at_ms=1000 pid=1", "channel", "tp:672", 0)
        events = viewer.read(timeout=0)
        text = json.dumps(events)
        self.assertNotIn("secret", text); self.assertNotIn("private", text)
        self.assertEqual(events[-1]["channel"], "tp:672")
        self.assertEqual(events[-1]["latency_ms"], 184)
        viewer.close()

    def test_ca_selection_and_key_wait_are_safe_live_events(self):
        bus = EventBus(); viewer = bus.subscribe()
        diagnostic_event(bus, "CA select: sid=1874 caid=1802 ecm=100 candidate=2/3", "channel", "tp:1874", 8)
        diagnostic_event(bus, "CW wait: parity=0 stale=1 pending=20", "channel", "tp:1874", 8)
        records = viewer.read(timeout=0)
        self.assertEqual([r["kind"] for r in records], ["ca_select", "cw_wait"])
        self.assertEqual(records[0]["caid"], "1802")
        self.assertEqual(records[1]["pending"], 20)
        diagnostic_event(bus, "CA select: sid=1874 caid=1802 ecm=100 candidate=2/3 password=secret", "channel")
        self.assertEqual(viewer.read(timeout=0), [])
        viewer.close()

    def test_transport_sync_counters_are_visible_without_raw_logs(self):
        bus = EventBus(); viewer = bus.subscribe()
        diagnostic_event(bus, "TS stats: tei=0 cc_errors=1 pending=5 sync_losses=2 sync_bytes=30 invalid_ts=4 psi_discontinuities=3", "channel", "tp:1", 0)
        event = viewer.read(timeout=0)[0]
        self.assertEqual(event["stats"]["sync_losses"], 2)
        self.assertEqual(event["stats"]["psi_discontinuities"], 3)
        self.assertEqual(event["level"], "warning")
        viewer.close()

    def test_session_drains_stderr_and_health_without_creating_log(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = StreamManager(); viewer = manager.events.subscribe()
            script = '''import os,time
pid=os.getpid()
for n in range(1000):
 print(f"ECM: sid=672 caid=1802 time_ms=184 result=0 cw_mask=3 at_ms={int(time.monotonic()*1000)} pid={pid}",file=__import__('sys').stderr,flush=True)
p=bytes([0x47,0,0,0x10])+bytes(184)
while True:
 os.write(1,p*7); time.sleep(.01)
'''
            try:
                session = manager.acquire("tp:672",0,[sys.executable,"-u","-c",script],
                                          os.path.join(folder,"should-not-exist.log"),timeout=4)
                deadline = time.monotonic()+2
                while session.status()["ecm"]["state"] != "ok" and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertEqual(session.status()["ecm"]["state"],"ok")
                self.assertEqual(os.listdir(folder),[])
                manager.release(session)
                records=[]
                while viewer.queue: records.extend(viewer.read(timeout=0))
                self.assertTrue(any(e["kind"]=="ecm" for e in records))
                self.assertTrue(any(e["kind"]=="stop" for e in records))
            finally:
                manager.stop_all(); viewer.close()


class EventHTTPTests(unittest.TestCase):
    def test_sse_has_no_cache_no_replay_and_disconnect_cleans_subscriber(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = os.environ.get("DVB_PANEL_DATA_DIR")
            os.environ["DVB_PANEL_DATA_DIR"] = folder
            spec = importlib.util.spec_from_file_location("event_panel", "panel/server.py")
            panel = importlib.util.module_from_spec(spec); spec.loader.exec_module(panel); panel._auth = None  # Auth is covered separately in test_auth.py
            server = panel.ThreadingHTTPServer(("127.0.0.1",0),panel.Handler)
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                panel._streams.events.publish("start","Should not replay")
                with urllib.request.urlopen("http://127.0.0.1:%s/api/events" % server.server_port,timeout=4) as response:
                    self.assertEqual(response.headers["Cache-Control"],"no-store")
                    self.assertTrue(response.headers["Content-Type"].startswith("text/event-stream"))
                    self.assertEqual(response.readline(),b"event: ready\n")
                    self.assertEqual(response.readline(),b"data: {}\n"); response.readline()
                    panel._streams.events.publish("start","New channel",channel="tp:672")
                    line=response.readline()
                    self.assertTrue(line.startswith(b"data: "))
                    self.assertEqual(json.loads(line[6:])["message"],"New channel")
                deadline=time.monotonic()+5
                while panel._streams.events.subscribers and time.monotonic()<deadline: time.sleep(.05)
                self.assertFalse(panel._streams.events.subscribers)
                self.assertEqual(os.listdir(folder),[])
            finally:
                server.shutdown();server.server_close();thread.join()
                if previous is None: os.environ.pop("DVB_PANEL_DATA_DIR",None)
                else: os.environ["DVB_PANEL_DATA_DIR"]=previous
