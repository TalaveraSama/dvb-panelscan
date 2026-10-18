import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=30).read())

def post(p, b):
    req = urllib.request.Request(BASE + p, data=json.dumps(b).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read())

# 1) borrar todos los transponders (con sus canales)
tps = get("/api/transponders")
for t in tps:
    post("/api/transponders/%s/delete" % t["id"], {})
print("tras borrar: transponders=", get("/api/transponders"), " epg_tps=", len(get("/api/epg")))

# 2) re-agregar y escanear solo el primer transponder
r = post("/api/transponders", {"name": "70W AMX", "type": "dvbs2", "freq_mhz": 10970,
                               "lnb_lo": 9750, "symbol_rate": 27900000, "pol": "V",
                               "adapter": 0, "timeout": 12})
tid = r["transponder"]["id"]
print("agregado id=", tid)
r = post("/api/scan", {"id": tid})
print("scan ok=", r.get("ok"), " red=", r.get("network"), " canales=", len(r.get("services", [])))
