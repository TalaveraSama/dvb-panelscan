import urllib.request, json, sys

body = {
    "type": "dvbs2",
    "freq_mhz": 10970,
    "lnb_lo": 9750,
    "symbol_rate": 27900000,
    "pol": "V",
    "adapter": 0,
    "timeout": 8,
}
data = json.dumps(body).encode()
req = urllib.request.Request("http://127.0.0.1:8081/api/scan", data=data,
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=40)
    print(r.read().decode())
except Exception as e:
    print("ERROR", e)
