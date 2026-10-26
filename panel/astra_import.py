"""Validated Astra migration. Never import Astra users, auth, outputs or raw files."""
import collections
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import urllib.parse

KEYS = ("profiles", "transponders", "channels")


def atomic_json(path, value):
    path = Path(path)
    temporary = str(path) + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def recover(directory):
    journal = Path(directory) / "astra-recovery.json"
    if not journal.exists():
        return
    original = json.loads(journal.read_text(encoding="utf-8"))
    for name in KEYS:
        atomic_json(Path(directory) / (name + ".json"), original[name])
    journal.unlink()


def apply(directory, state, planned):
    original = {key: copy.deepcopy(state[key]) for key in KEYS}
    backups = Path(directory) / "import-backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backups, 0o700)
    backup = backups / ("astra-" + uuid.uuid4().hex + ".json")
    atomic_json(backup, original)
    atomic_json(Path(directory) / "astra-recovery.json", original)
    try:
        for name in KEYS:
            atomic_json(Path(directory) / (name + ".json"), planned[name])
        (Path(directory) / "astra-recovery.json").unlink()
    except OSError:
        recover(directory)
        raise
    for name in KEYS:
        state[name] = planned[name]
    return backup.name


def build(state, options, validate_tp, validate_profile):
    source = options.get("config")
    if not isinstance(source, dict) or not isinstance(source.get("dvb_tune"), list):
        raise ValueError("Archivo Astra inválido: falta dvb_tune")
    tunes, streams = source["dvb_tune"], source.get("make_stream", [])
    if not isinstance(streams, list) or len(tunes) > 256 or len(streams) > 10000:
        raise ValueError("Límite: 256 transpondedores y 10000 entradas")
    bindings = options.get("adapters", {})
    profile_map = options.get("profiles", {})
    if not isinstance(bindings, dict) or not isinstance(profile_map, dict):
        raise ValueError("Mapas de adaptadores/perfiles inválidos")
    result = {key: copy.deepcopy(state[key]) for key in KEYS}
    warnings = ["No se importan usuarios, hashes, permisos, salidas HTTP/UDP/HLS ni ajustes globales de Astra."]
    selected, seen_ids, seen_adapters = {}, set(), set()
    summary = {"transponders": [], "channels": 0, "profiles_created": 0, "profiles_updated": 0, "warnings": warnings}
    for row in tunes:
        if not isinstance(row, dict):
            raise ValueError("Entrada dvb_tune inválida")
        sid = row.get("id")
        if not isinstance(sid, str) or not 1 <= len(sid) <= 80 or sid in seen_ids:
            raise ValueError("ID Astra ausente o duplicado")
        seen_ids.add(sid)
        if row.get("enable", True) is False:
            warnings.append("Se omite transpondedor deshabilitado: " + sid)
            continue
        mac = str(row.get("mac", "")).lower()
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError("MAC ausente/inválida: " + sid)
        adapter = bindings.get(mac)
        if isinstance(adapter, bool) or not isinstance(adapter, int) or not 0 <= adapter <= 255:
            raise ValueError("Selecciona adaptador Linux para MAC " + mac)
        if adapter in seen_adapters:
            raise ValueError("Dos transpondedores habilitados asignados al mismo adaptador")
        seen_adapters.add(adapter)
        if row.get("type") not in ("S", "S2") or row.get("polarization") not in ("V", "H", "L", "R"):
            raise ValueError("Tipo/polarización no soportados: " + sid)
        if row["polarization"] in ("L", "R"):
            warnings.append(sid + ": polarización circular " + row["polarization"] + "; NO se convierte a V/H automáticamente.")
        settings = {"name": row.get("name") or sid, "type": "dvbs2" if row["type"] == "S2" else "dvbs",
                    "freq_mhz": float(row.get("frequency", 0)), "pol": row["polarization"],
                    "symbol_rate": int(float(row.get("symbolrate", 0)) * 1000),
                    "adapter": adapter, "lnb_mode": "universal", "lnb_lo": 9750, "timeout": 30}
        if not 0 < settings["symbol_rate"] <= 100000000:
            raise ValueError("Symbol rate inválido: " + sid)
        settings = validate_tp(settings)
        matches = [t for t in result["transponders"] if t.get("astra_id") == sid and t.get("source_mac") == mac]
        if not matches:
            matches = [t for t in result["transponders"] if t.get("freq_mhz") == settings["freq_mhz"] and t.get("pol") == settings["pol"] and t.get("adapter") == adapter]
        if len(matches) > 1:
            raise ValueError("Coincidencia ambigua para " + sid)
        tp = matches[0] if matches else {"id": uuid.uuid4().hex[:8], "scan": None}
        if matches:
            settings["newcamd_profile"] = tp.get("newcamd_profile", "default")
        tp.update(settings, astra_id=sid, source_mac=mac)
        if not matches:
            result["transponders"].append(tp)
        if not matches and any(t is not tp and t.get("adapter") == adapter for t in result["transponders"]):
            warnings.append("Adaptador %d: se conservan otros TP locales; revisar duplicados antes de escanear" % adapter)
        selected[sid] = tp
        summary["transponders"].append({"astra_id": sid, "name": settings["name"], "adapter": adapter,
                                         "frequency": settings["freq_mhz"], "pol": settings["pol"], "action": "actualizar" if matches else "crear"})
    if not selected:
        raise ValueError("No hay transpondedores habilitados para importar")
    imported = {}
    for stream in streams:
        if not isinstance(stream, dict):
            raise ValueError("Entrada make_stream inválida")
        inputs = stream.get("input", [])
        if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], str):
            warnings.append("Se omite entrada sin fuente DVB única")
            continue
        url = urllib.parse.urlsplit(inputs[0])
        if url.scheme != "dvb" or url.netloc not in selected:
            continue
        params = urllib.parse.parse_qs(url.fragment)
        if len(params.get("pnr", [])) != 1 or len(params.get("cam", [])) > 1:
            raise ValueError("PNR/cam ambiguo en entrada DVB")
        service_id = int(params["pnr"][0])
        if not 1 <= service_id <= 65535:
            raise ValueError("SID/PNR fuera de rango")
        key = (url.netloc, service_id)
        value = {"name": str(stream.get("name") or "Servicio " + str(service_id))[:120],
                 "enabled": stream.get("enable", True) is not False, "cam": params.get("cam", [""])[0]}
        previous = imported.get(key)
        if previous:
            if previous["enabled"] and value["enabled"] and previous != value:
                raise ValueError("Dos canales activos incompatibles con el mismo TP:SID")
            warnings.append("Entrada TP:SID duplicada; se prefiere la habilitada")
            if previous["enabled"]:
                continue
        imported[key] = value
    needed = {v["cam"] for v in imported.values() if v["cam"]}
    softcams = source.get("softcam", [])
    if not isinstance(softcams, list):
        raise ValueError("Lista softcam inválida")
    for cam in sorted(needed):
        target = profile_map.get(cam)
        if target:
            if target != "default" and target not in result["profiles"]:
                raise ValueError("Perfil destino inexistente para cam " + cam)
            continue
        if options.get("import_profiles") is not True:
            raise ValueError("Asigna un perfil DVB Panel a cam " + cam + " o activa importar cuentas")
        candidates = [p for p in softcams if isinstance(p, dict) and p.get("id") == cam]
        if len(candidates) != 1 or candidates[0].get("type") != "newcamd":
            raise ValueError("Softcam Newcamd ausente o ambigua: " + cam)
        original = candidates[0]
        fields = {k: original[k] for k in ("name", "host", "port", "user", "pass", "deskey") if k in original}
        fields.setdefault("deskey", original.get("key") or options.get("deskey", ""))
        try:
            account = validate_profile(fields)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Cuenta Newcamd inválida o sin DES key válida: " + cam) from None
        target = "astra-" + hashlib.sha256(cam.encode()).hexdigest()[:16]
        existing = result["profiles"].get(target)
        if existing and existing.get("astra_id") != cam:
            raise ValueError("Colisión con perfil local; asigna el perfil manualmente")
        summary["profiles_updated" if existing else "profiles_created"] += 1
        result["profiles"][target] = dict(account, astra_id=cam)
        profile_map = dict(profile_map, **{cam: target})
    summary["profile_bindings"] = [
        {"astra_cam": cam, "profile": profile_map[cam],
         "name": (result["profiles"].get(profile_map[cam], {}).get("name") or profile_map[cam])}
        for cam in sorted(needed)]
    preferred = collections.defaultdict(collections.Counter)
    for (tid, service_id), value in imported.items():
        tp = selected[tid]
        scan = tp.get("scan")
        if not scan:
            scan = {"services": [], "network": "Importado de Astra; pendiente de escaneo"}
            tp["scan"] = scan
        if not any(int(s["sid"]) == service_id for s in scan.get("services", [])):
            scan.setdefault("services", []).append({"sid": service_id, "name": value["name"]})
        key = tp["id"] + ":" + str(service_id)
        settings = result["channels"].setdefault(key, {})
        settings.update(name=value["name"], enabled=value["enabled"])
        if value["cam"]:
            settings["newcamd_profile"] = profile_map[value["cam"]]
            if value["enabled"]:
                preferred[tid][settings["newcamd_profile"]] += 1
        summary["channels"] += 1
    for tid, counts in preferred.items():
        selected[tid]["newcamd_profile"] = counts.most_common(1)[0][0]
    summary["warnings"] = list(dict.fromkeys(warnings))[:100]
    return result, summary
