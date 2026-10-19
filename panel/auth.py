"""Single-admin authentication. Password hashes on disk; sessions only in RAM."""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError


class AuthStore:
    ttl = 8 * 3600
    iterations = 600_000

    def __init__(self, path, secure=False, clock=time.time):
        self.path, self.secure, self.clock = path, secure, clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = {}
        self.workers = threading.BoundedSemaphore(4)
        self.account = {}
        try:
            with open(path, encoding="utf-8") as source:
                self.account = json.load(source)
        except FileNotFoundError:
            pass

    def _save(self):
        temporary = self.path + ".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(self.account, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, self.path)

    def set_admin(self, username, password):
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 64:
            raise ValueError("Usuario requerido (máximo 64 caracteres)")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("Contraseña: entre 12 y 256 caracteres")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), self.iterations
        ).hex()
        with self.lock:
            self.account = {
                "username": username.strip(),
                "salt": salt,
                "hash": digest,
                "iterations": self.iterations,
            }
            self._save()
            self.sessions.clear()
            self.failures.clear()

    def login(self, username, password, ip):
        now = self.clock()
        with self.lock:
            self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
            count, until = self.failures.get(ip, (0, now + 600))
            if count >= 10 or len(self.failures) >= 1024:
                return None, 429
            self.failures[ip] = (count + 1, until)
            account = dict(self.account)
        if not self.workers.acquire(blocking=False):
            return None, 429
        try:
            salt = bytes.fromhex(account.get("salt", "00" * 16))
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                str(password).encode(),
                salt,
                account.get("iterations", self.iterations),
            ).hex()
            valid = hmac.compare_digest(digest, account.get("hash", ""))
            valid = (
                hmac.compare_digest(
                    str(username).encode(), account.get("username", "").encode()
                )
                and valid
            )
        finally:
            self.workers.release()
        if not valid:
            return None, 401
        with self.lock:
            if account != self.account:
                return None, 401
            self.failures.pop(ip, None)
            self.sessions = {
                k: v for k, v in self.sessions.items() if v["expires"] > now
            }
            if len(self.sessions) >= 64:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {
                "expires": now + self.ttl,
                "csrf": secrets.token_urlsafe(32),
                "username": username,
            }
        return token, 200

    def session(self, cookie):
        try:
            cookies = SimpleCookie()
            cookies.load(cookie or "")
            token = cookies["dvb_session"].value
        except (KeyError, ValueError, CookieError):
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            session = self.sessions.get(key)
            if session and session["expires"] > self.clock():
                return dict(session)
            self.sessions.pop(key, None)
        return None

    def logout(self, cookie):
        try:
            cookies = SimpleCookie()
            cookies.load(cookie or "")
            key = hashlib.sha256(cookies["dvb_session"].value.encode()).hexdigest()
            with self.lock:
                self.sessions.pop(key, None)
        except (KeyError, ValueError, CookieError):
            pass

    def cookie(self, token=""):
        return "dvb_session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d%s" % (
            token,
            self.ttl if token else 0,
            "; Secure" if self.secure else "",
        )

    def rotate_media_token(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.account["media_hash"] = hashlib.sha256(token.encode()).hexdigest()
            self._save()
        return token

    def media_allowed(self, token):
        with self.lock:
            expected = self.account.get("media_hash", "")
        return bool(expected) and hmac.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), expected
        )
