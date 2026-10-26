#!/usr/bin/env python3
"""Read-only Linux CA topology snapshot. No credentials, addresses or log files."""
import os
from pathlib import Path
import re
import sys

STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "06": "TIME_WAIT", "08": "CLOSE_WAIT", "0A": "LISTEN"}


def role(args):
    if "--ca-broker" in args:
        return "ca-broker"
    if "--mux-only" in args:
        return "mux-capture"
    if "--cam-socket" in args:
        return "service-local-ca"
    return "service-direct-ca"


def socket_table(root):
    result = {}
    for protocol in ("tcp", "tcp6"):
        try:
            lines = (root / "net" / protocol).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) >= 10:
                result[fields[9]] = (STATES.get(fields[3], "OTHER"), fields[2])
    return result


def snapshot(proc=Path("/proc")):
    peers, rows, inaccessible = {}, [], 0
    for entry in sorted((p for p in proc.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            args = [a.decode("utf-8", "replace") for a in args if a]
            exe = os.readlink(entry / "exe").removesuffix(" (deleted)")
            is_engine = Path(exe).name == "tbsstream"
            is_panel = any(a.endswith("/dvb-panelscan/server.py") for a in args)
            is_other = Path(exe).name in ("astra", "tvheadend")
            if not (is_engine or is_panel or is_other):
                continue
            status = (entry / "status").read_text()
            match = re.search(r"^PPid:\s+(\d+)", status, re.M)
            item = {"pid": int(entry.name), "ppid": int(match[1]) if match else 0,
                    "role": role(args) if is_engine else ("panel" if is_panel else "other-headend"), "tcp": []}
            # The process network namespace matters (containers may differ).
            table = socket_table(entry)
            for fd in (entry / "fd").iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                match = re.fullmatch(r"socket:\[(\d+)\]", link)
                if not match or match[1] not in table:
                    continue
                state, remote = table[match[1]]
                label = peers.setdefault(remote, "peer-%d" % (len(peers) + 1))
                item["tcp"].append((state, label))
            rows.append(item)
        except (OSError, ValueError):
            inaccessible += 1
    return rows, inaccessible


def main():
    if os.geteuid() != 0:
        print("Ejecuta con sudo para ver sockets de todos los motores; sin permisos el resultado es incompleto.")
    version = Path("/usr/lib/dvb-panelscan/VERSION")
    try:
        value = version.read_text().strip()
        print("Version:", value if re.fullmatch(r"\d+\.\d+\.\d+", value) else "no verificable")
    except OSError:
        print("Version: no disponible")
    rows, skipped = snapshot()
    print("PID     PPID    ROL                  TCP (destinos anonimizados)")
    for item in rows:
        connections = ", ".join("%s:%s" % c for c in item["tcp"]) or "ninguno"
        print("%-7d %-7d %-20s %s" % (item["pid"], item["ppid"], item["role"], connections))
    established = [(p, peer) for p in rows for state, peer in p["tcp"] if state == "ESTABLISHED" and p["role"] in ("ca-broker", "service-direct-ca", "service-local-ca", "mux-capture")]
    print("Sockets TCP establecidos en motores:", len(established))
    print("Procesos relevantes:", len(rows), "| entradas no inspeccionables:", skipped)
    print("Un worker local usa socket Unix, no TCP a Newcamd. Un broker puede tener un TCP upstream.")
    print("other-headend puede mantener otra conexion: no se identifica su cuenta ni se detiene automaticamente.")
    print("peer-N agrupa destinos exactos; no identifica cuentas ni distingue aliases DNS.")
    print("Es una instantanea: no prueba login/CW ni identifica reconexiones historicas.")
    print("No se han mostrado argumentos, usuarios, claves, IPs, puertos ni URLs. No se guardan archivos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
