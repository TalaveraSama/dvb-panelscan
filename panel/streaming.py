"""Bounded, shared TS subscriptions. One service per adapter, not a full mux engine."""
import collections
import os
import subprocess
import threading
import time


class StreamError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class Session:
    def __init__(self, key, adapter, command, log_path, http_port=0, udp=(), max_bytes=1500000):
        self.key, self.adapter = key, adapter
        self.http_port, self.udp = http_port, tuple(udp)
        self.log_path = log_path
        self.clients, self.pinned = 0, False
        self.condition = threading.Condition()
        self.blocks = collections.deque()
        self.first = self.next = self.buffered = self.bytes_received = 0
        self.overruns = 0
        self.max_bytes = max_bytes
        self.ended = False
        self.created = time.monotonic()
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(log_path, 0o600)
        with os.fdopen(fd, "ab", buffering=0) as log:
            log.write(b"\n--- New shared TS session ---\n")
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                            bufsize=0, start_new_session=True)
        self.worker = threading.Thread(target=self._drain, daemon=True)
        self.worker.start()

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
            with self.condition:
                self.ended = True
                self.condition.notify_all()

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.bytes_received and not self.ended:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise StreamError("Sin TS a tiempo; consulta el registro del servicio", 504)
                self.condition.wait(left)
            if not self.bytes_received or self.ended:
                raise StreamError("tbsstream terminó antes de iniciar; consulta el registro del servicio")

    def cursor(self):
        with self.condition:
            return self.next

    def read(self, cursor, timeout=5):
        deadline = time.monotonic() + timeout
        with self.condition:
            while cursor >= self.next and not self.ended:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise StreamError("Sin nuevos datos TS", 504)
                self.condition.wait(left)
            if cursor < self.first:
                self.overruns += 1
                raise StreamError("Cliente lento: cola TS desbordada; reconecta", 503)
            if self.ended:
                raise StreamError("El servicio TS terminó")
            return cursor + 1, self.blocks[cursor - self.first]

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.worker.join(timeout=4)

    def status(self):
        with self.condition:
            return {"channel_id": self.key, "adapter": self.adapter,
                    "pid": self.process.pid, "running": self.process.poll() is None and not self.ended,
                    "clients": self.clients, "pinned": self.pinned,
                    "http_port": self.http_port, "udp": ", ".join(self.udp),
                    "bytes_received": self.bytes_received, "queue_bytes": self.buffered,
                    "slow_clients": self.overruns, "exit_code": self.process.poll(),
                    "log": os.path.basename(self.log_path)}


class StreamManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.scanning = set()

    def acquire(self, key, adapter, command, log_path, timeout=35, pinned=False,
                http_port=0, udp=()):
        with self.lock:
            previous = self.sessions.get(adapter)
            if previous and (previous.ended or previous.process.poll() is not None):
                previous.stop()
                self.sessions.pop(adapter)
                previous = None
            if adapter in self.scanning:
                raise StreamError("Adaptador ocupado por un escaneo", 409)
            if previous and previous.key != key:
                raise StreamError("Adaptador ocupado por otro canal; detén sus reproducciones primero", 409)
            if pinned and previous and (previous.http_port != http_port or previous.udp != tuple(udp)):
                raise StreamError("Canal ya activo con otras salidas; usa su URL del panel o detén la sesión", 409)
            if not previous:
                if http_port and any(s.http_port == http_port and s.process.poll() is None
                                     for s in self.sessions.values()):
                    raise StreamError("Puerto HTTP ocupado por otra sesión", 409)
                try:
                    previous = Session(key, adapter, command, log_path, http_port, udp)
                except OSError:
                    raise StreamError("No se pudo iniciar tbsstream o abrir su registro") from None
                self.sessions[adapter] = previous
            previous.clients += 1  # reserve before waiting, including concurrent starts
        try:
            previous.wait_ready(timeout)
            if pinned:
                with self.lock:
                    if self.sessions.get(adapter) is not previous:
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
                if self.sessions.get(session.adapter) is session:
                    self.sessions.pop(session.adapter)
                session.stop()

    def reserve_scan(self, adapter):
        with self.lock:
            active = self.sessions.get(adapter)
            if adapter in self.scanning or (active and active.process.poll() is None):
                raise StreamError("Adaptador ocupado; detén streaming antes de escanear", 409)
            self.scanning.add(adapter)

    def release_scan(self, adapter):
        with self.lock:
            self.scanning.discard(adapter)

    def stop_all(self):
        with self.lock:
            sessions, self.sessions = list(self.sessions.values()), {}
            for session in sessions:
                session.pinned = False
                session.stop()

    def status(self):
        with self.lock:
            return [s.status() for s in self.sessions.values()]
