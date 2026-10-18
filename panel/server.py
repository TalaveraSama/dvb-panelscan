#!/usr/bin/env python3
"""
Mini panel DVB-Scan - panel con dashboard, EPG, newcamd y M3U8.

  * Dashboard: adaptadores + transponders (cada uno con sus canales escaneados)
  * EPG: agregado de todos los transponders escaneados (now/next)
  * Newcamd: servidor/cuenta/DES key
  * M3U8: exporta la lista de canales de todos los transponders
  * Streaming: arranca/para tbsstream (newcamd + UDP + HTTP)

Sin dependencias externas (solo stdlib de Python).
"""

import argparse
import glob
import json
import os
import platform
import shutil
import socket
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
    "transponders": [],   # cada uno puede tener "scan" (canales escaneados)
    "newcamd": {
        "host": "127.0.0.1", "port": 5000, "user": "astra",
        "pass": "astra", "deskey": "0102030405060708091011121314",
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


def _save_transponder(tp):
    for i, t in enumerate(STATE["transponders"]):
        if t.get("id") == tp.get("id"):
            STATE["transponders"][i] = tp
            break
    _save("transponders")


# ---------------------------------------------------------------------------
# Binarios / adaptadores
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
        adapters.append({"adapter": int(num) if num.isdigit() else 0,
                         "path": path, "frontends": frontends})
    return adapters


# ---------------------------------------------------------------------------
# Transponders / escaneo
# ---------------------------------------------------------------------------
VALID_TYPES = ("dvbt", "dvbt2", "dvbc", "dvbs", "dvbs2")


def _norm_tp(tp):
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
           "--pol", tp["pol"], "-a", str(tp["adapter"]),
           "-T", str(tp["timeout"]), "-v"]
    if tp["symbol_rate"]:
        cmd += ["--symbol-rate", str(tp["symbol_rate"])]
    return cmd


def run_scan(tp):
    """Escanea un transponder y guarda el resultado dentro del propio transponder."""
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
    data["time"] = time.time()
    data["demo"] = False
    for line in proc.stderr.splitlines():
        if "señal:" in line.lower() or "senal:" in line.lower():
            try:
                data["signal"] = int(line.split(":")[-1].strip())
            except ValueError:
                pass
    return data


def _find_tp(tp_id):
    return next((t for t in STATE["transponders"] if t.get("id") == tp_id), None)


def _find_tp_by_sid(sid):
    try:
        sid = int(sid)
    except ValueError:
        return None
    with _lock:
        for t in STATE["transponders"]:
            scan = t.get("scan")
            if scan and any(s.get("sid") == sid for s in scan.get("services", [])):
                return t
    return None


# ---------------------------------------------------------------------------
# EPG agregado
# ---------------------------------------------------------------------------
def epg_aggregate():
    """Junta los canales (con now/next) de todos los transponders escaneados."""
    out = []
    with _lock:
        for t in STATE["transponders"]:
            scan = t.get("scan")
            if not scan:
                continue
            out.append({
                "transponder_id": t.get("id"),
                "transponder": t.get("name"),
                "type": t.get("type"),
                "freq_mhz": t.get("freq_mhz"),
                "network": scan.get("network"),
                "time": scan.get("time"),
                "services": scan.get("services", []),
            })
    return out


# ---------------------------------------------------------------------------
# M3U8 (todos los transponders)
# ---------------------------------------------------------------------------
def build_m3u8(base_url=None, tid=None, profile="pass"):
    """Genera M3U8 estilo TVHeadend: cada canal -> URL de stream TS.
    base_url acepta el placeholder {sid} (p. ej. http://host:9981/stream/channelid/{sid}).
    Si no se da base_url, se usa el host del request (self.headers Host)."""
    lines = ["#EXTM3U"]
    with _lock:
        tps = STATE["transponders"]
    for t in tps:
        if tid and t.get("id") != tid:
            continue
        scan = t.get("scan")
        if not scan:
            continue
        adapter = t.get("adapter", 0)
        for s in scan.get("services", []):
            name = s.get("name") or "Service %s" % s.get("sid")
            group = s.get("provider") or scan.get("network") or t.get("name") or "DVB"
            radio = ' radio="true"' if s.get("type") in (2, 0x0A) else ""
            lines.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s" group-title="%s"%s,%s'
                         % (s.get("sid"), name, group, radio, name))
            if base_url:
                url = base_url.replace("{sid}", str(s.get("sid")))
                sep = "&" if "?" in url else "?"
                lines.append("%s%sprofile=%s" % (url, sep, profile))
            else:
                lines.append("dvb://adapter%s/%s/%s" % (adapter, t.get("freq_mhz", 0), s.get("sid")))
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
    cmd = [binary, "-t", tp["type"], "--freq-mhz", str(tp["freq_mhz"]),
           "--lnb-lo", str(tp["lnb_lo"]), "--pol", tp["pol"],
           "-a", str(tp["adapter"]), "-T", str(tp["timeout"]),
           "--newcamd", "%s:%s" % (nc.get("host"), nc.get("port")),
           "--deskey", nc.get("deskey", ""),
           "--user", nc.get("user", ""), "--pass", nc.get("pass", ""),
           "--http-port", str(int(http_port or 9981))]
    if tp["symbol_rate"]:
        cmd += ["--symbol-rate", str(tp["symbol_rate"])]
    for u in (udp or []):
        cmd += ["--udp", u]

    try:
        log = open(os.path.join(DATA_DIR, "stream.log"), "w")
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    STATE["stream"] = {"running": True, "pid": p.pid, "udp": ", ".join(udp or []),
                       "http_port": int(http_port or 9981), "cmd": " ".join(cmd)}
    return {"ok": True, "pid": p.pid, "cmd": " ".join(cmd)}


def stream_stop():
    st = STATE["stream"]
    if st.get("running") and st.get("pid"):
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
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".m3u8": "application/vnd.apple.mpegurl",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "MiniDVBPanel/3.0"

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
                "platform": platform.system(), "linux": is_linux(),
                "dvb2m3u8": find_dvb2m3u8(_cfg["dvb2m3u8"]),
                "tbsstream": find_tbsstream(_cfg["tbsstream"]),
                "adapters": list_adapters() if is_linux() else [],
                "stream": stream_status(),
            })
        if path == "/api/transponders":
            return self._send(200, STATE["transponders"])
        if path == "/api/epg":
            return self._send(200, epg_aggregate())
        if path == "/api/newcamd":
            return self._send(200, STATE["newcamd"])
        if path == "/api/stream/status":
            return self._send(200, stream_status())
        if path == "/api/playlist.m3u8":
            base = qs.get("base_url", [None])[0]
            tid = qs.get("transponder", [None])[0]
            profile = qs.get("profile", ["pass"])[0]
            if not base:
                host = self.headers.get("Host", "127.0.0.1:8081")
                base = "http://%s/stream/channelid/{sid}" % host
            return self._send(200, build_m3u8(base, tid, profile), "application/vnd.apple.mpegurl")
        if path.startswith("/stream/channelid/"):
            return self._stream_channel(path[len("/stream/channelid/"):])
        return self._send(404, {"error": "not found"})

    def _stream_channel(self, sid):
        """Sirve el TS de un canal: arranca tbsstream (-s SID) -> UDP local -> HTTP."""
        tp = _find_tp_by_sid(sid)
        if not tp:
            return self._send(404, {"error": "SID %s no encontrado" % sid})
        tbs = find_tbsstream(_cfg["tbsstream"])
        if not tbs:
            return self._send(500, {"error": "tbsstream no disponible"})
        nc = STATE["newcamd"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        cmd = [tbs, "-t", tp.get("type", "dvbs2"),
               "--freq-mhz", str(tp.get("freq_mhz")),
               "--lnb-lo", str(tp.get("lnb_lo", 9750)),
               "--pol", tp.get("pol", "V"),
               "-a", str(tp.get("adapter", 0)),
               "-s", sid,
               "--symbol-rate", str(tp.get("symbol_rate", 0)),
               "--newcamd", "%s:%s" % (nc.get("host"), nc.get("port")),
               "--deskey", nc.get("deskey", ""),
               "--user", nc.get("user", ""), "--pass", nc.get("pass", ""),
               "--udp", "127.0.0.1:%d" % port]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Connection", "close")
        self.end_headers()
        sock.settimeout(1)
        deadline = time.time() + 3600
        try:
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(1316)
                    self.wfile.write(data)
                except socket.timeout:
                    continue
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            sock.close()
            try:
                proc.terminate()
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = self._read_body()

        if path == "/api/transponders":
            tp = _norm_tp(body)
            tp["id"] = uuid.uuid4().hex[:8]
            tp["name"] = body.get("name") or ("%s %s%s" % (tp["type"].upper(), tp["freq_mhz"], tp["pol"]))
            tp["scan"] = None
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

        if path == "/api/scan":
            tid = body.get("id")
            tp = _find_tp(tid) or _norm_tp(body)
            result = run_scan(tp)
            if result.get("ok") and tid:
                with _lock:
                    t = _find_tp(tid)
                    if t:
                        t["scan"] = result
                        _save_transponder(t)
            return self._send(200, result)

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
            tp = _find_tp(body.get("id")) or body.get("transponder") or body
            return self._send(200, stream_start(tp, body.get("newcamd"), udp, body.get("http_port")))
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
