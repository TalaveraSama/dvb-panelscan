"""Two real C service workers fed by one synthetic multiplex through the panel.
Only DVB capture is substituted. Broker, demux, PAT/PMT rewrite and HTTP are real.
"""
import base64
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))
BINARY = ROOT / "tbsstream" / "tbsstream"


def crc(data):
    value = 0xffffffff
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value.to_bytes(4, "big")


def section(table, body):
    length = len(body) + 4
    data = bytes([table, 0xb0 | (length >> 8), length & 255]) + body
    return data + crc(data)


def packet(pid, payload, psi=False):
    return (bytes([0x47, (pid >> 8) | (0x40 if psi else 0), pid & 255, 0x10]) +
            (b"\0" if psi else b"") + payload).ljust(188, b"\xff")


def multiplex():
    pat = section(0, bytes.fromhex("0001 c1 0000 02a0 e100 0283 e110"))
    pmt_a = section(2, bytes.fromhex("02a0 c1 0000 e101 f000 1b e101 f000"))
    pmt_b = section(2, bytes.fromhex("0283 c1 0000 e111 f000 1b e111 f000"))
    from test_epg import eit, packets
    import time
    guide = b''.join(packets(18, eit(int(time.time())+3600,tsid=1)))
    return guide + (packet(0, pat, True) + packet(0x100, pmt_a, True) + packet(0x110, pmt_b, True) +
            packet(0x101, b"service-A" * 20)[:188] + packet(0x111, b"service-B" * 20)[:188])


@unittest.skipUnless(BINARY.is_file(), "build tbsstream before C pipeline integration")
class RealMuxPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("DVB_PANEL_DATA_DIR")
        os.environ["DVB_PANEL_DATA_DIR"] = self.tmp.name
        spec = importlib.util.spec_from_file_location("real_mux_panel", ROOT / "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)
        wrapper = Path(self.tmp.name) / "capture-substitute"
        wrapper.write_text("#!" + sys.executable + "\n" + '''import os,sys,time,base64
if "--mux-only" not in sys.argv:
 os.execv(BINARY, [BINARY]+sys.argv[1:])
data=bytearray(base64.b64decode(DATA))
cc=0
while True:
 for offset in range(0,len(data),188): data[offset+3]=0x10 | cc
 sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
 cc=(cc+1)&15; time.sleep(.002)
'''.replace("BINARY", repr(str(BINARY))).replace("DATA", repr(base64.b64encode(multiplex()).decode())))
        wrapper.chmod(0o700)
        self.panel._cfg["tbsstream"] = str(wrapper)
        self.panel.STATE["transponders"] = [{"id": "mux-a", "adapter": 0, "type": "dvbs2",
            "freq_mhz": 10970, "symbol_rate": 27900000,
            "scan": {"services": [{"sid": 672}, {"sid": 643}]}}]
        self.server = self.panel.ThreadingHTTPServer(("127.0.0.1", 0), self.panel.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%s" % self.server.server_port

    def tearDown(self):
        self.panel.stream_stop()
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        if self.previous is None: os.environ.pop("DVB_PANEL_DATA_DIR", None)
        else: os.environ["DVB_PANEL_DATA_DIR"] = self.previous
        self.tmp.cleanup()

    def test_actual_c_workers_separate_services_over_http(self):
        with urllib.request.urlopen(self.base + "/stream/channel/mux-a:672", timeout=4) as a:
            with urllib.request.urlopen(self.base + "/stream/channel/mux-a:643", timeout=4) as b:
                for session in self.panel._streams.sessions.values():
                    self.assertNotIn("--pass", session.process.args)
                    self.assertNotIn("--newcamd", session.source.process.args)
                    self.assertEqual(os.stat(session.broker.directory).st_mode & 0o777, 0o700)
                    self.assertEqual(os.stat(session.broker.path).st_mode & 0o777, 0o600)
                status = self.panel.stream_status()["sessions"]
                import time
                deadline = time.monotonic() + 1
                while not all(s["ecm"]["state"] == "clear" for s in status) and time.monotonic() < deadline:
                    time.sleep(.01)
                    status = self.panel.stream_status()["sessions"]
                self.assertTrue(all(s["ecm"]["state"] == "clear" for s in status))
                self.assertEqual(len({s["mux_pid"] for s in status}), 1)
                self.assertEqual(len({s["ca_pid"] for s in status}), 1)
                deadline = time.monotonic() + 1
                while not self.panel._epg.events_for("mux-a:672") and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(self.panel._epg.events_for("mux-a:672")[0]["title"], "Película")
                for response, expected, foreign in ((a, 0x101, 0x111), (b, 0x111, 0x101)):
                    data = response.read(188 * 200)
                    self.assertEqual(len(data), 188 * 200)
                    pids = set()
                    for i in range(0, len(data), 188):
                        self.assertEqual(data[i], 0x47)
                        pids.add(((data[i+1] & 31) << 8) | data[i+2])
                    self.assertIn(expected, pids)
                    self.assertNotIn(foreign, pids)
                    self.assertIn(0, pids)  # rewritten PAT
                    self.assertIn(expected - 1, pids)  # this service's PMT
                    self.assertNotIn(foreign - 1, pids)
                    pat_packet = next(data[i:i+188] for i in range(0,len(data),188)
                                      if ((data[i+1] & 31) << 8) | data[i+2] == 0)
                    pat = pat_packet[5:21]
                    self.assertEqual(crc(pat), b"\0" * 4)
                    self.assertEqual(int.from_bytes(pat[8:10], "big"), 672 if expected == 0x101 else 643)
                self.panel._streams.stop_channel("mux-a:672")
                self.assertEqual(b.read(188)[0], 0x47)
                remaining = self.panel.stream_status()["sessions"]
                self.assertEqual([s["channel_id"] for s in remaining], ["mux-a:643"])

    def test_two_adapters_share_broker_not_capture(self):
        import copy
        other = copy.deepcopy(self.panel.STATE["transponders"][0])
        other.update(id="mux-b", adapter=1, freq_mhz=11010)
        self.panel.STATE["transponders"].append(other)
        with urllib.request.urlopen(self.base + "/stream/channel/mux-a:672", timeout=4) as a:
            with urllib.request.urlopen(self.base + "/stream/channel/mux-b:643", timeout=4) as b:
                self.assertEqual(a.read(188)[0], 0x47); self.assertEqual(b.read(188)[0], 0x47)
                status = self.panel.stream_status()["sessions"]
                self.assertEqual(len({s["mux_pid"] for s in status}), 2)
                self.assertEqual(len({s["ca_pid"] for s in status}), 1)
