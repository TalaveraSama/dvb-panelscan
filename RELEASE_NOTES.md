## DVB PanelScan 0.5.1 · Continuidad TS y cambio de canal

Correcciones verificadas por pruebas, no una garantía de resolver todos los tirones en hardware real.

### Sesiones

- Botón **Detener este canal** y parada individual mediante `POST /api/stream/stop` con `{"channel_id":"<tp>:<sid>"}`. Sin channel_id se mantiene Detener todas.
- El error 409 identifica el adaptador, el canal que lo ocupa y si es una sesión manual fija. Una sesión manual del SID 672 impide abrir otro SID en ese tuner hasta detenerla, aunque marque cero clientes del panel. Clientes del puerto directo 9981 no se cuentan como suscriptores del panel.
- Se mantiene un SID por adaptador: no se promete multicanal por mux ni se reasignan tuners sin conocer sus conexiones de antena.

### Continuidad

- Cola ordenada del servicio completo (vídeo, audio y PCR), no solo los paquetes cifrados. No se adelantan paquetes claros a otros que esperan CW.
- Descrambling CSA en lotes de hasta 64 paquetes disponibles por lectura; una clave no disponible bloquea el prefijo pendiente en lugar de descartarlo al llegar otra paridad.
- Desbordamiento de cola acotado y contabilizado, sin crecimiento ilimitado.
- Deduplicación exacta de ECM todavía en cola; se conservan ECM diferentes y siguen siendo posibles los reintentos.
- Solicitud de buffer demux de 4 MiB cuando el driver lo permite, lecturas DVR más grandes y drenaje de cola en pausas de lectura.
- Logs `TS stats` cada 5 segundos con TEI, saltos CC, descartes de servicio, cola y descartes ECM, lotes y paquetes CSA. Las métricas TEI/CC corresponden al TS de entrada completo, no solo al canal seleccionado.
- Logs de tiempo de respuesta ECM y resultado, sin imprimir credenciales ni CW.

### Tvheadend y HLS

La ruta oficial revisada `profile=pass` usa suscripción, cola, muxer y TS continuo. Su M3U de canales no es un manifiesto HLS segmentado. Esta versión **no añade HLS**: un empaquetador HLS podría amortiguar jitter a costa de latencia, pero no recupera paquetes perdidos ni corrige claves aplicadas en un período equivocado. Ver docs/TVHEADEND_STREAMING_REVIEW.md.

### Actualizar Ubuntu amd64

```sh
wget -O /tmp/dvb-panelscan_0.5.1_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.5.1/dvb-panelscan_0.5.1_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.5.1_amd64.deb
sudo systemctl start dvb-panel
```

Datos y overrides systemd se conservan. Recargar el dashboard con Ctrl+F5, cerrar VLC anterior y detener la sesión manual fija antes de probar otro SID del M3U. Nuevos registros siguen en `/var/lib/dvb-panelscan/stream-<adapter>-<SID>.log`.

### Pruebas y límites

19 pruebas Python/HTTP, incluyendo parar el canal fijado y abrir otro sin afectar otro adaptador. Regresiones C con ASan/UBSan: orden de audio/PCR al esperar claves, dos paridades, lote CSA de 64 paquetes con comparación del payload descifrado, cola llena, contadores TEI/CC y las pruebas anteriores de demux/PSI/framing/transporte. Instalación/arranque en Ubuntu 22.04 amd64 mediante Actions.

No se ha validado esta versión end-to-end con hardware DVB del usuario. Sigue pendiente el seguimiento temporal completo de generaciones CW, selección CAID/proveedor en simulcrypt y EMM. Recibir una CW no demuestra que el vídeo sea válido. Tampoco se incluyen HLS, transcodificación, varios SID simultáneos en un tuner ni reparación artificial de PCR/PTS/CC.

El panel continúa sin autenticación: no exponer a Internet. Las credenciales en argv son visibles al administrador del host. Los logs aún no tienen rotación automática.
