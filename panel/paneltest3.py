import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(p, host=None):
    req = urllib.request.Request(BASE + p)
    if host:
        req.add_header("Host", host)
    return urllib.request.urlopen(req, timeout=30).read().decode()

def post(p, body):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

tps = json.loads(get("/api/transponders"))
print("TPS_INI:", [(t.get("id"), t.get("name")) for t in tps])

tid = None
if tps:
    tid = tps[0]["id"]
else:
    r = post("/api/transponders", {"name": "70W AMX", "type": "dvbs2", "freq_mhz": 10970,
                                   "lnb_lo": 9750, "symbol_rate": 27900000, "pol": "V",
                                   "adapter": 0, "timeout": 12})
    print("ADD:", r.get("ok"), r.get("transponder", {}).get("id"))
    tid = r["transponder"]["id"]

r = post("/api/scan", {"id": tid})
print("SCAN ok:", r.get("ok"), "network:", r.get("network"), "n:", len(r.get("services", [])))

epg = json.loads(get("/api/epg"))
print("EPG tps:", len(epg), "total:", sum(len(e.get("services", [])) for e in epg))

m3u = get("/api/playlist.m3u8", host="192.168.1.22:8081")
lines = m3u.splitlines()
print("M3U8 lines:", len(lines))
for l in lines[:6]:
    print("  " + l)
