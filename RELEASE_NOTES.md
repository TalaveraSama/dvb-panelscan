## DVB PanelScan 0.6.0 · Multicanal por transpondedor y Newcamd compartido

### Varios canales simultáneos

- Una sola captura DVB por adaptador/transpondedor, compartida por varios SID.
- Un worker independiente por canal para demux, CSA, claves y colas. Los workers
  reciben TS por stdin y **no vuelven a sintonizar** el adaptador.
- Varios espectadores del mismo canal siguen compartiendo worker. Detener un
  canal no detiene los demás; captura y broker cierran al salir su último usuario.
- Diferentes transpondedores pueden trabajar simultáneamente en adaptadores
  distintos. Se mantiene 409 si se intenta cambiar de transpondedor o escanear un
  adaptador con canales activos; no se interrumpe su recepción.
- Mux de 32 MiB y salida de 8 MiB por servicio, acotados. Un feeder o reproductor
  rezagado no bloquea a otros servicios; el fallo se informa, no se oculta.

### Newcamd compartido

- Un broker por configuración, compartido entre servicios y adaptadores, con una
  sola conexión upstream y múltiples ECM pendientes (hasta 256 workers).
- SID/CAID/proveedor y secuencia conservados; las respuestas fuera de orden se
  envían al worker correcto. Caché positiva de un segundo para retransmisiones
  idénticas del mismo worker, no una caché global por SID.
- Deadline individual de 5000 ms en lugar de 1500 ms. Un deadline devuelve -3 y
  **no reconecta la conexión compartida**, ni bloquea los ECM de los otros canales.
  Respuestas tardías se ignoran. Un NOK devuelve -2. Un error real de transporte
  puede afectar a todos los servicios del broker y provoca reconexión bajo demanda.
- Indicadores de CW invalidados en el servicio que falla; no se promete conservar
  una clave antigua indefinidamente. Cola de espera ampliada a 65536 paquetes
  (~12 MB) por servicio, con descartes explícitos si se llena. El mayor límite no
  impone una pausa de arranque ni garantiza soportar cualquier bitrate/espera.
- Socket UNIX privado (directorio 0700, socket 0600); credenciales solo en argv del
  broker, no en los workers ni en el capturador. Keepalive de 30 segundos.

### Panel y diagnóstico

- Estado con PID del worker, PID de captura compartida, PID/estado del broker,
  cola de mux y error del feeder. El formulario manual usa puerto 0 (solo panel).
- Para salida HTTP directa, utilizar un puerto distinto por canal. Las URLs del
  M3U/panel no necesitan puertos extra. Clientes directos no se cuentan aquí.
- Registros separados de canal, mux y Newcamd desde Streaming:
  `stream-<adapter>-<SID>.log`, `mux-<adapter>.log`, `ca.log`.
- Se conservan TP:SID, M3U/XMLTV, paradas individuales y sesiones manuales fijas.

### Actualizar Ubuntu 22.04 amd64

```sh
wget -O /tmp/dvb-panelscan_0.6.0_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.6.0/dvb-panelscan_0.6.0_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.6.0_amd64.deb
sudo systemctl start dvb-panel
```

Se conservan los datos y overrides de systemd. Cerrar reproductores antiguos y
recargar el panel con Ctrl+F5. Abrir dos canales del mismo transpondedor en dos
reproductores sin cerrar el primero: deben tener el mismo `mux_pid` y `ca_pid`,
pero distinto `pid`. Detener uno y comprobar que el otro continúa. Repetir con
otro transpondedor/adapter: distinto `mux_pid`, mismo `ca_pid` si comparte CA.
No es necesario volver a escanear si la lista guardada es correcta.

### Pruebas y límites

28 pruebas Python/HTTP, incluyendo concurrencia, refcounts, limpieza, aislamiento
ante worker detenido, dos canales sobre una captura y dos adaptadores. Integración
con multiplex sintético de dos servicios, broker y workers C **reales**: separación
de PIDs, PAT con CRC y parada independiente. Regresiones C con ASan/UBSan para
CSA, demux, transporte y Newcamd. Nueva prueba de broker con upstream cifrado
simulado: solicitudes simultáneas, respuestas fuera de orden, caché, deadline,
CW tardía y NOK sin reconectar los otros servicios. Actions también verifica el
paquete mediante instalación y arranque del servicio en Ubuntu 22.04 amd64.

**Sin validación en la TBS del usuario ni benchmark de todos sus canales.**
La arquitectura reparte el mux completo a cada worker, no es todavía el demux
central por PID de Tvheadend. El servidor CA debe autorizar y poder atender los
servicios simultáneos. Se activan bajo demanda o manualmente, no todos al escanear.

No se garantiza que desaparezcan todos los tirones. Siguen pendientes la gestión
temporal completa de generaciones CW, muestreo de paridad inicial, selección
avanzada de CAID/simulcrypt y EMM. No hay HLS, transcodificación ni reparación
artificial de PCR/PTS/CC. Los bytes TS/CW no certifican vídeo válido. El panel
sigue sin autenticación y los logs sin rotación: usar solo en red confiable o
tras un proxy seguro. Ver `docs/MULTICHANNEL.md` para arquitectura y prueba TBS.
