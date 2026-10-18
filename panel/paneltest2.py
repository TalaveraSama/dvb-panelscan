import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=30).read())

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=45).read())

print("STATUS:", get("/api/status"))
print("TPS_INI:", get("/api/transponders"))
r = post("/api/transponders", {"name": "70W AMX", "type": "dvbs2", "freq_mhz": 10970,
                               "lnb_lo": 9750, "symbol_rate": 27900000, "pol": "V",
                               "adapter": 0, "timeout": 12})
print("ADD_TP:", r)
tps = get("/api/transponders")
print("TPS:", [t.get("name") for t in tps])
if tps:
    tid = tps[0]["id"]
    sc = post("/api/scan", {"id": tid})
    print("SCAN_OK:", sc.get("ok"), "network:", sc.get("network"),
          "n:", len(sc.get("services", [])), "err:", sc.get("error"))
    ch = get("/api/channels")
    print("CHANNELS:", len(ch.get("services", [])))
    m3u = urllib.request.urlopen(BASE + "/api/playlist.m3u8", timeout=10).read().decode()
    print("M3U8_HEAD:", m3u[:150].replace("\n", " | "))
print("NEWCAMD:", get("/api/newcamd"))
print("STREAM:", get("/api/stream/status"))
