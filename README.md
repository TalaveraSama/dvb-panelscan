# dvb-panelscan

Conjunto de herramientas en C y Python para **sintonizar tarjetas TBS/DVB-S2,
escaneo de canales, descrambling (acceso condicional newcamd) y salida UDP/HTTP
para headends IP→analógico**.

Desarrollado reutilizando lógica de [TVHeadend](https://github.com/tvheadend/tvheadend)
(protocolo newcamd `cwc.c`, descrambler CSA `tvhcsa.c`, demux/PSI `dvb_psi`).

## Actual: 0.8.1 · Logos locales y diagnóstico EPG

Incluye 12 logos del listado comunicado, con asignación explícita por nombre,
prioridad a logos personalizados y exportación de iconos XMLTV/M3U. Los canales
sin coincidencia segura quedan sin imagen, no se adivina la marca. Fuentes y
condiciones en `panel/static/LOGOS-SOURCES.txt` (marcas de sus titulares).

Si XMLTV solo contiene `<channel>` y ningún `<programme>`, abrir un canal del
transpondedor, esperar unos minutos y consultar `/api/epg/status`. Devuelve la hora
del servidor, recepción PID 0x12, tablas y eventos parseados. No prueba por sí solo
que el proveedor emita guía estándar; logos y programación son independientes.

### EPG real de uno o varios días (0.8.0)

En **Canales y EPG** se puede elegir entre **1 y 14 días** de programación y
consultar los eventos recibidos por canal. El EPG se guarda en `epg.json` privado,
sobrevive al reinicio y elimina los eventos vencidos/fuera del período. Se recoge
EIT present/following y schedule del transpondedor actual al escanear y mientras
su adaptador esté recibiendo. XMLTV usa los inicios y duraciones reales; ya no
asigna horas artificiales a títulos sin horario. Disponibilidad según el satélite:
seleccionar siete días no garantiza que los emita ni descarga datos de Internet.

La tabla **Canales y EPG** también tiene **Editar** para asignar un perfil
Newcamd por TP:SID (por ejemplo, una cuenta por satélite), sin mezclar SIDs iguales.
Se mantienen los eventos en vivo sin grabación: guardar EPG no guarda logs.
Ver [operación y límites EPG](docs/EPG.md).

### Eventos en vivo incorporados en 0.7.1

Nueva pestaña **Eventos en vivo**: arranque/parada de canales, ECM/CW y latencia,
conexiones/reconexiones, señal/lock y estadísticas TS. Filtro por canal/nombre,
adaptador, mensaje y nivel; botón para limpiar pantalla. Sin archivos nuevos ni
historial: solo se reciben eventos mientras se ve la pestaña. Al salir o esconder
el navegador se desconecta y se vacía. Las colas de transporte y pantalla tienen
límites para no bloquear el streaming. Los indicadores ECM siguen funcionando
con métricas en RAM, sin leer/escribir archivos de diagnóstico.

El usuario confirmó recepción simultánea de varios canales en su TBS real con
la versión anterior. Esta nueva vista se valida con pruebas automatizadas.
Los archivos de log de versiones anteriores no se borran automáticamente, pero
ya no se crean ni amplían. Ver [eventos y migración](docs/LIVE_EVENTS.md).

### Estado ECM y perfiles incorporados en 0.7.0

- Canal con brillo verde tras dos respuestas ECM/CW no nulas recientes; rojo
  ante error/CW vacía o falta de respuestas durante 15 s en una sesión cifrada.
  Amarillo al iniciar, azul para libres y gris para inactivos o sin telemetría.
  Solo canales abiertos: recibir TS no equivale a descifrar vídeo correctamente.
- Perfiles Newcamd múltiples, asignables por canal desde **Editar** en Dashboard.
- Editor de nombre, grupo M3U, número, logo y habilitación; ajustes persistentes
  e independientes del reescaneo. Se aplica también a M3U/XMLTV.
- Guardar cambios detiene únicamente los canales afectados: reabrir sus URLs.
  La cuenta anterior se conserva como perfil **Predeterminado**.

Ver [operación, colores, perfiles y límites](docs/CHANNELS_AND_PROFILES.md).

### Motor multicanal incorporado en 0.6.0

El panel recibe **una sola vez cada transpondedor por adaptador** y abre varios
canales simultáneamente, con worker/colas/claves independientes por servicio.
Varios espectadores del mismo canal comparten worker. Un broker Newcamd por
configuración comparte conexión entre canales y adaptadores y correlaciona ECM
con CW por secuencia, sin bloquear los demás servicios por un timeout individual.

Usar las URLs del M3U en varios reproductores a la vez. El formulario manual usa
puerto **0** (solo panel); salidas directas adicionales necesitan puertos distintos.
Detener un canal no detiene los otros del mismo mux. Transpondedores diferentes
siguen necesitando adaptadores diferentes; un escaneo no interrumpe streaming.

Ver [arquitectura, límites y prueba TBS](docs/MULTICHANNEL.md) y
[instalación/notas actuales](RELEASE_NOTES.md). Las secciones de versiones 0.4/0.5
más abajo son históricas: su límite de un SID por adaptador ya no aplica al panel
0.6.0. No se promete eliminar todos los tirones ni se incluye HLS.

## Componentes

| Carpeta | Descripción |
|---|---|
| `dvb2m3u8/` | Escáner DVB/S2 → lista M3U8 o JSON. Sintoniza, demultiplexa PAT/PMT/SDT/NIT/EIT y lista los servicios. |
| `tbsstream/` | Sintoniza + **descifra con newcamd** (CSA/libdvbcsa) + remuxea + reenvía por **UDP** (IP→analógico) y **HTTP** (TS + M3U8). |
| `panel/` | **Panel web** (HTML/JS + Python stdlib) para administrar el escaneo: adaptadores, escanear, canales con EPG now/next, exportar M3U8. |
| `tvh-dvb-kit/` | Módulos extraídos de TVHeadend (DVB/TBS, EPG, M3U, descrambler, muxer, transcodificación) para estudio/reutilización. |

## Requisitos (Linux)

```sh
sudo apt install build-essential libssl-dev libdvbcsa-dev python3
```

## Compilar

```sh
make -C dvb2m3u8
make -C tbsstream          # requiere libdvbcsa + OpenSSL
```

## Uso rápido

```sh
# Escanear un mux (listar canales + now/next)
sudo ./dvb2m3u8/dvb2m3u8 -t dvbs --freq-mhz <FI_MHz> --symbol-rate <sym/s> --pol H --json

# Descifrar + reenviar por UDP (para el modulador IP→analógico)
sudo ./tbsstream/tbsstream -t dvbs --freq-mhz <FI_MHz> --symbol-rate <sym/s> --pol H \
  --newcamd 127.0.0.1:5000 --deskey 0102030405060708091011121314 \
  --user astra --pass astra \
  --udp 239.1.1.1:1234 --http-port 9981

# Panel web
python3 panel/server.py --host 0.0.0.0 --port 8081
```

> ⚠️ **Notas específicas del driver TBS (TBSECP3):**
> - La frecuencia se pasa en **kHz** internamente (el driver no usa Hz como el estándar).
> - El symbol rate va en **símbolos/segundo** (p. ej. `27900000` para 27.9 MBaud).

## Estructura

```
dvb-panelscan/
├── dvb2m3u8/        escáner
├── tbsstream/       descrambling + UDP/HTTP
│   └── tools/       utilidades de diagnóstico (login newcamd, scan, señal)
├── panel/           panel web
└── tvh-dvb-kit/     módulos extraídos de TVHeadend
```

## Estado / despliegue

Probado en un servidor **TBS 6904se (DVB-S/S2/S2X, 4 tuners)** con **OSCam 2.26**
(newcamd puerto 5000). Login newcamd validado. El escaneo/descrambling de
extremo a extremo queda pendiente de alinear el plato y conectar el lector de
smartcard (ver `tvh-dvb-kit/README.md` para el detalle de los módulos).

## Licencia

GPLv3 (reutiliza código de TVHeadend: `cwc.c`, `tvhcsa.c`, `crypt_md5`).

## Release 0.4.0 · Ubuntu

El paquete `dvb-panelscan_0.4.0_amd64.deb` se compila en **Ubuntu 22.04 amd64**
y usa OpenSSL 3/libdvbcsa del sistema. En Ubuntu 22.04/24.04 amd64:

```sh
sudo apt update
sudo apt install ./dvb-panelscan_0.4.0_amd64.deb
sudo systemctl enable --now dvb-panel
# Acceder localmente a http://127.0.0.1:8081
```

Para un servidor remoto, usar un túnel SSH desde tu equipo:

```sh
ssh -L 8081:127.0.0.1:8081 usuario@servidor
```

El servicio no requiere root: usa `dvb-panel` y el grupo `video`. Verificar que
los dispositivos `/dev/dvb` concedan acceso al grupo `video`. Los datos y logs
están en `/var/lib/dvb-panelscan` (directorio privado; configuración modo 0600).
No se inicia automáticamente al instalar. Para actualizar, detener el servicio,
instalar el nuevo paquete y arrancarlo de nuevo. No se eliminan los datos al
quitar el paquete.

**Seguridad:** el panel todavía no tiene autenticación. No publicarlo directamente
en Internet ni en una LAN no confiable. El paquete escucha solo en loopback.
El modo manual `--host 0.0.0.0` es únicamente para redes controladas o detrás de
un proxy con autenticación/TLS. Newcamd utiliza criptografía heredada por
compatibilidad; usar una red privada/VPN. Las credenciales se pasan al proceso
`tbsstream` por argumentos: el administrador del host puede verlas. La API ya
no devuelve estos argumentos en el estado del streaming.

### Diagnóstico del acceso condicional (solo suscripciones autorizadas)

1. Guardar servidor, puerto, usuario, contraseña y DES key reales. No hay cuenta
   predeterminada. Usar **Comprobar conexión TCP**: solo valida conectividad,
   no autenticación ni permisos de tarjeta.
2. Elegir transponder y **SID** del canal en Streaming; vacío selecciona el primer
   servicio de PAT. Cada URL entrega **un servicio**; se pueden abrir varias simultáneamente.
3. Revisar los registros de canal, mux y Newcamd desde Streaming y los logs de OSCam. El cliente
   completa login + solicitud de datos de tarjeta; `CW actualizada` indica una
   respuesta ECM válida, no una comprobación visual del vídeo.
4. Confirmar CAID, lector activo y autorización de la cuenta. En simulcrypt se
   selecciona el primer descriptor CA: la selección explícita por CAID/proveedor
   sigue pendiente. El panel usa ahora el broker compartido descrito arriba.
   No se implementan EMM ni renovación de derechos de tarjeta.

Corregido en esta versión: ECM cortas descartadas por exigir CRC de sección
larga; envío ECM truncado; PID ECM inicializado a -1 pero comparado con cero;
CA a nivel de programa ignorado; ausencia de solicitud CARD_DATA; sustitución
de CW válidas por mitades vacías. También se validan los límites del framing y
la longitud declarada de respuestas CW. No se afirma validación de descifrado
end-to-end sin hardware, tarjeta y servidor autorizados.

### Compilación reproducible y pruebas

```sh
sudo apt install build-essential libssl-dev libdvbcsa-dev python3
python3 -m unittest discover -s tests -p 'test_*.py' -v
make -C dvb2m3u8 && make -C tbsstream
gcc -O1 -g -fsanitize=address,undefined -o /tmp/test-demux \
  tests/test_demux.c tbsstream/csa.c tbsstream/newcamd.c -ldvbcsa -lcrypto -lpthread
/tmp/test-demux
sh packaging/build-deb.sh 0.4.0
```

GitHub Actions ejecuta estas pruebas y genera el `.deb`. La descarga del código
fuente no contiene binarios: usar el `.deb` de Releases para instalar.

## Actualización 0.4.1 · Continuidad de vídeo

La versión 0.4.1 conserva fragmentos TS entre lecturas del DVR, reenvía PCR
separado, completa escrituras parciales TCP y entrega HTTP en lotes de hasta
siete paquetes disponibles. Los clientes nuevos se conectan al punto en vivo.
También rechaza un ID de transponder inexistente antes de iniciar un proceso.
Estas correcciones eliminan fallos concretos de transporte, pero no garantizan
resolver cortes por señal, carga, red o cambios de claves. Hay pruebas de
regresión en `tests/test_transport.c` ejecutadas con ASan/UBSan en Actions.

Para actualizar, descargar el `.deb` 0.4.1 desde Releases, detener `dvb-panel`,
instalar con `apt install ./dvb-panelscan_0.4.1_amd64.deb` y volver a iniciar el
servicio. Se conservan la configuración privada y los overrides de systemd.

## Comparación con Tvheadend

Ver [revisión de playlists, MPEG-TS y descrambling](docs/TVHEADEND_STREAMING_REVIEW.md),
con enlaces a un commit fijo de Tvheadend y diferencias concretas respecto a
0.4.1. La playlist M3U no es HLS; los principales pendientes son el motor de
suscripciones compartidas, PSI con descriptores y la gestión temporal de CW.

## Versión 0.5.0 · Mejoras aplicadas de la revisión Tvheadend

- Gestor compartido por adaptador para M3U y modo manual; se elimina el puente
  UDP local del panel. Un canal por tuner, varios reproductores del mismo canal.
- Reserva de adaptador y protección frente a escaneos simultáneos.
- M3U/XMLTV con identidad `transponder_id:SID`; reexportar ambas listas después
  de actualizar. Las rutas antiguas SID-only se aceptan si son inequívocas.
- PMT con descriptores no CA, validación atómica, tablas PSI multipaquete y
  emisión monotónica de 100 ms durante recepción TS, antes del payload inicial.
- Colas acotadas y diagnóstico por sesión desde Streaming: clientes, bytes,
  cola y desbordamientos de clientes lentos. Registros `stream-<adapter>-<SID>.log`.
- El único perfil implementado es `pass` (TS sin transcodificación, no HLS).

El inicio bajo demanda espera datos TS y devuelve errores de arranque/conflicto
antes de HTTP 200. Una cola lenta se desconecta, no bloquea a los demás.
Las sesiones bajo demanda cierran al salir el último cliente; las manuales
permanecen hasta **Detener todas**. El botón desconecta todas las sesiones.

Para usar el puerto directo 9981, iniciar manualmente primero. Si el canal ya
se inició desde el M3U sin ese puerto, utilizar la URL del panel o detener y
volver a iniciar; no se alteran las salidas del proceso activo.

La actualización conserva datos y overrides systemd. Instalar el `.deb` 0.5.0
con el servicio detenido, reiniciarlo y recargar el navegador. Ver
[notas de versión](RELEASE_NOTES.md). Los programas C y el servidor Python deben
actualizarse juntos: el gestor necesita la opción `tbsstream --stdout`.

**Alcance:** no es el motor completo de Tvheadend. No implementa varios SID por
adaptador ni el seguimiento temporal de generaciones de CW, y no garantiza
eliminar tirones por cambio de claves, señal o red. La revisión histórica de
0.4.1 sigue en `docs/`; las diferencias aplicadas se resumen aquí.

## Versión 0.5.1 · Cambio de canal y diagnóstico de tirones

Si solo abre el SID iniciado manualmente, revisar **Streaming**: una sesión
manual fija reserva el adaptador. **Detener este canal** la libera sin detener
las de otros tuners. Se mantiene un SID por adaptador, varios clientes del
mismo SID. La petición sin channel_id a `/api/stream/stop` detiene todas;
con `{"channel_id":"transponder:SID"}` detiene solo la sesión indicada.

La cola del servicio ahora mantiene el orden de audio/vídeo/PCR al esperar CW
y descifra lotes de hasta 64 paquetes disponibles. El límite de cola no cambia
las restricciones de generaciones de clave: no equivale al motor completo de
Tvheadend. Se deduplican ECM iguales pendientes y se solicita más buffer al
kernel cuando el driver lo admite.

Para diagnosticar, reproducir 30 segundos y revisar **Ver registro** o:

```sh
grep -E 'TS stats|ECM:|ECM sin|queue full|newcamd:' /var/lib/dvb-panelscan/stream-0-672.log | tail -n 30
```

- `tei`/`cc_errors`: indicadores acumulados del TS de entrada completo. No
  distinguen por sí solos señal del driver o del lector, y CC repetidos no
  se consideran automáticamente pérdidas.
- `service_drops`: paquetes descartados al llenarse la cola esperando claves.
- `pending`, `ecm_pending`, `ecm_drops`: presión de las colas.
- `csa_batches`, `csa_packets`: trabajo CSA acumulado; la relación depende
  de cuántos paquetes seleccionados hay en cada lectura.
- `ECM time_ms`/`result`: latencia y resultado de la solicitud, no prueba de
  decodificación correcta. Nunca se imprimen las CW.

**No se añade HLS en 0.5.1.** La playlist de canales de Tvheadend y su perfil
pass no son HLS segmentado. Un empaquetador HLS con búfer puede reducir jitter
percibido, pero no reconstruye paquetes perdidos ni corrige CW fuera de período.
La actualización conserva datos y overrides. Ver RELEASE_NOTES.md.

### Newcamd: conexión compartida y arranque

Ver [revisión específica del cliente Newcamd de Tvheadend](docs/TVHEADEND_NEWCAMD_REVIEW.md):
cola de ECM, correlación por secuencia, contextos por servicio, marcas de CW,
paridad inicial y diferencias pendientes respecto a 0.5.1. El intervalo base
observado de 10 segundos no equivale a una espera fija de arranque; no se
atribuye el síntoma del usuario a ese valor sin medirlo.
