#!/usr/bin/env python3
"""
Panel administrativo del escáner DVB/TBS (dvb2m3u8).

Servidor web con la biblioteca estándar de Python (sin dependencias) que:
  * sirve el panel (HTML/JS/CSS) desde static/
  * expone una API JSON:
      GET  /api/status         -> plataforma, adaptadores, modo demo
      POST /api/scan           -> lanza un escaneo (dvb2m3u8 en Linux)
      GET  /api/channels       -> canales del último escaneo
      GET  /api/playlist.m3u8  -> exporta el M3U8
  * en Linux engancha el binario dvb2m3u8 real; en Windows (o sin binario)
    sirve datos de demostración para poder ver el panel.

Uso:
    python server.py [--port 8080] [--host 127.0.0.1] [--dvb2m3u8 /ruta/al/binario]
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ---------------------------------------------------------------------------
# Estado global (protegido con un lock)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_last_scan = None          # dict con el resultado del último escaneo (o None)
_last_scan_time = None

DEMO_NETWORK = "Red de demostración"


def _demo_services():
    """Canales de ejemplo (solo para ver el panel sin hardware DVB)."""
    return [
        {"sid": 1, "name": "La 1", "provider": "TVE", "type": 1,
         "now": "Telediario", "next": "La Promesa"},
        {"sid": 2, "name": "La 2", "provider": "TVE", "type": 1,
         "now": "Documental", "next": "Cine de la tarde"},
        {"sid": 3, "name": "24h", "provider": "TVE", "type": 1,
         "now": "Informativos 24h", "next": "Agenda informativa"},
        {"sid": 4, "name": "Antena 3", "provider": "Atresmedia", "type": 1,
         "now": "Noticias", "next": "El Hormiguero"},
        {"sid": 5, "name": "laSexta", "provider": "Atresmedia", "type": 1,
         "now": "Al Rojo Vivo", "next": "laSexta Noticias"},
        {"sid": 6, "name": "Telecinco", "provider": "Mediaset", "type": 1,
         "now": "Informativos Telecinco", "next": "Got Talent"},
        {"sid": 7, "name": "Cuatro", "provider": "Mediaset", "type": 1,
         "now": "Deportes Cuatro", "next": "Cine"},
        {"sid": 8, "name": "RNE", "provider": "TVE", "type": 2,
         "now": "Boletín informativo", "next": "Magacín matinal"},
    ]


# ---------------------------------------------------------------------------
# Detección del entorno y del binario
# ---------------------------------------------------------------------------
def is_linux():
    return platform.system() == "Linux"


def find_dvb2m3u8(explicit=None):
    if explicit:
        if os.path.isfile(explicit):
            return os.path.abspath(explicit)
        return None
    # 1) junto al proyecto (../dvb2m3u8/dvb2m3u8)
    sibling = os.path.normpath(os.path.join(BASE_DIR, "..", "dvb2m3u8", "dvb2m3u8"))
    if os.path.isfile(sibling):
        return sibling
    # 2) en el PATH
    return shutil.which("dvb2m3u8")


def list_adapters():
    """Enumera /dev/dvb/adapterN y sus frontends (vacío si no hay DVB)."""
    adapters = []
    for path in sorted(glob.glob("/dev/dvb/adapter*")):
        name = os.path.basename(path)
        num = name.replace("adapter", "")
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


def _build_cmd(binary, p):
    cmd = [binary, "-t", p.get("type", "dvbt"), "--json"]
    if p.get("freq"):
        cmd += ["--freq", str(int(p["freq"]))]
    elif p.get("freq_mhz"):
        cmd += ["--freq-mhz", str(float(p["freq_mhz"]))]
    # LNB LO: convierte frecuencia de satélite a FI (0 = la freq ya es FI)
    lnb_lo = p.get("lnb_lo")
    if lnb_lo is None:
        lnb_lo = 9750
    cmd += ["--lnb-lo", str(float(lnb_lo))]
    if p.get("symbol_rate"):
        cmd += ["--symbol-rate", str(int(p["symbol_rate"]))]
    if p.get("pol"):
        cmd += ["--pol", str(p["pol"])]
    if p.get("adapter"):
        cmd += ["-a", str(int(p["adapter"]))]
    timeout = p.get("timeout") or 15
    cmd += ["-T", str(int(timeout))]
    cmd += ["-v"]  # para capturar la línea "señal: N" del stderr
    return cmd


def _parse_signal(stderr_text):
    for line in stderr_text.splitlines():
        if "señal:" in line.lower() or "senal:" in line.lower():
            try:
                return int(line.split(":")[-1].strip())
            except ValueError:
                pass
    return None


def _run_real_scan(binary, p):
    cmd = _build_cmd(binary, p)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=int(p.get("timeout") or 15) + 10)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout del escaneo"}
    except OSError as e:
        return {"ok": False, "error": str(e)}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "error desconocido").strip()}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "salida JSON inválida de dvb2m3u8"}

    data["ok"] = True
    data["demo"] = False
    data["signal"] = _parse_signal(proc.stderr)
    data["command"] = " ".join(cmd)
    return data


def _run_demo_scan(p):
    # pequeña pausa para que el spinner sea visible en el panel
    time.sleep(0.6)
    freq = int(p.get("freq") or (float(p.get("freq_mhz") or 674) * 1_000_000))
    return {
        "ok": True,
        "demo": True,
        "adapter": int(p.get("adapter") or 0),
        "frequency": freq,
        "network": DEMO_NETWORK,
        "signal": 43210,
        "services": _demo_services(),
    }


def perform_scan(p):
    global _last_scan, _last_scan_time
    p = {k: v for k, v in (p or {}).items() if v not in (None, "")}
    if p.get("type") not in VALID_TYPES:
        p["type"] = "dvbt"

    binary = find_dvb2m3u8(_cfg.get("dvb2m3u8"))
    if is_linux() and binary:
        result = _run_real_scan(binary, p)
    else:
        result = _run_demo_scan(p)

    if result.get("ok"):
        with _lock:
            _last_scan = result
            _last_scan_time = time.time()
    return result


def current_channels():
    with _lock:
        if _last_scan and _last_scan.get("services"):
            return _last_scan
    return {"demo": True, "network": DEMO_NETWORK,
            "services": _demo_services(), "signal": 43210}


def build_m3u8(scan, base_url=None):
    lines = ["#EXTM3U"]
    for s in scan.get("services", []):
        name = s.get("name") or f"Service {s.get('sid')}"
        group = s.get("provider") or scan.get("network") or "DVB"
        radio = ' radio="true"' if s.get("type") in (2, 0x0A) else ""
        lines.append(f'#EXTINF:-1 tvg-id="{s.get("sid")}" tvg-name="{name}" '
                     f'group-title="{group}"{radio},{name}')
        if base_url:
            lines.append(f"{base_url}{s.get('sid')}")
        else:
            lines.append(f"dvb://adapter{scan.get('adapter', 0)}/{scan.get('frequency', 0)}/{s.get('sid')}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------
_cfg = {"dvb2m3u8": None}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".m3u8": "application/vnd.apple.mpegurl",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "DVBAdmin/1.0"

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
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            return self._send_file(os.path.join(STATIC_DIR, rel))
        if path == "/api/status":
            binary = find_dvb2m3u8(_cfg.get("dvb2m3u8"))
            return self._send(200, {
                "platform": platform.system(),
                "linux": is_linux(),
                "demo": not (is_linux() and binary),
                "dvb2m3u8": binary,
                "adapters": list_adapters() if is_linux() else [],
                "last_scan_time": _last_scan_time,
            })
        if path == "/api/channels":
            return self._send(200, current_channels())
        if path == "/api/playlist.m3u8":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            base_url = qs.get("base_url", [None])[0]
            body = build_m3u8(current_channels(), base_url)
            return self._send(200, body, "application/vnd.apple.mpegurl")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/scan":
            return self._send(200, perform_scan(self._read_body()))
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[panel] %s\n" % (fmt % args))


def main():
    ap = argparse.ArgumentParser(description="Panel administrativo DVB/TBS")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dvb2m3u8", default=None, help="ruta al binario dvb2m3u8")
    args = ap.parse_args()

    _cfg["dvb2m3u8"] = args.dvb2m3u8

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    demo = not (is_linux() and find_dvb2m3u8(args.dvb2m3u8))
    print(f"Panel DVB/TBS en http://{args.host}:{args.port}")
    print(f"  modo: {'DEMO (sin hardware/binario)' if demo else 'real (Linux + dvb2m3u8)'}")
    print("  Ctrl+C para salir")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
