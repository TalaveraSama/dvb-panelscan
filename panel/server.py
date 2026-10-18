#!/usr/bin/env python3
"""
Mini panel DVB-Scan - panel completo para administrar el sintonizador TBS/DVB-S2.

Funciones:
  * Adaptadores DVB
  * Transponders: agregar / listar / borrar / escanear (persistidos en JSON)
  * Canales con EPG (now/next) del último escaneo
  * Exportar lista M3U8 (con tvg-id, tvg-name, group-title)
  * Newcamd: servidor + cuenta + DES key (para descifrar)
  * Streaming: arrancar/parar tbsstream (newcamd + salida UDP + HTTP)

Servidor con la biblioteca estándar de Python (sin dependencias).
"""

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = BASE_DIR

_lock = threading.Lock()

STATE = {
    "transponders": [],
    "scan": None,          # resultado del último escaneo
    "newcamd": {
        "host": "127.0.0.1",
        "port": 5000,
        "user": "astra",
        "pass": "astra",
        "deskey": "0102030405060708091011121314",
    },
    "stream": {"running": False, "pid": None, "udp": "", "http_port": 0},
}

_cfg = {"dvb2m3u8": None, "tbsstream": None}


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------
def _load():
    for name in ("transponders", "newcamd"):
        path = os.path.join(DATA_DIR, name + ".json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    STATE[name] = json.load(f)
            except Exception:
                pass


def _save(name):
    path = os.path.join(DATA_DIR, name + ".json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(STATE[name], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Localizar binarios
# ---------------------------------------------------------------------------
def find_dvb2m3u8(explicit=None):
    if explicit and os.path.isfile(explicit):
        return os.path.abspath(explicit)
    for cand in ("../dvb2m3u8/dvb2m3u8", "../../dvb2m3u8/dvb2m3u8"):
        p = os.path.normpath(os.path.join(BASE_DIR, cand))
        if os.path.isfile(p):
            return p
    return shutil.which("dvb2m3u8")


def find_tbsstream(explicit=None):
    if explicit and os.path.isfile(explicit):
        return os.path.abspath(explicit)
    for cand in ("../tbsstream/tbsstream", "../../tbsstream/tbsstream"):
        p = os.path.normpath(os.path.join(BASE_DIR, cand))
        if os.path.isfile(p):
            return p
    return shutil.which("tbsstream")


def is_linux():
    return platform.system() == "Linux"


def list_adapters():
    adapters = []
    for path in sorted(glob.glob("/dev/dvb/adapter*")):
        num = os.path.basename(path).replace("adapter", "")
        frontends = [os.path.basename(f) for f in sorted(glob.glob(os.path.join(path, "frontend*")))]
        adapters.append({
            "adapter": int(num) if num.isdigit() else 0,
            "path": path,
            "frontends": frontends,
        })
    return adapters


# ---------------------------------------------------------------------------
# Escaneo
# ---------------------------------------------------------------------------
VALID_TYPES = ("dvbt", "dvbt2", "dvbc", "dvbs", "dvbs2")


def _norm_tp(tp):
    """Normaliza/valida los campos de un transponder."""
    return {
        "type": tp.get("type") if tp.get("type") in VALID_TYPES else "dvbs2",
        "freq_mhz": float(tp.get("freq_mhz") or 0),
        "lnb_lo": float(tp.get("lnb_lo") if tp.get("lnb_lo") is not None else 9750),
        "symbol_rate": int(tp.get("symbol_rate") or 0),
        "pol": str(tp.get("pol") or "V").upper()[:1],
        "adapter": int(tp.get("adapter") or 0),
        "timeout": int(tp.get("timeout") or 15),
    }


def _scan_cmd(binary, tp):
    cmd = [binary, "-t", tp["type"], "--json",
           "--freq-mhz", str(tp["freq_mhz"]),
           "--lnb-lo", str(tp["lnb_lo"]),
           "--pol", tp["pol"],
           "-a", str(tp["adapter"]),
           "-T", str(tp["timeout"]),
           "-v"]
    if tp["symbol_rate"]:
        cmd += ["--symbol-rate", str(tp["symbol_rate"])]
    return cmd


def run_scan(tp):
    tp = _norm_tp(tp)
    binary = find_dvb2m3u8(_cfg["dvb2m3u8"])
    if not (is_linux() and binary):
        return {"ok": False, "error": "dvb2m3u8 no disponible (¿Linux?)"}

    cmd = _scan_cmd(binary, tp)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=tp["timeout"] + 15)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout del escaneo"}
    except OSError as e:
        return {"ok": False, "error": str(e)}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "sin lock/error").strip()}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "salida JSON inválida"}

    data["ok"] = True
    data["transponder"] = tp
    data["time"] = time.time()
    data["demo"] = False
    # señal del stderr ("señal: N")
    for line in proc.stderr.splitlines():
        if "señal:" in line.lower() or "senal:" in line.lower():
            try:
                data["signal"] = int(line.split(":")[-1].strip())
            except ValueError:
                pass
    with _lock:
        STATE["scan"] = data
    return data


# ---------------------------------------------------------------------------
# M3U8
# ---------------------------------------------------------------------------
def build_m3u8(scan, base_url=None):
    lines = ["#EXTM3U"]
    for s in (scan or {}).get("services", []):
        name = s.get("name") or "Service %s" % s.get("sid")
        group = s.get("provider") or (scan or {}).get("network") or "DVB"
        radio = ' radio="true"' if s.get("type") in (2, 0x0A) else ""
        attrs = 'tvg-id="%s" tvg-name="%s" group-title="%s"%s' % (s.get("sid"), name, group, radio)
        lines.append("#EXTINF:-1 %s,%s" % (attrs, name))
        if base_url:
            lines.append("%s%s" % (base_url, s.get("sid")))
        else:
            lines.append("dvb://adapter%s/%s/%s" % (
                (scan or {}).get("adapter", 0), (scan or {}).get("frequency", 0), s.get("sid")))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Streaming (tbsstream)
# ---------------------------------------------------------------------------
def stream_start(tp, newcamd, udp, http_port):
    binary = find_tbsstream(_cfg["tbsstream"])
    if not (is_linux() and binary):
        return {"ok": False, "error": "tbsstream no disponible"}
    if STATE["stream"]["running"]:
        return {"ok": False, "error": "ya hay un stream corriendo"}

    tp = _norm_tp(tp)
    nc = newcamd or STATE["newcamd"]
    cmd = [binary, "-t", tp["type"],
           "--freq-mhz", str(tp["freq_mhz"]),
           "--lnb-lo", str(tp["lnb_lo"]),
           "--pol", tp["pol"],
           "-a", str(tp["adapter"]),
           "-T", str(tp["timeout"]),
           "--newcamd", "%s:%s" % (nc.get("host"), nc.get("port")),
           "--deskey", nc.get("deskey", ""),
           "--user", nc.get("user", ""),
           "--pass", nc.get("pass", ""),
           "--http-port", str(int(http_port or 9981))]
    if tp["symbol_rate"]:
        cmd += ["--symbol-rate", str(tp["symbol_rate"])]
    for u in (udp or []):
        cmd += ["--udp", u]

    try:
        log = open(os.path.join(DATA_DIR, "stream.log"), "w")
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    STATE["stream"] = {"running": True, "pid": p.pid, "udp": ", ".join(udp or []),
                       "http_port": int(http_port or 9981), "cmd": " ".join(cmd)}
    return {"ok": True, "pid": p.pid, "cmd": " ".join(cmd)}


def stream_stop():
    st = STATE["stream"]
    if not st.get("running") or not st.get("pid"):
        STATE["stream"] = {"running": False}
        return {"ok": True}
    try:
        os.kill(int(st["pid"]), 15)
        time.sleep(0.5)
        try:
            os.kill(int(st["pid"]), 9)
        except OSError:
            pass
    except OSError:
        pass
    STATE["stream"] = {"running": False}
    return {"ok": True}


def stream_status():
    st = STATE["stream"]
    alive = False
    if st.get("running") and st.get("pid"):
        try:
            os.kill(int(st["pid"]), 0)
            alive = True
        except OSError:
            alive = False
    if st.get("running") and not alive:
        STATE["stream"] = {"running": False}
    return STATE["stream"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_cfg = {"dvb2m3u8": None, "tbsstream": None}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".m3u8": "application/vnd.apple.mpegurl",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "MiniDVBPanel/2.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))
        if path.startswith("/static/"):
            return self._send_file(os.path.join(STATIC_DIR, path[len("/static/"):]))

        if path == "/api/status":
            return self._send(200, {
                "platform": platform.system(),
                "linux": is_linux(),
                "dvb2m3u8": find_dvb2m3u8(_cfg["dvb2m3u8"]),
                "tbsstream": find_tbsstream(_cfg["tbsstream"]),
                "adapters": list_adapters() if is_linux() else [],
                "stream": stream_status(),
            })
        if path == "/api/transponders":
            return self._send(200, STATE["transponders"])
        if path == "/api/channels" or path == "/api/epg":
            return self._send(200, STATE["scan"] or {"services": [], "network": ""})
        if path == "/api/newcamd":
            return self._send(200, STATE["newcamd"])
        if path == "/api/stream/status":
            return self._send(200, stream_status())
        if path == "/api/playlist.m3u8":
            base = qs.get("base_url", [None])[0]
            return self._send(200, build_m3u8(STATE["scan"], base),
                              "application/vnd.apple.mpegurl")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = self._read_body()

        if path == "/api/transponders":
            tp = _norm_tp(body)
            tp["id"] = uuid.uuid4().hex[:8]
            tp["name"] = body.get("name") or ("%s %s%s" % (tp["type"].upper(), tp["freq_mhz"], tp["pol"]))
            with _lock:
                STATE["transponders"].append(tp)
                _save("transponders")
            return self._send(200, {"ok": True, "transponder": tp})
        if path.startswith("/api/transponders/") and path.endswith("/delete"):
            tid = path[len("/api/transponders/"):-len("/delete")]
            with _lock:
                STATE["transponders"] = [t for t in STATE["transponders"] if t.get("id") != tid]
                _save("transponders")
            return self._send(200, {"ok": True})
        if path.startswith("/api/scan"):
            tid = body.get("id")
            tp = None
            if tid:
                tp = next((t for t in STATE["transponders"] if t.get("id") == tid), None)
            if tp is None:
                tp = body
            r = run_scan(tp)
            return self._send(200, r)
        if path == "/api/newcamd":
            nc = dict(STATE["newcamd"])
            for k in ("host", "user", "pass", "deskey"):
                if k in body:
                    nc[k] = str(body[k])
            if "port" in body:
                nc["port"] = int(body["port"] or 0)
            STATE["newcamd"] = nc
            _save("newcamd")
            return self._send(200, {"ok": True, "newcamd": nc})
        if path == "/api/stream/start":
            udp = body.get("udp") or []
            if isinstance(udp, str):
                udp = [udp]
            return self._send(200, stream_start(body.get("transponder") or body,
                                                body.get("newcamd"),
                                                udp, body.get("http_port")))
        if path == "/api/stream/stop":
            return self._send(200, stream_stop())
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[panel] %s\n" % (fmt % args))


def main():
    ap = argparse.ArgumentParser(description="Mini panel DVB-Scan")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--dvb2m3u8", default=None)
    ap.add_argument("--tbsstream", default=None)
    args = ap.parse_args()

    _cfg["dvb2m3u8"] = args.dvb2m3u8
    _cfg["tbsstream"] = args.tbsstream
    _load()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Mini panel DVB-Scan en http://%s:%d" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
