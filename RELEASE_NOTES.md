## DVB PanelScan 0.4.1 · Continuidad del streaming

Correcciones de transporte para reducir posibles causas de tirones. No se garantiza eliminar todos los cortes: señal DVB, carga, red y cambios de claves requieren validación en el servidor real.

- Conserva fragmentos TS entre lecturas del DVR; ya no exige tres paquetes completos en cada lectura ni descarta el final incompleto.
- Reenvía el PID de PCR aunque sea distinto de los PID de audio/vídeo.
- HTTP completa las escrituras TCP, incluso si `send()` acepta solo parte del buffer.
- Agrupa hasta siete paquetes disponibles por escritura HTTP y conecta clientes al punto en vivo en lugar de repetir el ring antiguo.
- Limita el tiempo de E/S de clientes HTTP bloqueados.
- Rechaza IDs de transponder inexistentes antes de iniciar streaming.
- Pruebas de lecturas fragmentadas, recuperación de sincronía, PCR separado, ring y entrega TCP, además de las regresiones anteriores con ASan/UBSan.

### Actualizar en Ubuntu amd64

```sh
wget https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.4.1/dvb-panelscan_0.4.1_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install ./dvb-panelscan_0.4.1_amd64.deb
sudo systemctl start dvb-panel
```

Se conservan `/var/lib/dvb-panelscan` y los overrides en `/etc/systemd/system/dvb-panel.service.d/`. El servicio base sigue escuchando solo en loopback; el panel no tiene autenticación.

Compilación, pruebas e instalación/arranque automatizados en Ubuntu 22.04 amd64. Verificar la descarga con `sha256sum -c SHA256SUMS`.

Continúan pendientes el seguimiento preciso del período de claves como Tvheadend, EMM y selección explícita de CAID/proveedor en simulcrypt. No se incluye transcodificación ni se cambian las credenciales del usuario.
