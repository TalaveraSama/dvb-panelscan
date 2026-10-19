"""One capture per adapter/mux, isolated service workers and shared CA brokers."""
import collections
import os
import subprocess
import tempfile
import shutil
import threading
import time
from health import ECMHealth
from events import EventBus, DiagnosticReader


class StreamError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class Session:
    def __init__(self, key, adapter, command, log_path, http_port=0, udp=(), max_bytes=8 * 1024 * 1024, source=None, events=None, ts_callback=None):
        self.key, self.adapter = key, adapter
        self.source = source
        self.broker = None
        self.input_error = None
        self.input_worker = None
        source_cursor = source.cursor() if source else None
        self.http_port, self.udp = http_port, tuple(udp)
        self.log_path = log_path
        self.clients, self.pinned = 0, False
        self.condition = threading.Condition()
        self.blocks = collections.deque()
        self.first = self.next = self.buffered = self.bytes_received = 0
        self.overruns = 0
        self.max_bytes = max_bytes
        self.ended = False
        self.stopping = False
        self.created = time.monotonic()
        self.events = events
        self.ts_callback = ts_callback
        self.diagnostic_lock = threading.Lock()
        self.health = ECMHealth(None, created=self.created)
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.PIPE if source else subprocess.DEVNULL,
                                        bufsize=0, start_new_session=True)
        self.health.pid = self.process.pid
        self.diagnostics = DiagnosticReader(self.process, events,
            "mux" if key == "mux" else "channel", None if key == "mux" else key,
            adapter, self.health, self.diagnostic_lock)
        self._event("start", "Captura iniciada" if key == "mux" else "Canal iniciado")
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()
        if source:
            self.input_worker = threading.Thread(target=self._feed, args=(source_cursor,), daemon=True)
            self.input_worker.start()

    def _event(self, kind, message, level="info"):
        if self.events:
            self.events.publish(kind, message, level,
                                None if self.key == "mux" else self.key, self.adapter)

    def _feed(self, cursor):
        try:
            while self.process.poll() is None:
                cursor, block = self.source.read(cursor, timeout=40, cancelled=lambda: self.process.poll() is not None)
                # A blocked pipe belongs only to this service. The mux reader
                # and other workers never wait for this writer.
                view = memoryview(block)
                while view:
                    n = os.write(self.process.stdin.fileno(), view)
                    if n <= 0:
                        raise OSError("closed input")
                    view = view[n:]
        except (OSError, StreamError) as error:
            if not self.stopping:
                self.input_error = str(error)
                self._event("input", "Entrada del canal interrumpida", "warning")
        finally:
            self.process.stdin.close()
            if self.process.poll() is None:
                self.process.terminate()

    def _append(self, data):
        with self.condition:
            self.blocks.append(data)
            self.next += 1
            self.buffered += len(data)
            self.bytes_received += len(data)
            while self.buffered > self.max_bytes and len(self.blocks) > 1:
                self.buffered -= len(self.blocks.popleft())
                self.first += 1
            self.condition.notify_all()
        if self.ts_callback:
            try:
                self.ts_callback(data)
            except Exception:
                self._event("epg", "Se omitió un bloque EPG no válido", "warning")

    def _drain(self):
        pending = b""
        try:
            while True:
                block = os.read(self.process.stdout.fileno(), 188 * 64)
                if not block:
                    break
                pending += block
                size = len(pending) // 188 * 188
                if size:
                    # stdout is exclusively TS. Detect incompatible/old executables.
                    if any(pending[i] != 0x47 for i in range(0, size, 188)):
                        break
                    self._append(pending[:size])
                    pending = pending[size:]
        except OSError:
            pass
        finally:
            self.process.stdout.close()
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.diagnostics.join()
            self._event("stop", ("Captura finalizada" if self.key == "mux" else "Canal finalizado") +
                        " (código %s)" % self.process.returncode,
                        "error" if self.process.returncode not in (0, -15) else "info")
            with self.condition:
                self.ended = True
                self.condition.notify_all()

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.bytes_received and not self.ended:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise StreamError("Sin TS a tiempo; abre Eventos en vivo y reintenta", 504)
                self.condition.wait(left)
            if not self.bytes_received or self.ended:
                raise StreamError("tbsstream terminó antes de iniciar; abre Eventos en vivo y reintenta")

    def cursor(self):
        with self.condition:
            return self.next

    def read(self, cursor, timeout=5, cancelled=None):
        deadline = time.monotonic() + timeout
        with self.condition:
            while cursor >= self.next and not self.ended:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise StreamError("Sin nuevos datos TS", 504)
                if cancelled and cancelled():
                    raise StreamError("Canal detenido")
                self.condition.wait(min(left, .1) if cancelled else left)
            if cursor < self.first:
                self.overruns += 1
                self._event("overflow", "Consumidor lento: cola TS desbordada", "warning")
                raise StreamError("Cliente lento: cola TS desbordada; reconecta", 503)
            if self.ended:
                raise StreamError("El servicio TS terminó")
            return cursor + 1, self.blocks[cursor - self.first]

    def stop(self):
        self.stopping = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.worker.join(timeout=4)
        if self.input_worker:
            # Wake a feeder waiting for a silent source on shutdown.
            self.input_worker.join(timeout=1)

    def status(self):
        with self.diagnostic_lock:
            health = self.health.snapshot(self.process.poll() is None and not self.ended)
        with self.condition:
            return {"channel_id": self.key, "adapter": self.adapter,
                    "pid": self.process.pid, "running": self.process.poll() is None and not self.ended,
                    "clients": self.clients, "pinned": self.pinned,
                    "http_port": self.http_port, "udp": ", ".join(self.udp),
                    "bytes_received": self.bytes_received, "queue_bytes": self.buffered,
                    "slow_clients": self.overruns, "exit_code": self.process.poll(),
                    "log": None,
                    "mux_pid": self.source.process.pid if self.source else self.process.pid,
                    "ca_pid": self.broker.process.pid if self.broker else None,
                    "ca_running": self.broker.process.poll() is None if self.broker else None,
                    "mux_queue_bytes": self.source.buffered if self.source else None,
                    "input_error": self.input_error,
                    "ecm": health}


class Broker:
    """Private local IPC; exactly one upstream Newcamd connection per config."""
    def __init__(self, command, log_path, events=None):
        self.directory = tempfile.mkdtemp(prefix="dvb-ca-")
        self.path = os.path.join(self.directory, "cam.sock")
        try:
            self.process = subprocess.Popen(command + ["--ca-broker", self.path],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, start_new_session=True)
            self.diagnostics = DiagnosticReader(self.process, events, "ca")
            deadline = time.monotonic() + 3
            while not os.path.exists(self.path):
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise StreamError("No se pudo iniciar el broker Newcamd; abre Eventos en vivo y reintenta")
                time.sleep(.01)
        except Exception:
            self.stop()
            raise

    def stop(self):
        process = getattr(self, "process", None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if hasattr(self, "diagnostics"):
            self.diagnostics.join()
        shutil.rmtree(self.directory, ignore_errors=True)


class StreamManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = EventBus()
        self.sessions = {}  # (adapter, channel identity), never just adapter
        self.muxes = {}  # adapter -> (tuning identity, capture)
        self.brokers = {}  # private configuration tuple -> broker
        self.scanning = set()

    def _collect(self):
        for adapter, (_, source) in list(self.muxes.items()):
            if not any(s.source is source for s in self.sessions.values()):
                source.stop()
                del self.muxes[adapter]
        for identity, broker in list(self.brokers.items()):
            if not any(s.broker is broker for s in self.sessions.values()):
                broker.stop()
                del self.brokers[identity]

    def _reap(self):
        for identity, session in list(self.sessions.items()):
            if (session.ended or session.process.poll() is not None or
                    (session.source and session.source.ended)):
                del self.sessions[identity]
                session.stop()
        self._collect()

    def acquire(self, key, adapter, command, log_path, timeout=35, pinned=False,
                http_port=0, udp=(), mux_id=None, mux_command=None, mux_log=None,
                broker_command=None, broker_id=None, broker_log=None, mux_callback=None):
        identity = (adapter, key)
        with self.lock:
            self._reap()
            if adapter in self.scanning:
                raise StreamError("Adaptador ocupado por un escaneo", 409)
            previous = self.sessions.get(identity)
            active = [s for s in self.sessions.values() if s.adapter == adapter]
            if active and (mux_command is None or adapter not in self.muxes or
                           self.muxes[adapter][0] != mux_id) and not previous:
                raise StreamError("Adaptador %s ocupado por %s (%s); detén sus canales antes de cambiar transpondedor" %
                                  (adapter, active[0].key,
                                   "inicio manual fijo" if active[0].pinned else "%s clientes" % active[0].clients), 409)
            if previous and mux_command is not None and self.muxes.get(adapter, (None, None))[0] != mux_id:
                raise StreamError("La sintonía cambió; detén primero los canales del adaptador", 409)
            if previous and broker_command is not None and previous.broker is not self.brokers.get(broker_id):
                raise StreamError("La configuración Newcamd cambió; detén primero este canal", 409)
            if pinned and previous and (previous.http_port != http_port or previous.udp != tuple(udp)):
                raise StreamError("Canal ya activo con otras salidas; usa su URL del panel o detén la sesión", 409)
            if not previous:
                if http_port and any(s.http_port == http_port for s in self.sessions.values()):
                    raise StreamError("Puerto HTTP ocupado por otra sesión; usa 0 (solo panel) u otro puerto", 409)
                try:
                    source, broker = None, None
                    if broker_command is not None:
                        broker = self.brokers.get(broker_id)
                        if broker and broker.process.poll() is not None:
                            # Existing workers reconnect locally; recreate the
                            # same endpoint only by restarting affected channels.
                            raise StreamError("Broker Newcamd finalizado; detén sus canales y reintenta")
                        if not broker:
                            broker = Broker(broker_command, broker_log, self.events)
                            self.brokers[broker_id] = broker
                        command = command + ["--cam-socket", broker.path]
                    if mux_command is not None:
                        if adapter not in self.muxes:
                            source = Session("mux", adapter, mux_command, mux_log,
                                             max_bytes=32 * 1024 * 1024, events=self.events, ts_callback=mux_callback)
                            self.muxes[adapter] = (mux_id, source)
                        source = self.muxes[adapter][1]
                    previous = Session(key, adapter, command, log_path, http_port, udp, source=source, events=self.events)
                    previous.broker = broker
                    self.sessions[identity] = previous
                except Exception as error:
                    self._collect()
                    if isinstance(error, OSError):
                        raise StreamError("No se pudo iniciar el motor") from None
                    raise
            previous.clients += 1
        try:
            previous.wait_ready(timeout)
            if pinned:
                with self.lock:
                    if self.sessions.get(identity) is not previous:
                        raise StreamError("Sesión detenida durante el inicio")
                    previous.pinned = True
            return previous
        except Exception:
            self.release(previous)
            raise

    def release(self, session):
        with self.lock:
            session.clients = max(0, session.clients - 1)
            if not session.clients and not session.pinned:
                identity = (session.adapter, session.key)
                if self.sessions.get(identity) is session:
                    del self.sessions[identity]
                session.stop()
                self._collect()

    def reserve_scan(self, adapter):
        with self.lock:
            self._reap()
            if adapter in self.scanning or any(s.adapter == adapter for s in self.sessions.values()):
                raise StreamError("Adaptador ocupado; detén sus canales antes de escanear", 409)
            self.scanning.add(adapter)

    def release_scan(self, adapter):
        with self.lock:
            self.scanning.discard(adapter)

    def stop_channel(self, key):
        with self.lock:
            targets = [s for s in self.sessions.values() if s.key == key]
            for session in targets:
                self.sessions.pop((session.adapter, session.key), None)
                session.pinned = False
                session.stop()
            self._collect()
            return bool(targets)

    def stop_all(self):
        with self.lock:
            sessions, self.sessions = list(self.sessions.values()), {}
            for session in sessions:
                session.pinned = False
                session.stop()
            self._collect()

    def status(self):
        with self.lock:
            return [s.status() for s in self.sessions.values()]
