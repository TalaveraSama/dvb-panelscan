import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from health import ECMHealth


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "log")
        Path(self.path).write_text("")
        self.health = ECMHealth(self.path, created=90)

    def tearDown(self): self.tmp.cleanup()

    def event(self, at, result=0, mask=3):
        self.health.ingest(f"ECM: sid=672 caid=1802 time_ms=180 result={result} cw_mask={mask} at_ms={at*1000}")

    def test_green_requires_two_fresh_successes_and_expires(self):
        self.event(100)
        self.assertEqual(self.health.snapshot(now=101)["state"], "waiting")
        self.event(102)
        self.assertEqual(self.health.snapshot(now=103)["state"], "ok")
        self.assertEqual(self.health.snapshot(now=120)["state"], "error")

    def test_failure_is_red_and_resets_continuity(self):
        self.event(100); self.event(101); self.event(102, -3, 0)
        self.assertEqual(self.health.snapshot(now=103)["state"], "error")
        self.event(104)
        self.assertEqual(self.health.snapshot(now=104)["state"], "waiting")

    def test_zero_cw_and_old_log_are_never_green(self):
        self.event(100, mask=0)
        self.assertEqual(self.health.snapshot(now=100)["state"], "error")
        Path(self.path).write_text("ECM: sid=672 caid=1802 time_ms=180 result=0 cw_mask=3 at_ms=1000\n" * 2)
        self.assertEqual(self.health.snapshot(now=100)["state"], "error")

    def test_clear_and_inactive_are_not_failure(self):
        self.health.ingest("PMT: sid=672 pcr=1 video=1 audio=2 ecm=-1 caid=0000 nes=2")
        self.assertEqual(self.health.snapshot(now=100)["state"], "clear")
        self.assertEqual(self.health.snapshot(running=False, now=100)["state"], "idle")

    def test_no_ecm_after_startup_is_red(self):
        self.health.ingest("PMT: sid=672 pcr=1 video=1 audio=2 ecm=1774 caid=1802 nes=2")
        self.assertEqual(self.health.snapshot(now=100)["state"], "waiting")
        self.assertEqual(self.health.snapshot(now=110)["state"], "error")

    def test_pid_isolation_and_partial_log_lines(self):
        self.health.pid = 123
        with open(self.path, "a") as log:
            log.write("ECM: sid=672 caid=1802 time_ms=180 result=0 cw_mask=3 at_ms=100000 pid=999\n" * 2)
            log.write("ECM: sid=672 caid=1802 time_ms=180 result=0 cw_mask=3 at_ms=101000 pid=")
        self.assertNotEqual(self.health.snapshot(now=102)["state"], "ok")
        with open(self.path, "a") as log:
            log.write("123\nECM: sid=672 caid=1802 time_ms=180 result=0 cw_mask=3 at_ms=102000 pid=123\n")
        self.assertEqual(self.health.snapshot(now=103)["state"], "ok")
