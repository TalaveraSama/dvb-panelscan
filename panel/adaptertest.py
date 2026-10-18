import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=30).read())

def post(p, b):
    req = urllib.request.Request(BASE + p, data=json.dumps(b).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

print("antes:", [(t["name"], t["adapter"]) for t in get("/api/transponders")])
r = post("/api/transponders", {"name": "TEST-ad1", "type": "dvbs2", "freq_mhz": 11836,
                               "lnb_lo": 9750, "symbol_rate": 27500000, "pol": "H",
                               "adapter": 1, "timeout": 8})
print("add ad1 ok:", r.get("ok"), "id:", r.get("transponder", {}).get("id"))
print("ahora:", [(t["name"], t["adapter"]) for t in get("/api/transponders")])
for t in get("/api/transponders"):
    if t["adapter"] == 1:
        post("/api/transponders/%s/delete" % t["id"], {})
print("final:", [(t["name"], t["adapter"]) for t in get("/api/transponders")])
