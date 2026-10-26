"""Independent IPTV accounts and revocable signed playback credentials."""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from auth import AuthStore
from astra_import import atomic_json


class IPTVStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.verifiers = {}
        try:
            with open(path, encoding="utf-8") as source:
                self.data = json.load(source)
        except FileNotFoundError:
            self.data = {"key": secrets.token_hex(32), "backend_key": secrets.token_urlsafe(32), "users": {}}

    def listing(self):
        with self.lock:
            return [{k: copy.deepcopy(v) for k, v in user.items() if k not in ("account", "revision")}
                    for user in self.data["users"].values()]

    def save_user(self, body):
        name = body.get("username", "")
        channels = body.get("channels", [])
        expires = body.get("expires", 0)
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError("Usuario: 1–64 letras, números, punto, guion o guion bajo")
        if not isinstance(channels, list) or len(channels) > 10000 or any(not isinstance(c, str) or len(c) > 128 or ":" not in c for c in channels):
            raise ValueError("Canales: lista de identidades TP:SID")
        if not isinstance(expires, (int, float)) or not 0 <= expires <= 32503680000:
            raise ValueError("Vencimiento inválido")
        if not isinstance(body.get("enabled", True), bool):
            raise ValueError("Estado inválido")
        password = body.get("password", "")
        if not isinstance(password, str) or (password and not 12 <= len(password) <= 256):
            raise ValueError("Contraseña: 12–256 caracteres")
        with self.lock:
            old = self.data["users"].get(name, {})
            account = old.get("account")
            if password:
                salt = secrets.token_hex(16)
                account = {"username": name, "salt": salt, "iterations": AuthStore.iterations,
                           "hash": hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), AuthStore.iterations).hex()}
            if not account:
                raise ValueError("Introduce una contraseña para el usuario nuevo")
            updated = copy.deepcopy(self.data)
            updated["users"][name] = {"username": name, "enabled": body.get("enabled", True),
                "expires": expires, "channels": sorted(set(channels)), "account": account,
                "revision": secrets.token_hex(16)}
            atomic_json(self.path, updated)
            self.data = updated
            self.verifiers.pop(name, None)

    def _active(self, user):
        return user and user["enabled"] and (not user["expires"] or user["expires"] > time.time())

    def credentials(self, username, password, ip):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
            return None
        with self.lock:
            user = self.data["users"].get(username)
            if not self._active(user):
                return None
            verifier = self.verifiers.get(username)
            if verifier is None:
                verifier = AuthStore(self.path + ".unused")
                verifier.account = dict(user["account"])
                self.verifiers[username] = verifier
            revision = user["revision"]
        token, _ = verifier.login(username, password, ip)
        if not token:
            return None
        with verifier.lock:
            verifier.sessions.clear()
        with self.lock:
            current = self.data["users"].get(username)
            if not self._active(current) or current["revision"] != revision:
                return None
            return self.token(username)

    def token(self, username):
        with self.lock:
            user = self.data["users"].get(username)
            if not self._active(user):
                raise ValueError("Usuario deshabilitado o vencido")
            payload = base64.urlsafe_b64encode((username + ":" + user["revision"]).encode()).decode().rstrip("=")
            signature = hmac.new(bytes.fromhex(self.data["key"]), payload.encode(), hashlib.sha256).hexdigest()
            return payload + "." + signature

    def allowed(self, token, channel=None):
        if not isinstance(token, str) or len(token) > 512:
            return False
        with self.lock:
            try:
                payload, signature = token.split(".")
                expected = hmac.new(bytes.fromhex(self.data["key"]), payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    return False
                username, revision = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split(":")
                user = self.data["users"].get(username)
                return bool(self._active(user) and hmac.compare_digest(revision, user["revision"]) and
                            (channel is None or channel in user["channels"]))
            except (ValueError, UnicodeError, KeyError):
                return False
