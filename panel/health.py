"""Fresh, per-process ECM telemetry. Never infer decryption from TS bytes."""
import collections
import os
import re
import time

ECM = re.compile(r"ECM: sid=\d+ caid=[0-9A-Fa-f]+ time_ms=(\d+) result=(-?\d+) cw_mask=(\d+) at_ms=(\d+)")
PMT = re.compile(r"PMT: .* ecm=(-?\d+)")


class ECMHealth:
    def __init__(self, path, offset=0, created=None):
        self.path, self.offset = path, offset
        self.created = time.monotonic() if created is None else created
        self.pid = None
        self.partial = b""
        self.encrypted = None
        self.recent = collections.deque(maxlen=256)
        self.last_at = self.last_success = self.result = self.latency = None
        self.mask = 0

    def ingest(self, line):
        pid = re.search(r"\bpid=(\d+)", line)
        if self.pid is not None and (not pid or int(pid[1]) != self.pid):
            return
        pmt = PMT.search(line)
        if pmt:
            count = re.search(r"\bca_count=(\d+)", line)
            self.encrypted = int(pmt[1]) > 0 or bool(count and int(count[1]))
        match = ECM.search(line)
        if not match:
            return
        latency, result, mask, stamp = map(int, match.groups())
        self.encrypted = True
        self.last_at, self.result, self.latency, self.mask = stamp / 1000, result, latency, mask
        if result == 0 and mask:
            self.last_success = self.last_at
            self.recent.append(self.last_at)
        else:
            self.recent.clear()

    def snapshot(self, running=True, now=None):
        now = time.monotonic() if now is None else now
        if self.path is not None:
            try:
                size = os.path.getsize(self.path)
                if size < self.offset or size - self.offset > 131072:
                    self.offset, self.partial = max(0, size - 131072), b""
                with open(self.path, "rb") as log:
                    log.seek(self.offset)
                    data = log.read(131072)
                    self.offset = log.tell()
                lines = (self.partial + data).split(b"\n")
                self.partial = lines.pop()
                for line in lines:
                    self.ingest(line.decode("utf-8", errors="replace"))
            except OSError:
                pass
        while self.recent and now - self.recent[0] > 15:
            self.recent.popleft()
        age = None if self.last_success is None else max(0, now - self.last_success)
        if not running:
            state, label = "idle", "Detenido"
        elif self.encrypted is False:
            state, label = "clear", "Libre · sin ECM"
        elif self.last_at is None:
            state, label = (("error", "Sin ECM/CW reciente") if self.encrypted and now-self.created > 15
                            else ("waiting", "Esperando ECM/PMT"))
        elif self.result != 0 or not self.mask or age is None or age > 15:
            state, label = "error", "ECM/CW no disponible"
        elif len(self.recent) >= 2:
            state, label = "ok", "ECM/CW continuas"
        else:
            state, label = "waiting", "CW recibida · comprobando continuidad"
        return {"state": state, "label": label, "last_result": self.result,
                "latency_ms": self.latency, "cw_mask": self.mask,
                "success_age_seconds": None if age is None else round(age, 1),
                "successes_15s": len(self.recent), "window_seconds": 15}
