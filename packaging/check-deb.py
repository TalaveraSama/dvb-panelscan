#!/usr/bin/env python3
"""Validate the actual Debian artifact before installing or publishing it."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

package = Path(sys.argv[1]).resolve()
version = Path("VERSION").read_text().strip()
assert subprocess.check_output(["dpkg-deb", "-f", str(package), "Version"], text=True).strip() == version
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    subprocess.run(["dpkg-deb", "-R", str(package), str(root)], check=True)
    lib = root / "usr/lib/dvb-panelscan"
    for name in ("iptv.py", "astra_import.py", "auth.py", "server.py", "streaming.py", "health.py", "events.py", "epg.py", "VERSION"):
        assert (lib / name).is_file(), name
    assert (lib / "VERSION").read_text().strip() == version
    for name in ("logos.json", "LOGOS-SOURCES.txt", "index.html", "app.js", "login.html", "login.js", "style.css"):
        assert (lib / "static" / name).is_file(), name
    assert len(list((lib / "static").glob("logo-*.png"))) == 12
    for name in ("EPG.md", "CHANNELS_AND_PROFILES.md", "LIVE_EVENTS.md", "RELEASING.md", "SECURITY.md", "ASTRA_IMPORT.md", "IPTV_FLUSSONIC.md", "BUILD_INFO.txt"):
        assert (root / "usr/share/doc/dvb-panelscan" / name).is_file(), name
    assert not (root / "var").exists(), "Runtime state must never be bundled"
    files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "DEBIAN" not in p.relative_to(root).parts}
    checksums = {}
    for line in (root / "DEBIAN/md5sums").read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        assert hashlib.md5((root / relative).read_bytes()).hexdigest() == digest, relative
        checksums[relative] = digest
    assert files == checksums.keys(), "Checksum manifest must cover all installed files"
    assert not any(p.endswith((".log", ".pyc", "epg.json", "channels.json", "newcamd.json", "auth.json")) for p in files)
    for name in ("postinst", "prerm", "postrm"):
        subprocess.run(["sh", "-n", str(root / "DEBIAN" / name)], check=True)
print("Debian package validated:", package.name, "version", version)
