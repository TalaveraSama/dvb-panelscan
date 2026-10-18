import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=30).read())

tps = get("/api/transponders")
print("transponders:", len(tps))
for t in tps:
    sc = t.get("scan")
    print("  ", t.get("name"), "adapter", t.get("adapter"),
          "scan", bool(sc), "channels", len(sc.get("services", [])) if sc else 0)
