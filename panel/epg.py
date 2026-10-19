"""Bounded DVB EIT schedule collection and private, rolling 1–14 day storage."""

import json
import os
import threading
import time
import unicodedata


def _crc_entry(byte):
    value = byte << 24
    for _ in range(8):
        value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
    return value


_CRC_TABLE = tuple(_crc_entry(byte) for byte in range(256))


def crc32(data):
    value = 0xFFFFFFFF
    for byte in data:
        value = ((value << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[(value >> 24) ^ byte]
    return value


def clean_text(value):
    return "".join(
        c
        for c in str(value)
        if c in "\t\n\r"
        or 0x20 <= ord(c) <= 0xD7FF
        or 0xE000 <= ord(c) <= 0xFFFD
        or 0x10000 <= ord(c) <= 0x10FFFF
    )


def text(data):
    if not data:
        return ""
    if data[0] in (0x11, 0x12, 0x13, 0x14):
        return data[1:].decode(
            {0x11: "utf-16-be", 0x12: "euc_kr", 0x13: "gb2312", 0x14: "big5"}[data[0]],
            "replace",
        )
    if data[0] == 0x15:
        return data[1:].decode("utf-8", "replace")
    if data[:2] == b"\x10\x00" and len(data) >= 3:
        try:
            return data[3:].decode("iso8859_%d" % data[2], "replace")
        except LookupError:
            return data[3:].decode("latin1", "replace")
    if 1 <= data[0] <= 11:
        try:
            return data[1:].decode("iso8859_%d" % (data[0] + 4), "replace")
        except LookupError:
            return data[1:].decode("latin1", "replace")
    accents = {
        0xC1: "\u0300",
        0xC2: "\u0301",
        0xC3: "\u0302",
        0xC4: "\u0303",
        0xC5: "\u0304",
        0xC6: "\u0306",
        0xC7: "\u0307",
        0xC8: "\u0308",
        0xCA: "\u030a",
        0xCB: "\u0327",
        0xCD: "\u030b",
        0xCE: "\u0328",
        0xCF: "\u030c",
    }
    out = []
    pos = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        if byte in accents and pos < len(data):
            out.append(unicodedata.normalize("NFC", chr(data[pos]) + accents[byte]))
            pos += 1
        elif byte >= 32 and byte not in range(0x80, 0xA0):
            out.append(chr(byte))
        elif byte == 0x8A:
            out.append("\n")
    return "".join(out)


def bcd(byte):
    if byte >> 4 > 9 or byte & 15 > 9:
        raise ValueError("invalid BCD")
    return (byte >> 4) * 10 + (byte & 15)


def parse_eit(section, tsid):
    if (
        len(section) < 18
        or len(section) != 3 + ((section[1] & 15) << 8) + section[2]
        or section[0] not in (0x4E, *range(0x50, 0x60))
        or not section[1] & 0x80
        or not section[5] & 1
        or crc32(section)
        or int.from_bytes(section[8:10], "big") != tsid
    ):
        return []
    sid = int.from_bytes(section[3:5], "big")
    events = []
    pos = 14
    end = len(section) - 4
    while pos + 12 <= end:
        size = ((section[pos + 10] & 15) << 8) | section[pos + 11]
        if pos + 12 + size > end:
            return []
        raw = section[pos : pos + 12]
        descriptors = section[pos + 12 : pos + 12 + size]
        pos += 12 + size
        try:
            h, m, s = map(bcd, raw[4:7])
            dh, dm, ds = map(bcd, raw[7:10])
            if (
                raw[2:7] == b"\xff" * 5
                or h > 23
                or m > 59
                or s > 59
                or dm > 59
                or ds > 59
            ):
                continue
            start = (
                (int.from_bytes(raw[2:4], "big") - 40587) * 86400
                + h * 3600
                + m * 60
                + s
            )
            duration = dh * 3600 + dm * 60 + ds
            if duration <= 0:
                continue
        except ValueError:
            continue
        title = description = language = ""
        extended = {}
        off = 0
        while off + 2 <= len(descriptors):
            tag, length = descriptors[off : off + 2]
            body = descriptors[off + 2 : off + 2 + length]
            off += 2 + length
            if len(body) != length:
                return []
            if tag == 0x4D and length >= 5:
                lang = body[:3].decode("ascii", "replace")
                n = body[3]
                if 5 + n > length or 5 + n + body[4 + n] > length:
                    continue
                if not language:
                    language = lang
                    title = text(body[4 : 4 + n])
                    description = text(body[5 + n : 5 + n + body[4 + n]])
            elif tag == 0x4E and length >= 6:
                lang = body[1:4].decode("ascii", "replace")
                n = body[4]
                at = 5 + n
                if at < length and at + 1 + body[at] <= length:
                    extended.setdefault(lang, {})[body[0] >> 4] = text(
                        body[at + 1 : at + 1 + body[at]]
                    )
        if extended:
            fragments = (
                extended.get(language, {})
                if language
                else extended[next(iter(extended))]
            )
            description = (
                description + " " + "".join(fragments[k] for k in sorted(fragments))
            ).strip()
        events.append(
            {
                "sid": sid,
                "event_id": int.from_bytes(raw[:2], "big"),
                "start": start,
                "stop": start + duration,
                "title": title[:256],
                "description": description[:2048],
                "language": language,
            }
        )
    return events


class EITCollector:
    def __init__(self, callback):
        self.callback = callback
        self.tsid = None
        self.buffers = {}
        self.counters = {}
        self.eit_packets = 0
        self.sections = 0
        self.bad_crc = 0
        self.tables = {}
        self.parsed_events = 0
        self.last_eit_packet = None
        self.last_event_start = None
        self.last_event_stop = None

    def status(self):
        # Reception diagnostics only: RAM counters, never saved to EPG/log files.
        return {
            "tsid": self.tsid,
            "eit_packets": self.eit_packets,
            "sections": self.sections,
            "bad_crc": self.bad_crc,
            "tables": dict(self.tables),
            "parsed_events": self.parsed_events,
            "last_eit_packet": self.last_eit_packet,
            "last_event_start": self.last_event_start,
            "last_event_stop": self.last_event_stop,
        }

    def feed(self, data):
        for off in range(0, len(data) - 187, 188):
            packet = data[off : off + 188]
            if packet[0] != 0x47:
                continue
            pid = ((packet[1] & 31) << 8) | packet[2]
            if pid not in (0, 18):
                continue
            if pid == 18:
                self.eit_packets += 1
                self.last_eit_packet = int(time.time())
            afc = (packet[3] >> 4) & 3
            cc = packet[3] & 15
            if packet[1] & 0x80 or packet[3] & 0xC0 or afc == 0:
                self.buffers.pop(pid, None)
                self.counters.pop(pid, None)
                continue
            at = 4
            if afc & 2:
                size = packet[4]
                if size > 183:
                    self.buffers.pop(pid, None)
                    continue
                if size and packet[5] & 0x80:
                    self.buffers.pop(pid, None)
                    self.counters.pop(pid, None)
                at += size + 1
            if not afc & 1 or at >= 188:
                continue
            previous = self.counters.get(pid)
            self.counters[pid] = cc
            if previous == cc:
                continue
            if previous is not None and cc != (previous + 1) % 16:
                self.buffers.pop(pid, None)
            payload = packet[at:]
            pusi = packet[1] & 0x40
            if pusi:
                pointer = payload[0]
                if pointer > len(payload) - 1:
                    self.buffers.pop(pid, None)
                    continue
                if pid in self.buffers:
                    self._append(pid, payload[1 : 1 + pointer])
                self.buffers[pid] = bytearray()
                self._append(pid, payload[1 + pointer :])
            elif pid in self.buffers:
                self._append(pid, payload)

    def _append(self, pid, payload):
        buf = self.buffers[pid]
        buf.extend(payload)
        while len(buf) >= 3:
            if buf[0] == 0xFF:
                buf.clear()
                return
            size = 3 + ((buf[1] & 15) << 8) + buf[2]
            if size < 8 or size > 4096:
                buf.clear()
                return
            if len(buf) < size:
                return
            section = bytes(buf[:size])
            del buf[:size]
            if pid == 18:
                self.sections += 1
                key = "0x%02X" % section[0]
                self.tables[key] = self.tables.get(key, 0) + 1
                if crc32(section):
                    self.bad_crc += 1
            if (
                pid == 0
                and section[0] == 0
                and section[1] & 0x80
                and section[5] & 1
                and crc32(section) == 0
            ):
                self.tsid = int.from_bytes(section[3:5], "big")
            elif pid == 18 and self.tsid is not None:
                events = parse_eit(section, self.tsid)
                if events:
                    self.parsed_events += len(events)
                    self.last_event_start = events[-1]["start"]
                    self.last_event_stop = events[-1]["stop"]
                    self.callback(events)


class EPGStore:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self.days = 1
        self.channels = {}
        self.count = 0
        self.lock = threading.RLock()
        self.writer = threading.Lock()
        self.dirty = False
        self.generation = 0
        self.done = threading.Event()
        self.thread = None
        try:
            with open(path, encoding="utf-8") as source:
                saved = json.load(source)
            self.days = self.validate_days(saved.get("days", 1))
            for channel, events in saved.get("channels", {}).items():
                for event in events.values():
                    self.put(channel, event)
            self.dirty = False
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def validate_days(days):
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
            raise ValueError("EPG: selecciona entre 1 y 14 días")
        return days

    def put(self, channel, event):
        try:
            start, stop, event_id = (event[k] for k in ("start", "stop", "event_id"))
            if any(
                isinstance(v, bool) or not isinstance(v, int)
                for v in (start, stop, event_id)
            ):
                return
            now = self.clock()
            if (
                stop <= now
                or start >= now + self.days * 86400
                or stop <= start
                or stop - start > 360000
                or not 0 <= event_id <= 65535
            ):
                return
            value = {
                "event_id": event_id,
                "start": start,
                "stop": stop,
                "title": clean_text(event.get("title", ""))[:256],
                "description": clean_text(event.get("description", ""))[:2048],
                "language": str(event.get("language", ""))[:3],
            }
            with self.lock:
                if stop <= self.clock() or start >= self.clock() + self.days * 86400:
                    return
                events = self.channels.get(channel, {})
                key = str(event_id)
                if events.get(key) == value or (
                    key not in events and self.count >= 50000
                ):
                    return
                if key not in events:
                    self.count += 1
                events[key] = value
                self.channels[channel] = events
                self.dirty = True
                self.generation += 1
        except (KeyError, TypeError, ValueError):
            return

    def events_for(self, channel):
        now = self.clock()
        with self.lock:
            return sorted(
                (
                    dict(e)
                    for e in self.channels.get(channel, {}).values()
                    if e["stop"] > now and e["start"] < now + self.days * 86400
                ),
                key=lambda e: e["start"],
            )

    def _prune(self):
        now = self.clock()
        for channel, events in list(self.channels.items()):
            for key, event in list(events.items()):
                if event["stop"] <= now or event["start"] >= now + self.days * 86400:
                    del events[key]
                    self.count -= 1
                    self.dirty = True
                    self.generation += 1
            if not events:
                del self.channels[channel]

    def set_days(self, days):
        days = self.validate_days(days)
        with self.lock:
            self.days = days
            self._prune()
            self.dirty = True
            self.generation += 1
        self.flush()

    def flush(self):
        with self.writer:
            with self.lock:
                self._prune()
                if not self.dirty:
                    return
                generation = self.generation
                data = {
                    "days": self.days,
                    "channels": {
                        c: dict(events) for c, events in self.channels.items()
                    },
                }
            temporary = self.path + ".tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            with self.lock:
                if self.generation == generation:
                    self.dirty = False

    def start(self):
        if self.thread:
            return

        def save_loop():
            while not self.done.wait(30):
                try:
                    self.flush()
                except OSError:
                    pass  # never stop DVB reception for a storage error

        self.thread = threading.Thread(target=save_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.flush()
