"""Live-only structured events: no files, replay, or backlog without viewers."""
import collections
import re
import threading
import time
from health import ECM, PMT


class Subscription:
    def __init__(self, bus):
        self.bus = bus
        self.queue = collections.deque(maxlen=500)
        self.dropped = 0
        self.closed = False

    def read(self, timeout=2):
        with self.bus.condition:
            self.bus.condition.wait_for(lambda: self.queue or self.closed, timeout)
            batch = []
            if self.dropped:
                batch.append({"time": time.time(), "level": "warning", "kind": "overflow",
                              "message": "Visor lento: %d eventos omitidos" % self.dropped})
                self.dropped = 0
            while self.queue and len(batch) < 100:
                batch.append(self.queue.popleft())
            return batch

    def close(self):
        with self.bus.condition:
            self.closed = True
            self.queue.clear()
            self.bus.subscribers.discard(self)
            self.bus.condition.notify_all()


class EventBus:
    def __init__(self):
        self.condition = threading.Condition()
        self.subscribers = set()

    def subscribe(self):
        with self.condition:
            if len(self.subscribers) >= 16:
                raise ValueError("Máximo de 16 visores de eventos simultáneos")
            subscription = Subscription(self)
            self.subscribers.add(subscription)
            return subscription

    def publish(self, kind, message, level="info", channel=None, adapter=None, **metrics):
        with self.condition:
            if not self.subscribers:
                return
            event = {"time": time.time(), "kind": kind, "message": message,
                     "level": level, "channel": channel, "adapter": adapter, **metrics}
            for subscription in self.subscribers:
                if len(subscription.queue) == subscription.queue.maxlen:
                    subscription.dropped += 1
                subscription.queue.append(event)
            self.condition.notify_all()


def diagnostic_event(bus, line, scope, channel=None, adapter=None):
    """Whitelist structured events; never forward arbitrary stderr/secrets."""
    if bus is None:
        return
    match = ECM.search(line)
    if match:
        latency, result, mask, _ = map(int, match.groups())
        success = result == 0 and mask != 0
        bus.publish("ecm", "ECM/CW recibida" if success else "ECM sin CW válida",
                    "info" if success else "error", channel, adapter,
                    latency_ms=latency, result=result, cw_available=bool(mask))
        return
    if line.startswith("TS stats:"):
        stats = {key: int(value) for key, value in re.findall(
            r"\b(tei|cc_errors|service_drops|pending|ecm_drops)=(\d+)", line)}
        if stats:
            level = "warning" if any(value for key, value in stats.items() if key != "pending") else "info"
            bus.publish("stats", "Estadísticas TS (acumuladas)", level, channel, adapter, stats=stats)
        return
    match = PMT.search(line)
    if match:
        bus.publish("service", "Servicio cifrado detectado" if int(match[1]) > 0 else "Servicio libre detectado",
                    channel=channel, adapter=adapter)
        return
    # Labels are ours, never the raw line (which may include user data).
    labels = (
        ("open /dev/dvb", "No se pudo abrir el dispositivo DVB", "error"),
        ("login rechazado", "Login Newcamd rechazado", "error"),
        ("datos de tarjeta", "No se recibieron datos de tarjeta", "error"),
        ("unrecognized option", "Motor incompatible: opción no reconocida", "error"),
        ("queue full", "Cola de servicio llena; paquetes descartados", "error"),
        ("ECM deadline", "Tiempo de espera ECM agotado en el broker", "error"),
        ("connected", "Conexión de acceso condicional establecida", "info"),
        ("reconnecting", "Reconectando acceso condicional", "warning"),
        ("connection failed", "Falló la conexión de acceso condicional", "error"),
        ("sin lock", "Adaptador sin señal/lock", "error"),
        ("lock OK", "Adaptador sincronizado", "info"),
        ("allocation failed", "No se pudo reservar memoria", "error"),
    )
    for needle, label, level in labels:
        if needle in line:
            bus.publish(scope, label, level, channel, adapter)
            return


class DiagnosticReader:
    """Always drain stderr for process health; retain no diagnostic transcript."""
    def __init__(self, process, bus, scope, channel=None, adapter=None, health=None, lock=None):
        self.process, self.bus, self.scope = process, bus, scope
        self.channel, self.adapter, self.health = channel, adapter, health
        self.lock = lock or threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        import os
        pending = b""
        discarding = False
        try:
            while True:
                block = os.read(self.process.stderr.fileno(), 8192)
                if not block:
                    break
                parts = (pending + block).split(b"\n")
                pending = parts.pop()
                for part in parts:
                    if discarding:
                        discarding = False
                        continue
                    if len(part) > 4096:
                        continue
                    line = part.decode("utf-8", errors="replace")
                    if self.health:
                        with self.lock:
                            self.health.ingest(line)
                    diagnostic_event(self.bus, line, self.scope, self.channel, self.adapter)
                if len(pending) > 4096:
                    pending = b""
                    discarding = True
        except (OSError, ValueError):
            pass
        finally:
            self.process.stderr.close()

    def join(self):
        self.thread.join(timeout=2)
