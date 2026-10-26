"""CI smoke: authenticate without persisting a plaintext password to disk/logs."""

import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

root = "http://127.0.0.1:8081"
password = os.environ["SMOKE_PASSWORD"]
jar = http.cookiejar.CookieJar()
http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, body=None, csrf=""):
    request = urllib.request.Request(
        root + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
    )
    with http.open(request, timeout=5) as response:
        return json.load(response)


call("/api/auth/login", {"username": "ci-admin", "password": password})
session = call("/api/auth/session")
assert session["username"] == "ci-admin"
if sys.argv[1] == "configure":
    assert call("/api/status")["version"] == Path("VERSION").read_text().strip()
    call("/api/epg/settings", {"days": 2, "utc_offset_minutes": -360}, session["csrf"])
else:
    settings = call("/api/epg/settings")
    assert settings["days"] == 2 and settings["utc_offset_minutes"] == -360
diagnostic = call("/api/diag")
assert diagnostic["network_probe"] is False and diagnostic["selftest_available"]
assert diagnostic["brokers"] == [] and diagnostic["sessions"] == []
assert call("/api/diag/selftest", {}, session["csrf"]) == {"ok": True, "csa": True, "network_probe": False}
call("/api/auth/logout", {}, session["csrf"])
try:
    call("/api/status")
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("Logout must revoke session")
print("Authenticated package smoke passed:", sys.argv[1])
