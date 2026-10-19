## DVB PanelScan 0.8.3 · Referencia estable y empaquetado consolidado

Base funcional: 0.8.2, confirmada por el usuario en TBS real. Sin funciones nuevas
ni cambios al motor multicanal, perfiles, EPG, logos o interpretación de horarios.

- VERSION centraliza paquete, interfaz, API y publicación; BUILD_INFO incluye commit.
- Paquete incluye los documentos de operación y versiones, fuentes de logos,
  Installed-Size y manifiesto md5sums de todos los archivos instalados.
- Staging limpio por versión, validación del artefacto Debian antes de instalar,
  dependencias de instalación explícitas y recarga systemd tras desinstalación.
- CI instala y reinstala verificando conservación de EPG, UTC−6, período y un
  override local de systemd. No se empaquetan datos ni credenciales del usuario.
- Publicación con .deb, SHA256SUMS y BUILD_INFO.txt, marcada como latest.
- 60 pruebas Python/HTTP, DOM, integraciones C, sanitizadores y validación Debian.

Plataforma: Ubuntu 22.04 amd64. Ver docs/RELEASING.md para próximos cambios,
versiones, actualización y vuelta a una base. Esto no declara soporte LTS ni
fusiona automáticamente el PR abierto.

---

## DVB PanelScan 0.8.2 · Desfase horario UTC configurable

- Casilla en Canales y EPG: desfase UTC en horas, de −12 a +14, pasos de 0,25.
  Nicaragua: −6. Valor inicial 0 (UTC) para conservar compatibilidad.
- Ajuste persistente en epg.json, compartido entre navegadores y conservado al
  cambiar el número de días. API: utc_offset_minutes, entero validado.
- Vista Programas usa el desfase elegido, independiente del huso del navegador.
- XMLTV exporta horas locales con sufijo correcto (−0600 para Nicaragua), sin
  modificar los instantes UTC guardados, la retención ni la hora del servidor.
- Un desfase fijo no calcula horario de verano automáticamente.
- El diagnóstico comunicado confirma EPG activo: 42 eventos guardados, 665
  eventos parseados y CRC sin errores; esto no indica un fallo de recepción.
- 59 pruebas Python/HTTP, interfaz DOM y validación de persistencia, rangos,
  UTC−6 y conservación del instante real del programa.

---

## DVB PanelScan 0.8.1 · Logos locales y diagnóstico de EPG vacío

- Descarga e incluye 12 logos identificados del listado comunicado, con coincidencias
  explícitas por nombre y variantes HD. Se sirven localmente sin Internet en el receptor.
  No se asignan logos dudosos a Golden Premiere 2, canales locales ESA/HN/CR o TVChile.
- Logos en Canales y EPG, M3U y elementos icon de XMLTV con URL absoluta del panel.
  Se conservan los logos personalizados y pueden desactivarse dejando el campo vacío.
- Recepción EPG: GET /api/epg/status muestra adaptadores activos, TSID de PAT, paquetes
  PID 0x12, tablas, CRC inválidos, eventos parseados, últimas fechas y hora del servidor.
  Los contadores son solo RAM durante la captura; no hay archivos de logs ni historial.
- Explicación visible cuando no se reciben programas. XMLTV solo con channel significa
  que aún no hay eventos aceptados en la ventana, no que un logo pueda completar la guía.
- 58 pruebas Python/HTTP, DOM y sanitizadores C. No se afirma corregido el EPG real
  del proveedor sin revisar recepción; podría requerir otra fuente/formato no EIT estándar.

Fuentes, revisión y atribución de imágenes: panel/static/LOGOS-SOURCES.txt, incluida
junto con las imágenes en el paquete. Marcas de sus respectivos titulares; sin afiliación.

---

## DVB PanelScan 0.8.0 · EPG real de uno o varios días

- Canales y EPG: botón **Editar** para escoger perfil Newcamd por canal, también
  cuando dos satélites tienen SIDs iguales. Los perfiles y metadatos previos se conservan.
- Selector global de 1 a 14 días (próximas N×24 horas) y botón **Programas** por
  canal. La ventana incluye programas en curso y futuros, no un archivo histórico.
- Captura EIT 0x4E y schedule 0x50–0x5F del mux actual, validación CRC/TSID/fechas
  BCD, secciones multipaquete de hasta 4096 bytes, títulos y descripciones.
- Recibe EPG al escanear y desde la captura compartida del adaptador, sin otro
  proceso de sintonización ni bloquear canales por una escritura en disco.
- Persistencia privada 0600 en epg.json, escritura atómica cada 30 s y al apagar
  normalmente el servicio. Limpieza de vencidos, deduplicación y máximo 50 000
  eventos. Reducir días descarta datos fuera de ventana; ampliar requiere volver
  a recibir los eventos que no estaban guardados.
- XMLTV con horas UTC/duraciones emitidas, sin horarios inventados para títulos
  antiguos de ahora/después. Los canales siguen exportándose aun sin programas.
- Se mantienen **Eventos en vivo** sin archivos ni historial. EPG persistente
  es programación de televisión, no una grabación de los eventos del sistema.

### Actualizar Ubuntu 22.04 amd64

```sh
wget -O /tmp/dvb-panelscan_0.8.0_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.8.0/dvb-panelscan_0.8.0_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.8.0_amd64.deb
sudo systemctl start dvb-panel
```

Recargar con Ctrl+F5. En Canales y EPG elegir días y guardar; dejar un canal del
transpondedor abierto o escanearlo cuando no tenga streaming para recoger EIT.
Consultar Programas y descargar XMLTV. Para dos satélites, crear perfiles en
Acceso condicional y asignarlos con Editar en los canales de cada TP.

### Verificación y límites

55 pruebas Python/HTTP (dos integraciones C requieren compilar tbsstream), prueba
DOM y regresiones C con ASan/UBSan. EIT sintético verifica horarios UTC, UTF-8,
CRC, aislamiento entre mux/satélites, sección grande, TEI/saltos CC, retención,
persistencia, XMLTV real y selector/editor. El escáner C prueba secciones de
4096 bytes, schedule y horarios/descriptores reales con sanitizadores.

El usuario confirmó multicanal TBS real en versiones anteriores; el nuevo EPG
requiere comprobación en sus emisiones. Solo se guarda lo que emite el proveedor,
no se prometen 14 días completos. No se sintonizan adaptadores de fondo sin permiso,
no se importan guías externas ni se usan tablas EIT de otros transpondedores.
Ver docs/EPG.md. Sigue siendo un panel local sin autenticación.

---

# Historial 0.7.1

## DVB PanelScan 0.7.1 · Eventos en vivo, sin grabación

- Nueva pestaña **Eventos en vivo** con arranque/parada, resultados ECM/CW,
  latencia, conexión/reconexión, señal/lock y estadísticas TS acumuladas.
- Filtros por canal/nombre, adaptador, mensaje y nivel; limpiar pantalla.
- Solo eventos nuevos: sin archivos, base de datos ni historial. Al salir de
  la vista o esconder el navegador se cierra la suscripción y se borra la pantalla.
- Colas acotadas por visor (500 eventos); un visor lento no bloquea canales.
  No se imprimen claves, credenciales ni contenido ECM; solo eventos estructurados.
- Los procesos continúan drenando stderr para mantener métricas de salud en RAM,
  sin guardar transcripciones. No se generan/amplían stream-*.log, mux-*.log o
  ca.log, ni se envían los eventos a journald. Los archivos antiguos no se eliminan.
- Los botones de registros históricos se sustituyen por Eventos en vivo.
  El endpoint antiguo /api/stream/log devuelve 410. /api/events usa SSE sin caché
  ni replay; reconectar comienza de nuevo, sin recuperar eventos del corte.
- Se mantienen multicanal y perfiles. Usuario confirmó varios canales simultáneos
  en TBS real en la versión anterior; la vista nueva aún requiere prueba allí.

### Actualizar Ubuntu 22.04 amd64

```sh
wget -O /tmp/dvb-panelscan_0.7.1_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.7.1/dvb-panelscan_0.7.1_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.7.1_amd64.deb
sudo systemctl start dvb-panel
```

Se conservan perfiles, canales y overrides. Recargar con Ctrl+F5. Abrir Eventos
antes de abrir/cerrar canales para observar los eventos. No expongas el panel
sin autenticación a Internet. Verde ECM sigue sin certificar vídeo decodificado.

### Validación

46 pruebas Python/HTTP (dos de integración C requieren compilar tbsstream), más
prueba DOM de interfaz y regresiones C en CI. Nuevas comprobaciones: sin replay,
sin eventos retenidos sin espectadores, colas acotadas, consumidor lento,
exclusión de secretos, drenaje de stderr sin archivo, salud ECM en memoria,
SSE sin caché y limpieza al desconectar. La UI comprueba conexión solo en la
vista visible, desconexión al salir/esconderla y ausencia de eventos anteriores.

---

# Historial 0.7.0

## DVB PanelScan 0.7.0 · Estado ECM, canales y perfiles Newcamd

- Indicadores por canal en Dashboard y Streaming: verde brillante con dos CW no
  nulas recientes, rojo ante error o ausencia de ECM/CW en canal cifrado activo,
  amarillo comprobando continuidad, azul libre y gris inactivo/sin telemetría.
- Telemetría por proceso, con timestamp monotónico y PID; caduca tras 15 s y no
  confunde bytes TS, logs antiguos, otro worker ni éxito sin CW con descifrado.
  Al fallar la consulta HTTP desaparece el verde anterior. Respeta movimiento reducido.
- Múltiples perfiles Newcamd y asignación por canal, conservando la cuenta actual
  como Predeterminado. TCP test del perfil seleccionado; sin fallback automático.
- Editor de nombre, grupo, número, logo y habilitación. Ajustes TP:SID persistentes
  separados del escaneo; aplicados a Dashboard, EPG, M3U y XMLTV.
- Deshabilitar bloquea la URL y retira el canal de las exportaciones. Guardar el
  canal/perfil detiene solo los canales afectados: reabrir sus URLs para aplicar.
- Borrado de canal inequívoco por TP:SID; perfiles asignados protegidos de borrado.
- Se conserva la captura compartida y el multicanal de 0.6.0.

### Actualizar Ubuntu 22.04 amd64

```sh
wget -O /tmp/dvb-panelscan_0.7.0_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.7.0/dvb-panelscan_0.7.0_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.7.0_amd64.deb
sudo systemctl start dvb-panel
```

Se conservan configuración, canales y overrides. Cerrar reproductores antiguos y
recargar con Ctrl+F5. En Acceso condicional crear perfiles; en Dashboard → Editar
asignarlos. El color cambia solo mientras el canal esté abierto.

### Validación y límites

41 pruebas Python/HTTP, regresiones C con ASan/UBSan y prueba DOM JavaScript para
editor, perfil seleccionado, metadatos numéricos, luces verde/roja y apagado del
verde ante pérdida de telemetría. Integración con workers C reales y multiplex
simulado comprueba además el estado de canales libres. Actions instala y arranca
el paquete Ubuntu antes de publicar.

**Verde indica actividad ECM/CW reciente, no imagen decodificada correcta.** No
hay validación con hardware TBS en este entorno. Los ajustes de paridad/épocas CW,
CAID/simulcrypt, EMM, HLS y transcodificación siguen fuera de este cambio. El panel
sigue sin autenticación y los registros sin rotación: no exponer a Internet.
Ver `docs/CHANNELS_AND_PROFILES.md` para semántica y API.

---

# Historial 0.6.0

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
