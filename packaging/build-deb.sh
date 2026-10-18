#!/bin/sh
set -eu
VERSION=${1:-0.5.0}
ARCH=$(dpkg --print-architecture)
ROOT=$(pwd)
DEST="$ROOT/dist/dvb-panelscan_${VERSION}_${ARCH}"
make -C dvb2m3u8
make -C tbsstream
mkdir -p "$DEST/DEBIAN" "$DEST/usr/bin" "$DEST/usr/lib/dvb-panelscan/static" "$DEST/lib/systemd/system" "$DEST/usr/share/doc/dvb-panelscan"
install -m755 dvb2m3u8/dvb2m3u8 tbsstream/tbsstream "$DEST/usr/bin/"
install -m644 panel/server.py panel/streaming.py "$DEST/usr/lib/dvb-panelscan/"
install -m644 panel/static/* "$DEST/usr/lib/dvb-panelscan/static/"
install -m644 packaging/dvb-panel.service "$DEST/lib/systemd/system/"
install -m644 README.md RELEASE_NOTES.md "$DEST/usr/share/doc/dvb-panelscan/"
cat > "$DEST/DEBIAN/control" <<CONTROL
Package: dvb-panelscan
Version: $VERSION
Architecture: $ARCH
Maintainer: TalaveraSama <TalaveraSama@users.noreply.github.com>
Depends: python3, libdvbcsa1, libssl3, libc6 (>= 2.34)
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
chmod 755 "$DEST/DEBIAN/postinst" "$DEST/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$DEST"
