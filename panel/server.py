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
import copy
import glob
import json
import os
import platform
import shutil
import signal
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
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from streaming import StreamManager, StreamError

DATA_DIR = os.environ.get("DVB_PANEL_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)

_lock = threading.Lock()

STATE = {
    "transponders": [],   # cada uno puede tener "scan" (canales escaneados)
    "newcamd": {
        "host": "127.0.0.1", "port": 5000, "user": "",
        "pass": "", "deskey": "",
    },
    "profiles": {},
    "channels": {},
    "stream": {"running": False, "pid": None, "udp": "", "http_port": 0},
}

_cfg = {"dvb2m3u8": None, "tbsstream": None}
_streams = StreamManager()


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------
def _load():
    for name in ("transponders", "newcamd", "profiles", "channels"):
        path = os.path.join(DATA_DIR, name + ".json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    STATE[name] = json.load(f)
            except Exception:
                pass


def _save(name):
    path = os.path.join(DATA_DIR, name + ".json")
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(STATE[name], f, ensure_ascii=False, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


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


def _run_scan(tp):
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


def run_scan(tp):
    try:
        adapter = _norm_tp(tp)["adapter"]
        _streams.reserve_scan(adapter)
    except (StreamError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    try:
        return _run_scan(tp)
    finally:
        _streams.release_scan(adapter)


def _find_tp(tp_id):
    return next((t for t in STATE["transponders"] if t.get("id") == tp_id), None)


def channel_id(tp, sid):
    return "%s:%d" % (tp["id"], int(sid))


def channel_view(tp, service):
    result = dict(service)
    identity = channel_id(tp, service["sid"])
    result.update(STATE["channels"].get(identity, {}))
    result.update(channel_id=identity, sid=service["sid"])
    result.setdefault("enabled", True)
    result.setdefault("newcamd_profile", "default")
    profile = (STATE["newcamd"] if result["newcamd_profile"] == "default" else
               STATE["profiles"].get(result["newcamd_profile"], {}))
    result["profile_name"] = profile.get("name") or ("Predeterminado" if result["newcamd_profile"] == "default" else result["newcamd_profile"])
    return result


def transponder_views():
    result = copy.deepcopy(STATE["transponders"])
    for tp in result:
        if tp.get("scan"):
            tp["scan"]["services"] = [channel_view(tp, s) for s in tp["scan"].get("services", [])]
    return result


def profile_config(identity="default"):
    if not isinstance(identity, str):
        raise StreamError("Identificador de perfil inválido", 400)
    if identity == "default":
        return STATE["newcamd"]
    if identity not in STATE["profiles"]:
        raise StreamError("Perfil Newcamd no encontrado", 400)
    return STATE["profiles"][identity]


def validate_profile(body, previous=None):
    result = dict(previous or {})
    for key in ("host", "user", "pass", "deskey", "name"):
        if key in body:
            if not isinstance(body[key], str) or len(body[key]) > (128 if key == "host" else 80):
                raise ValueError("Campo Newcamd inválido: " + key)
            result[key] = body[key].strip() if key in ("host", "name", "deskey") else body[key]
    result["port"] = int(body.get("port", result.get("port", 5000)))
    key = result.get("deskey", "").replace(":", "").replace(" ", "")
    if not result.get("host") or not 1 <= result["port"] <= 65535:
        raise ValueError("Servidor o puerto inválidos")
    if len(key) != 28 or len(bytes.fromhex(key)) != 14:
        raise ValueError("DES key debe contener 14 bytes (28 caracteres hex)")
    if len(result.get("user", "")) > 63 or len(result.get("pass", "")) > 63:
        raise ValueError("Usuario/contraseña: máximo 63 caracteres")
    result["deskey"] = key
    return result


def stop_profile_channels(profile):
    for session in _streams.status():
        if STATE["channels"].get(session["channel_id"], {}).get("newcamd_profile", "default") == profile:
            _streams.stop_channel(session["channel_id"])


def resolve_channel(identity):
    """Canonical TP:SID; retain SID-only URLs only when unambiguous."""
    tid, sep, sid = str(identity).rpartition(":")
    try:
        sid = int(sid)
        if not 1 <= sid <= 65535:
            raise ValueError()
    except (TypeError, ValueError):
        raise StreamError("Identidad de canal inválida", 400)
    with _lock:
        candidates = [(t, sid) for t in STATE["transponders"]
                      if (not sep or t.get("id") == tid) and
                      any(int(c.get("sid", 0)) == sid
                          for c in (t.get("scan") or {}).get("services", []))]
    if not candidates:
        raise StreamError("Canal no encontrado; escanea el transponder", 404)
    if len(candidates) != 1:
        raise StreamError("SID ambiguo; descarga la nueva lista con identidad TP:SID", 409)
    return candidates[0]


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
                "services": [channel_view(t, s) for s in scan.get("services", [])],
            })
    return out


# ---------------------------------------------------------------------------
# M3U8 (todos los transponders)
# ---------------------------------------------------------------------------
def _m3u_text(value):
    return str(value).replace("\r", " ").replace("\n", " ").replace('"', "'")


def build_m3u8(base_url=None, tid=None, profile="pass"):
    """Genera M3U8 estilo TVHeadend: cada canal -> URL de stream TS.
    base_url acepta el placeholder {sid} (p. ej. http://host:9981/stream/channelid/{sid}).
    Si no se da base_url, se usa el host del request (self.headers Host)."""
    lines = ["#EXTM3U"]
    with _lock:
        tps = copy.deepcopy(STATE["transponders"])
    for t in tps:
        if tid and t.get("id") != tid:
            continue
        scan = t.get("scan")
        if not scan:
            continue
        adapter = t.get("adapter", 0)
        for raw in scan.get("services", []):
            s = channel_view(t, raw)
            if not s["enabled"]:
                continue
            name = _m3u_text(s.get("name") or "Service %s" % s.get("sid"))
            group = _m3u_text(s.get("group") or s.get("provider") or scan.get("network") or t.get("name") or "DVB")
            identity = channel_id(t, s["sid"])
            radio = ' radio="true"' if s.get("type") in (2, 0x0A) else ""
            extras = (' tvg-logo="%s"' % _m3u_text(s["logo"]) if s.get("logo") else "")
            extras += (' tvg-chno="%s"' % s["number"] if s.get("number") else "")
            lines.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s" group-title="%s"%s%s,%s'
                         % (identity, name, group, radio, extras, name))
            if base_url:
                url = (base_url.replace("{sid}", str(s.get("sid")))
                       .replace("{channel_id}", urllib.parse.quote(identity, safe=":"))
                       .replace("{transponder}", str(t["id"])))
                parsed = urllib.parse.urlsplit(url)
                query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                query = [(k, v) for k, v in query if k != "profile"] + [("profile", "pass")]
                lines.append(urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query))))
            else:
                lines.append("dvb://adapter%s/%s/%s" % (adapter, t.get("freq_mhz", 0), s.get("sid")))
    return "\n".join(lines) + "\n"


def _xml_esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_xmltv(tid=None):
    """XMLTV (EPG) para XUI.one / Xtream Codes (xcode)."""
    import datetime
    now = datetime.datetime.now().astimezone()

    def fmt(dt):
        return dt.strftime("%Y%m%d%H%M%S %z")

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<tv generator-info-name="Mini DVB-Scan">']
    with _lock:
        tps = copy.deepcopy(STATE["transponders"])
    for t in tps:
        if tid and t.get("id") != tid:
            continue
        scan = t.get("scan")
        if not scan:
            continue
        for raw in scan.get("services", []):
            s = channel_view(t, raw)
            if not s["enabled"]:
                continue
            sid = channel_id(t, s["sid"])
            name = _xml_esc(s.get("name") or "Service %s" % sid)
            lines.append('<channel id="%s"><display-name lang="es">%s</display-name></channel>' % (sid, name))
            if s.get("now"):
                start, stop = now, now + datetime.timedelta(hours=1)
                lines.append('<programme start="%s" stop="%s" channel="%s"><title lang="es">%s</title></programme>'
                             % (fmt(start), fmt(stop), sid, _xml_esc(s.get("now"))))
            if s.get("next"):
                start = now + datetime.timedelta(hours=1)
                stop = now + datetime.timedelta(hours=2)
                lines.append('<programme start="%s" stop="%s" channel="%s"><title lang="es">%s</title></programme>'
                             % (fmt(start), fmt(stop), sid, _xml_esc(s.get("next"))))
    lines.append('</tv>')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Streaming (tbsstream)
# ---------------------------------------------------------------------------
def _session_start(tp, nc=None, udp=(), http_port=0, sid=None, pinned=False):
    binary = find_tbsstream(_cfg["tbsstream"])
    if not (is_linux() and binary):
        raise StreamError("tbsstream no disponible")
    normalized = _norm_tp(tp)
    if normalized["freq_mhz"] <= 0:
        raise StreamError("Frecuencia inválida", 400)
    if sid is None:
        channels = (tp.get("scan") or {}).get("services", [])
        if not channels:
            raise StreamError("Selecciona un SID o escanea primero", 400)
        sid = channels[0]["sid"]
    try:
        sid, http_port = int(sid), int(http_port)
        if not 1 <= sid <= 65535 or not 0 <= http_port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise StreamError("SID o puerto HTTP inválido", 400)
    key = channel_id(tp, sid)
    settings = STATE["channels"].get(key, {})
    if not settings.get("enabled", True):
        raise StreamError("Canal deshabilitado", 403)
    if nc is None:
        nc = profile_config(settings.get("newcamd_profile", "default"))
    tuning = [binary, "-t", normalized["type"], "--freq-mhz", str(normalized["freq_mhz"]),
              "--lnb-lo", str(normalized["lnb_lo"]), "--pol", normalized["pol"],
              "-a", str(normalized["adapter"]), "-T", str(normalized["timeout"])]
    if normalized["symbol_rate"]:
        tuning += ["--symbol-rate", str(normalized["symbol_rate"])]
    capture = tuning + ["--mux-only", "--stdout", "--http-port", "0", "--verbose"]
    # Workers never open/tune DVB devices. Newcamd credentials stay in the
    # single broker, not in every service process.
    cmd = [binary, "--stdin", "--sid", str(sid), "--stdout", "--verbose",
           "--http-port", str(http_port)]
    if not isinstance(udp, (list, tuple)) or any(not isinstance(u, str) for u in udp):
        raise StreamError("Destinos UDP inválidos", 400)
    for destination in udp:
        cmd += ["--udp", destination]
    ca = [binary, "--newcamd", "%s:%s" % (nc.get("host"), nc.get("port")),
          "--deskey", nc.get("deskey", ""), "--user", nc.get("user", ""),
          "--pass", nc.get("pass", "")]
    log = os.path.join(DATA_DIR, "stream-%d-%d.log" % (normalized["adapter"], sid))
    mux_log = os.path.join(DATA_DIR, "mux-%d.log" % normalized["adapter"])
    # Exclude the acquisition timeout: it does not change the RF tuning.
    mux_id = tuple(normalized[k] for k in
                   ("type", "freq_mhz", "lnb_lo", "pol", "symbol_rate", "adapter"))
    return _streams.acquire(key, normalized["adapter"], cmd, log,
                            timeout=normalized["timeout"] + 20, pinned=pinned,
                            http_port=http_port, udp=udp, mux_id=mux_id,
                            mux_command=capture, mux_log=mux_log,
                            broker_command=ca, broker_id=tuple(ca),
                            broker_log=os.path.join(DATA_DIR, "ca.log"))


def stream_start(tp, newcamd, udp, http_port, sid=None):
    try:
        session = _session_start(tp, newcamd, udp,
                                 9981 if http_port is None else http_port, sid, True)
        result = {"ok": True, **session.status(),
                  "url": "/stream/channel/" + session.key + "?profile=pass"}
        _streams.release(session)
        return result
    except (StreamError, ValueError, TypeError, KeyError) as e:
        return {"ok": False, "error": str(e)}


def stream_stop():
    _streams.stop_all()
    return {"ok": True}


def stream_status():
    sessions = _streams.status()
    for session in sessions:
        settings = STATE["channels"].get(session["channel_id"], {})
        profile_id = settings.get("newcamd_profile", "default")
        profile = STATE["newcamd"] if profile_id == "default" else STATE["profiles"].get(profile_id, {})
        session.update(newcamd_profile=profile_id, profile_name=profile.get("name") or profile_id)
    active = [s for s in sessions if s["running"]]
    first = active[0] if active else {}
    return {**first, "running": bool(active), "sessions": sessions,
            "clients": sum(s["clients"] for s in active)}


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
            target = os.path.realpath(os.path.join(STATIC_DIR, urllib.parse.unquote(path[len("/static/"):])) )
            if os.path.commonpath((os.path.realpath(STATIC_DIR), target)) != os.path.realpath(STATIC_DIR):
                return self._send(403, {"error": "Ruta no permitida"})
            return self._send_file(target)

        if path == "/api/status":
            return self._send(200, {
                "platform": platform.system(), "linux": is_linux(),
                "dvb2m3u8": find_dvb2m3u8(_cfg["dvb2m3u8"]),
                "tbsstream": find_tbsstream(_cfg["tbsstream"]),
                "adapters": list_adapters() if is_linux() else [],
                "stream": stream_status(),
            })
        if path == "/api/transponders":
            return self._send(200, transponder_views())
        if path == "/api/epg":
            return self._send(200, epg_aggregate())
        if path == "/api/epg.xml":
            tid = qs.get("transponder", [None])[0]
            return self._send(200, build_xmltv(tid), "application/xml; charset=utf-8")
        if path == "/api/newcamd/profiles":
            profiles = [{"id": "default", "name": "Predeterminado", **STATE["newcamd"]}] + [
                {**config, "id": identity} for identity, config in STATE["profiles"].items()]
            return self._send(200, [{k: v for k, v in profile.items() if k not in ("pass", "deskey")}
                                    for profile in profiles])
        if path.startswith("/api/newcamd/profiles/"):
            identity = path.rsplit("/", 1)[-1]
            try:
                return self._send(200, {**profile_config(identity), "id": identity})
            except StreamError as error:
                return self._send(404, {"error": str(error)})
        if path == "/api/newcamd":
            return self._send(200, STATE["newcamd"])
        if path == "/api/events":
            return self._live_events()
        if path == "/api/stream/log":
            return self._send(410, {"error": "No se guardan registros; utiliza Eventos en vivo"})
        if path == "/api/stream/status":
            return self._send(200, stream_status())
        if path == "/api/playlist.m3u8":
            base = qs.get("base_url", [None])[0]
            tid = qs.get("transponder", [None])[0]
            profile = qs.get("profile", ["pass"])[0]
            if not base:
                host = self.headers.get("Host", "127.0.0.1:8081")
                base = "http://%s/stream/channel/{channel_id}" % host
            if profile != "pass":
                return self._send(400, {"error": "Solo se admite el perfil pass (TS sin transcodificar)"})
            return self._send(200, build_m3u8(base, tid, profile), "application/vnd.apple.mpegurl")
        for prefix in ("/stream/channel/", "/stream/channelid/"):
            if path.startswith(prefix):
                if qs.get("profile", ["pass"])[0] != "pass":
                    return self._send(400, {"error": "Solo se admite profile=pass"})
                return self._stream_channel(urllib.parse.unquote(path[len(prefix):]))
        return self._send(404, {"error": "not found"})

    def _live_events(self):
        try:
            subscription = _streams.events.subscribe()
        except ValueError as error:
            return self._send(503, {"error": str(error)})
        try:
            self.connection.settimeout(5)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            while not subscription.closed:
                batch = subscription.read(timeout=1)
                if not batch:
                    self.wfile.write(b": keepalive\n\n")
                for event in batch:
                    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            subscription.close()
            self.close_connection = True

    def _stream_channel(self, identity):
        session = None
        try:
            tp, sid = resolve_channel(identity)
            # A player may open the next channel just before the old TCP
            # handler observes its disconnect. Allow cleanup, never evict it.
            for attempt in range(6):
                try:
                    session = _session_start(tp, sid=sid)
                    break
                except StreamError as e:
                    if e.status != 409 or attempt == 5:
                        raise
                    time.sleep(0.1)
            cursor = session.cursor()
        except StreamError as e:
            return self._send(e.status, {"error": str(e)})
        except (ValueError, TypeError, KeyError):
            return self._send(400, {"error": "Configuración del canal inválida"})
        try:
            self._relay_session(session, cursor)
        finally:
            _streams.release(session)

    def _relay_session(self, session, cursor):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.connection.settimeout(5)
        try:
            while True:
                cursor, data = session.read(cursor)
                self.wfile.write(data)
        except (OSError, StreamError):
            pass

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            return self._send(403, {"ok": False, "error": "Origen no permitido"})
        try:
            if not 0 <= int(self.headers.get("Content-Length", "0")) <= 65536:
                return self._send(413, {"ok": False, "error": "Solicitud demasiado grande"})
        except ValueError:
            return self._send(400, {"ok": False, "error": "Longitud inválida"})
        u = urllib.parse.urlparse(self.path)
        path = u.path
        body = self._read_body()
        if not isinstance(body, dict):
            return self._send(400, {"ok": False, "error": "Se requiere un objeto JSON"})

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
                STATE["channels"] = {key: value for key, value in STATE["channels"].items() if not key.startswith(tid + ":")}
                _save("transponders"); _save("channels")
            for session in _streams.status():
                if session["channel_id"].startswith(tid + ":"):
                    _streams.stop_channel(session["channel_id"])
            return self._send(200, {"ok": True})

        if path.startswith("/api/channels/") and path.endswith("/edit"):
            identity = urllib.parse.unquote(path[len("/api/channels/"):-len("/edit")])
            try:
                tp, sid = resolve_channel(identity)
                identity = channel_id(tp, sid)
                allowed = {"name", "group", "number", "logo", "enabled", "newcamd_profile"}
                if set(body) - allowed:
                    raise ValueError("Campo de canal no admitido; el SID no se modifica")
                settings = dict(STATE["channels"].get(identity, {}))
                for field in ("name", "group", "logo", "newcamd_profile"):
                    if field in body:
                        if not isinstance(body[field], str) or len(body[field]) > (2048 if field == "logo" else 120):
                            raise ValueError("Campo inválido: " + field)
                        settings[field] = body[field].strip()
                if "name" in settings and not settings["name"]:
                    raise ValueError("Nombre requerido")
                if "enabled" in body:
                    if not isinstance(body["enabled"], bool):
                        raise ValueError("Habilitado debe ser booleano")
                    settings["enabled"] = body["enabled"]
                if "number" in body:
                    number = body["number"]
                    if number not in (None, "") and (isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 99999):
                        raise ValueError("Número de canal inválido (1–99999)")
                    settings["number"] = number or None
                if settings.get("logo"):
                    url = urllib.parse.urlsplit(settings["logo"])
                    if url.scheme not in ("http", "https") or not url.netloc:
                        raise ValueError("Logo debe ser URL http/https")
                profile_config(settings.get("newcamd_profile", "default"))
                with _lock:
                    STATE["channels"][identity] = settings
                    _save("channels")
                _streams.stop_channel(identity)
                return self._send(200, {"ok": True, "reopen_required": True})
            except StreamError as error:
                return self._send(error.status, {"ok": False, "error": str(error)})
            except (ValueError, TypeError) as error:
                return self._send(400, {"ok": False, "error": str(error)})

        if path == "/api/newcamd/profiles":
            identity = body.get("id") or uuid.uuid4().hex[:12]
            try:
                if not isinstance(identity, str) or not identity.isalnum() or len(identity) > 32:
                    raise ValueError("Identificador de perfil inválido")
                previous = profile_config(identity) if identity == "default" or identity in STATE["profiles"] else None
                config = validate_profile(body, previous)
                if not config.get("name"):
                    config["name"] = "Predeterminado" if identity == "default" else "Newcamd"
                with _lock:
                    if identity == "default":
                        STATE["newcamd"] = config; _save("newcamd")
                    else:
                        STATE["profiles"][identity] = config; _save("profiles")
                stop_profile_channels(identity)
                return self._send(200, {"ok": True, "id": identity, "reopen_required": True})
            except (ValueError, TypeError) as error:
                return self._send(400, {"ok": False, "error": str(error)})

        if path.startswith("/api/newcamd/profiles/") and path.endswith("/delete"):
            identity = path[len("/api/newcamd/profiles/"):-len("/delete")]
            if identity == "default" or any(s.get("newcamd_profile", "default") == identity for s in STATE["channels"].values()):
                return self._send(409, {"ok": False, "error": "Perfil predeterminado o asignado; reasigna sus canales primero"})
            with _lock:
                if identity not in STATE["profiles"]:
                    return self._send(404, {"ok": False, "error": "Perfil no encontrado"})
                del STATE["profiles"][identity]; _save("profiles")
            return self._send(200, {"ok": True})

        if path.startswith("/api/channels/") and path.endswith("/delete"):
            identity = urllib.parse.unquote(path[len("/api/channels/"):-len("/delete")])
            try:
                tp, sid = resolve_channel(identity)
                identity = channel_id(tp, sid)
            except StreamError as error:
                return self._send(error.status, {"ok": False, "error": str(error)})
            with _lock:
                tp["scan"]["services"] = [s for s in tp["scan"].get("services", []) if int(s["sid"]) != sid]
                STATE["channels"].pop(identity, None)
                _save_transponder(tp); _save("channels")
            _streams.stop_channel(identity)
            return self._send(200, {"ok": True})

        if path == "/api/scan":
            tid = body.get("id")
            if tid and not _find_tp(tid):
                return self._send(404, {"ok": False, "error": "Transponder no encontrado"})
            tp = _find_tp(tid) or _norm_tp(body)
            result = run_scan(tp)
            if result.get("ok") and tid:
                with _lock:
                    t = _find_tp(tid)
                    if t:
                        t["scan"] = result
                        _save_transponder(t)
            return self._send(200, result)

        if path == "/api/newcamd/test":
            try:
                nc = profile_config(body.get("id", "default"))
            except StreamError as error:
                return self._send(400, {"ok": False, "error": str(error)})
            try:
                with socket.create_connection((nc["host"], int(nc["port"])), timeout=3):
                    pass
                return self._send(200, {"ok": True, "message": "TCP accesible. No valida login, tarjeta ni descifrado."})
            except (OSError, ValueError) as e:
                return self._send(200, {"ok": False, "error": "Conexión TCP: " + str(e)})

        if path == "/api/newcamd":
            try:
                nc = validate_profile(body, STATE["newcamd"])
            except (ValueError, TypeError) as error:
                return self._send(400, {"ok": False, "error": str(error)})
            with _lock:
                STATE["newcamd"] = nc
                _save("newcamd")
            stop_profile_channels("default")
            return self._send(200, {"ok": True, "newcamd": nc})

        if path == "/api/stream/start":
            udp = body.get("udp") or []
            if isinstance(udp, str):
                udp = [udp]
            tp = _find_tp(body.get("id")) or body.get("transponder") or body
            if body.get("id") and not _find_tp(body["id"]):
                return self._send(404, {"ok": False, "error": "Transponder no encontrado"})
            return self._send(200, stream_start(tp, body.get("newcamd"), udp, body.get("http_port"), body.get("sid")))
        if path == "/api/stream/stop":
            if body.get("channel_id"):
                if not _streams.stop_channel(str(body["channel_id"])):
                    return self._send(404, {"ok": False, "error": "Sesión no encontrada"})
                return self._send(200, {"ok": True})
            return self._send(200, stream_stop())

        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        # No persistent channel/access event log (including systemd stderr).
        pass


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
        stream_stop()
        srv.server_close()


if __name__ == "__main__":
    main()
