import urllib.request, json

BASE = "http://127.0.0.1:8081"

def get(p):
    return urllib.request.urlopen(BASE + p, timeout=30).read().decode()

def post(p):
    req = urllib.request.Request(BASE + p, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

xml = get("/api/epg.xml")
print("XMLTV head:", xml[:120].replace("\n", " "))
ch = json.loads(get("/api/epg"))
print("canales antes:", sum(len(t["services"]) for t in ch))
print("delete 835:", post("/api/channels/835/delete"))
ch = json.loads(get("/api/epg"))
print("canales despues:", sum(len(t["services"]) for t in ch))
