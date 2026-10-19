import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from epg import EITCollector, EPGStore, crc32, parse_eit, text


def section(tid, body):
    length = len(body) + 4
    data = bytes([tid, 0xB0 | (length >> 8), length & 255]) + body
    return data + crc32(data).to_bytes(4, "big")


def bcd(n):
    return (n // 10) * 16 + n % 10


def eit(start, sid=672, tsid=42, event_id=1, tid=0x50, title="Película", large=False):
    tm = time.gmtime(start)
    name = b"\x15" + title.encode()
    short = b"spa" + bytes([len(name)]) + name + b"\x04Test"
    descriptors = bytes([0x4D, len(short)]) + short
    if large:
        for fragment in range(8):
            body = bytes([(fragment << 4) | 7]) + b"spa\x00\xc8" + b"a" * 200
            descriptors += bytes([0x4E, len(body)]) + body
    event = (
        event_id.to_bytes(2, "big")
        + (start // 86400 + 40587).to_bytes(2, "big")
        + bytes(map(bcd, (tm.tm_hour, tm.tm_min, tm.tm_sec)))
        + bytes([1, 0x30, 0])
        + bytes([0xF0 | (len(descriptors) >> 8), len(descriptors) & 255])
        + descriptors
    )
    body = (
        sid.to_bytes(2, "big")
        + b"\xc1\x00\x00"
        + tsid.to_bytes(2, "big")
        + b"\x00\x01\x00"
        + bytes([tid])
        + event
    )
    return section(tid, body)


def packets(pid, data, cc=0):
    result = []
    first = True
    while data:
        take = 183 if first else 184
        payload = (b"\x00" if first else b"") + data[:take]
        data = data[take:]
        result.append(
            (
                bytes([0x47, (pid >> 8) | (0x40 if first else 0), pid & 255, 0x10 | cc])
                + payload
            ).ljust(188, b"\xff")
        )
        first = False
        cc = (cc + 1) % 16
    return result


class EITTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())

    def test_real_times_utf8_description_and_crc(self):
        self.assertEqual(crc32(b"123456789"), 0x0376E6E7)
        events = parse_eit(eit(self.now + 3600), 42)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"], self.now + 3600)
        self.assertEqual(events[0]["stop"], self.now + 9000)
        self.assertEqual(events[0]["title"], "Película")
        self.assertEqual(events[0]["description"], "Test")
        self.assertEqual(text(bytes([0xC2]) + b"e"), "é")

    def test_other_mux_other_table_crc_and_bcd_rejected(self):
        self.assertEqual(parse_eit(eit(self.now, tid=0x60), 42), [])
        self.assertEqual(parse_eit(eit(self.now), 43), [])
        bad = bytearray(eit(self.now))
        bad[-1] ^= 1
        self.assertEqual(parse_eit(bad, 42), [])
        bad = bytearray(eit(self.now))
        bad[18] = 0x99
        bad[-4:] = crc32(bad[:-4]).to_bytes(4, "big")
        self.assertEqual(parse_eit(bad, 42), [])

    def test_large_schedule_sections_across_packets_and_pat_binding(self):
        output = []
        collector = EITCollector(output.extend)
        pat = section(0, b"\x00\x2a\xc1\x00\x00\x02\xa0\xe1\x00")
        data = eit(self.now + 3600, large=True)
        self.assertGreater(len(data), 1024)
        collector.feed(b"".join(packets(18, data)))
        self.assertEqual(output, [])
        collector.feed(b"".join(packets(0, pat)))
        collector.feed(b"".join(packets(18, data, cc=10)))
        self.assertEqual(len(output), 1)
        self.assertIn("a" * 100, output[0]["description"])
        self.assertEqual(output[0]["sid"], 672)

    def test_tei_and_continuity_gap_drop_partial_section(self):
        output = []
        collector = EITCollector(output.extend)
        collector.feed(b"".join(packets(0, section(0, b"\x00\x2a\xc1\x00\x00"))))
        data = packets(18, eit(self.now + 3600, large=True))
        collector.feed(data[0])
        collector.feed(b"".join(data[2:]))
        self.assertEqual(output, [])
        corrupt = bytearray(data[0])
        corrupt[1] |= 0x80
        collector.feed(bytes(corrupt))
        collector.feed(b"".join(data[1:]))
        self.assertEqual(output, [])
        collector.feed(b"".join(packets(18, eit(self.now + 3600), cc=14)))
        self.assertEqual(len(output), 1)

    def test_reception_counters_distinguish_crc_tables_and_real_events(self):
        collector = EITCollector(lambda events: None)
        collector.feed(b"".join(packets(0, section(0, b"\x00\x2a\xc1\x00\x00"))))
        bad = bytearray(eit(self.now + 3600))
        bad[-1] ^= 1
        for cc, data in enumerate(
            (
                bytes(bad),
                eit(self.now + 3600),
                eit(self.now + 3600, tsid=43),
                eit(self.now + 3600, tid=0x60),
            )
        ):
            collector.feed(b"".join(packets(18, data, cc=cc)))
        status = collector.status()
        self.assertEqual(status["tsid"], 42)
        self.assertEqual(status["eit_packets"], 4)
        self.assertEqual(status["sections"], 4)
        self.assertEqual(status["bad_crc"], 1)
        self.assertEqual(status["tables"], {"0x50": 3, "0x60": 1})
        self.assertEqual(status["parsed_events"], 1)
        self.assertEqual(status["last_event_start"], self.now + 3600)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [int(time.time())]
        self.path = os.path.join(self.tmp.name, "epg.json")
        self.store = EPGStore(self.path, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, days=0, event_id=1):
        start = self.now[0] + days * 86400 + 3600
        return {
            "event_id": event_id,
            "start": start,
            "stop": start + 3600,
            "title": "Real",
            "description": "Received",
        }

    def test_one_or_several_days_persist_and_prune(self):
        self.store.put("a:672", self.event(2))
        self.assertEqual(self.store.events_for("a:672"), [])
        self.store.set_days(7)
        self.store.put("a:672", self.event(2))
        self.store.flush()
        reopened = EPGStore(self.path, clock=lambda: self.now[0])
        self.assertEqual(reopened.days, 7)
        self.assertEqual(len(reopened.events_for("a:672")), 1)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        reopened.set_days(1)
        self.assertEqual(reopened.events_for("a:672"), [])

    def test_same_sid_satellites_separate_and_event_update_dedup(self):
        event = self.event()
        self.store.put("a:672", event)
        self.store.put("a:672", {**event, "title": "Updated"})
        self.store.put("b:672", {**event, "title": "Other satellite"})
        self.assertEqual(self.store.count, 2)
        self.assertEqual(self.store.events_for("a:672")[0]["title"], "Updated")
        self.assertEqual(self.store.events_for("b:672")[0]["title"], "Other satellite")
        self.now[0] += 86400
        self.store.flush()
        self.assertEqual(self.store.count, 0)

    def test_invalid_days_and_missing_timestamps_never_generate_events(self):
        for days in (0, 15, True, "7", None):
            with self.assertRaises(ValueError):
                self.store.set_days(days)
        self.store.put("a:672", {"event_id": 1, "title": "Title only"})
        self.assertEqual(self.store.count, 0)

    def test_shutdown_saves_and_text_is_xml_safe(self):
        self.store.start()
        self.store.put("a:672", {**self.event(), "title": "Real\x00 & recibido"})
        self.store.stop()
        reopened = EPGStore(self.path, clock=lambda: self.now[0])
        self.assertEqual(reopened.events_for("a:672")[0]["title"], "Real & recibido")
