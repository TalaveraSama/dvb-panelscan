import json

d = json.load(open("/tmp/scan.json"))
print("canales:", len(d["services"]))
print("red:", d.get("network"))
for s in d["services"][:25]:
    print("  ", s["sid"], s["name"], "tipo", s["type"])
