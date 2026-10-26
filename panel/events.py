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
    selection = re.fullmatch(r"CA select: sid=(\d+) caid=([0-9A-Fa-f]{4}) ecm=(\d+) candidate=(\d+)/(\d+)", line.strip())
    if selection:
        sid, caid, pid, candidate, total = selection.groups()
        bus.publish("ca_select", "Acceso condicional: CAID %s · ECM PID %s · opción %s/%s" %
                    (caid, pid, candidate, total), "info", channel, adapter,
                    sid=int(sid), caid=caid, ecm_pid=int(pid))
        return
    missing = re.fullmatch(r"CA locked missing: sid=(\d+) caid=([0-9A-Fa-f]{4}) ecm=(\d+)", line.strip())
    if missing:
        sid, caid, pid = missing.groups()
        bus.publish("ca_missing", "CAID/PID fijado no anunciado en la PMT; revisa Editar canal", "error",
                    channel, adapter, sid=int(sid), caid=caid, ecm_pid=int(pid))
        return
    waiting = re.fullmatch(r"CW wait: parity=([01]) stale=([01]) pending=(\d+)", line.strip())
    if waiting:
        parity, stale, pending = map(int, waiting.groups())
        bus.publish("cw_wait", "Esperando clave nueva para cambio de paridad" if stale else "Esperando clave de descifrado",
                    "warning", channel, adapter, parity=parity, pending=pending)
        return
    if line.startswith("TS stats:"):
        stats = {key: int(value) for key, value in re.findall(
            r"\b(tei|cc_errors|service_drops|pending|ecm_drops|sync_losses|sync_bytes|invalid_ts|psi_discontinuities|ca_switch_drops|queue_full_drops|ecm_repeats|cw_timeout_drops|output_pending|output_drops|output_late|pcr_resets|pes_errors|udp_output_drops)=(\d+)", line)}
        if stats:
            level = "warning" if any(value for key, value in stats.items() if key not in ("pending", "ecm_repeats", "output_pending")) else "info"
            bus.publish("stats", "Estadísticas TS (acumuladas)", level, channel, adapter, stats=stats)
        return
    match = PMT.search(line)
    if match:
        count = re.search(r"\bca_count=(\d+)", line)
        encrypted = int(match[1]) > 0 or bool(count and int(count[1]))
        bus.publish("service", "Servicio cifrado detectado" if encrypted else "Servicio libre detectado",
                    channel=channel, adapter=adapter)
        return
    # Labels are ours, never the raw line (which may include user data).
    labels = (
        ("open /dev/dvb", "No se pudo abrir el dispositivo DVB", "error"),
        ("login rechazado", "Login Newcamd rechazado", "error"),
        ("datos de tarjeta", "No se recibieron datos de tarjeta", "error"),
        ("unrecognized option", "Motor incompatible: opción no reconocida", "error"),
        ("output stalled", "Entrega bloqueada: el canal necesita reconexión", "error"),
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
