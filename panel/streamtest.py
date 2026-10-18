import urllib.request, time

t0 = time.time()
try:
    d = urllib.request.urlopen("http://127.0.0.1:8081/stream/channelid/137", timeout=25).read(1880)
    print("len=", len(d), "elapsed=", round(time.time() - t0, 1))
    print("hex=", d[:16].hex())
    print("sync0x47=", d.count(0x47))
except Exception as e:
    print("ERROR", type(e).__name__, e, "elapsed=", round(time.time() - t0, 1))
