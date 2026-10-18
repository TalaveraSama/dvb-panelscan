#!/bin/bash
# Deshabilita temporalmente el reader de OSCam (sin smartcard) para detener el crash loop.
set -e
cp -n /etc/oscam/oscam.server /etc/oscam/oscam.server.orig
cat > /etc/oscam/oscam.server <<'EOF'
# [reader] deshabilitado temporalmente (sin smartcard conectado)
# backup: /etc/oscam/oscam.server.orig
EOF
systemctl restart oscam
sleep 2
systemctl is-active oscam
