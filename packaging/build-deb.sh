#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
VERSION=$(cat VERSION)
if [ "${1:-$VERSION}" != "$VERSION" ]; then
  echo "La versión debe coincidir con VERSION ($VERSION)" >&2; exit 1
fi
printf '%s\n' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || exit 1
ARCH=$(dpkg --print-architecture)
DEST="$ROOT/dist/dvb-panelscan_${VERSION}_${ARCH}"
make -C dvb2m3u8
make -C tbsstream
# Recreate only this validated package staging directory; never package stale files.
rm -rf "$DEST"
mkdir -p "$DEST/DEBIAN" "$DEST/usr/bin" "$DEST/usr/lib/dvb-panelscan/static" "$DEST/lib/systemd/system" "$DEST/usr/share/doc/dvb-panelscan"
install -m755 dvb2m3u8/dvb2m3u8 tbsstream/tbsstream "$DEST/usr/bin/"
install -m644 VERSION "$DEST/usr/lib/dvb-panelscan/"
install -m644 panel/astra_import.py panel/auth.py panel/server.py panel/streaming.py panel/health.py panel/events.py panel/epg.py "$DEST/usr/lib/dvb-panelscan/"
install -m644 panel/static/* "$DEST/usr/lib/dvb-panelscan/static/"
install -m644 packaging/dvb-panel.service "$DEST/lib/systemd/system/"
install -m644 README.md RELEASE_NOTES.md VERSION "$DEST/usr/share/doc/dvb-panelscan/"
install -m644 docs/*.md "$DEST/usr/share/doc/dvb-panelscan/"
install -m644 panel/static/LOGOS-SOURCES.txt "$DEST/usr/share/doc/dvb-panelscan/"
printf 'Version: %s\nCommit: %s\nTarget: Ubuntu 22.04 %s\n' "$VERSION" "$(git rev-parse HEAD)" "$ARCH" > "$DEST/usr/share/doc/dvb-panelscan/BUILD_INFO.txt"
cat > "$DEST/DEBIAN/control" <<CONTROL
Package: dvb-panelscan
Version: $VERSION
Architecture: $ARCH
Maintainer: TalaveraSama <TalaveraSama@users.noreply.github.com>
Depends: adduser, python3, libdvbcsa1, libssl3, libc6 (>= 2.34)
Section: video
Priority: optional
Description: DVB scanner, Newcamd client and local web dashboard
 Local DVB headend control panel with UDP and HTTP streaming.
CONTROL
cat > "$DEST/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  if ! getent passwd dvb-panel >/dev/null; then
    adduser --system --group --home /var/lib/dvb-panelscan --no-create-home dvb-panel
  fi
  if getent group video >/dev/null; then adduser dvb-panel video; fi
  install -d -m 0700 -o dvb-panel -g dvb-panel /var/lib/dvb-panelscan
  if [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
fi
SCRIPT
cat > "$DEST/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then systemctl stop dvb-panel.service || true; fi
SCRIPT
cat > "$DEST/DEBIAN/postrm" <<'SCRIPT'
#!/bin/sh
set -e
# Runtime configuration and service overrides deliberately survive removal.
if [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
SCRIPT
chmod 755 "$DEST/DEBIAN/postinst" "$DEST/DEBIAN/prerm" "$DEST/DEBIAN/postrm"
(cd "$DEST" && find usr lib -type f -print | LC_ALL=C sort | xargs md5sum) > "$DEST/DEBIAN/md5sums"
SIZE=$(du -sk "$DEST/usr" "$DEST/lib" | awk '{total+=$1} END {print total}')
printf 'Installed-Size: %s\n' "$SIZE" >> "$DEST/DEBIAN/control"
dpkg-deb --root-owner-group --build "$DEST"
