# Revisión de M3U y MPEG-TS frente a Tvheadend

## Alcance y fuente

Revisión del código de `tvheadend/tvheadend`, rama `master`, fijada al commit
[`8837853a69aa822b0203eb3909c5912dc831312e`](https://github.com/tvheadend/tvheadend/commit/8837853a69aa822b0203eb3909c5912dc831312e).
Comparación con dvb-panelscan 0.4.1 (`a23cde5`). Las conclusiones son de lectura
estática; no se ha realizado una medición con el hardware del usuario.

Los módulos guardados en `tvh-dvb-kit/` son referencias: no se compilan ni se
integran automáticamente en el ejecutable `tbsstream`.

## 1. M3U8 no equivale a HLS

En las rutas de playlist revisadas, Tvheadend genera una lista de canales con
`#EXTM3U`, entradas `#EXTINF:-1` y URLs de streaming. No es una playlist HLS
segmentada con `#EXT-X-TARGETDURATION`, secuencias y segmentos `.ts`.

`http_m3u_playlist_add()` añade metadatos opcionales (`tvg-id`, `tvg-logo`,
`tvg-chno`), el nombre, el host, la ruta y `profile=...`; también admite tickets
o códigos de acceso. La lista de canales revisada utiliza la ruta
`/stream/channel/<uuid>`. Hay otras rutas, incluida `/stream/channelid/<id>`.

Fuentes:
- [Generación de una entrada M3U](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L565-L620).
- [Playlist de canales](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L865-L930).
- [Rutas de streaming](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L1727).

En nuestro panel, `build_m3u8()` produce una lista de este mismo tipo general,
no HLS. La URL `http://192.168.1.22:8081/stream/channelid/672?profile=pass`
es coherente con el endpoint propio. Cambiar la extensión o el texto de la
playlist no corrige un TS con pérdidas o problemas de claves.

**Diferencia de identidad:** nuestro `672` es el SID DVB. En Tvheadend,
`channelid` identifica un canal interno; `channel_find_by_id()` busca un nodo
por su identidad, no por el SID de la PMT. Por ello, copiar literalmente esa
URL a un servidor Tvheadend no garantiza seleccionar el mismo canal.

Fuente: [channel_find_by_id](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/channels.c#L699-L705).

En nuestro proyecto, `_find_tp_by_sid()` selecciona la primera coincidencia.
Si varios transponders repiten SID, la URL y el `tvg-id` no son unívocos.
La evolución correcta es una identidad estable de canal, o al menos
`transponder_id + SID`, actualizando también XMLTV y manteniendo las URLs
anteriores solo cuando no sean ambiguas.

## 2. Pipeline HTTP: suscripción, perfil y muxer

Tvheadend, en `http_stream_channel()`:

1. Comprueba permisos del canal y selecciona un perfil permitido.
2. Inicializa y abre un `profile_chain`.
3. Crea una suscripción con `subscription_create_from_channel()`.
4. Entrega mensajes y datos a `http_stream_run()`.
5. Cierra suscripción y perfil al finalizar.

La suscripción se conecta al `s_streaming_pad` del servicio. No ejecuta un
binario nuevo por petición HTTP: existe un motor de servicios y suscriptores.
Esto permite distribuir la salida del servicio a sus consumidores; la elección
de recursos depende del planificador, no de procesos HTTP que resintonizan
independientemente el mismo adaptador.

Fuentes:
- [http_stream_channel](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L1510-L1565).
- [Enlace de suscripción al servicio](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/subscriptions.c#L73-L125).

En dvb-panelscan, `_stream_channel()` hace:

```text
petición HTTP :8081 -> nuevo tbsstream -> UDP 127.0.0.1 -> Python -> cliente
```

Cada petición vuelve a abrir/sintonizar el adaptador y conectar Newcamd.
No existe una reserva central del tuner ni una salida compartida por servicio.
El inicio manual por `/api/stream/start` utiliza otra instancia:

```text
inicio manual -> tbsstream -> HTTP :9981/stream.ts
```

Son dos caminos diferentes. No conviene abrir simultáneamente ambos con el
mismo tuner. Tampoco se garantiza reproducción multicanal por el mero hecho
de listar todos los canales de un mux en M3U.

Además, el proceso creado por la ruta M3U descarta stderr, responde HTTP 200
antes de verificar que haya TS y no interrumpe de inmediato la espera cuando
el proceso termina sin entregar datos. Esto oculta errores al reproductor.

## 3. Qué significa realmente profile=pass

En Tvheadend, `profile_mpegts_pass_open()` selecciona `SUBSCRIPTION_MPEGTS`,
configura la cola y crea `MC_PASS`. Las opciones de reescritura de PAT, PMT,
SDT, NIT y EIT se transfieren al muxer. No significa transcodificar vídeo/audio
ni aplicar necesariamente la cadena `tsfix` de otros perfiles.

Fuente: [perfil MPEG-TS passthrough](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/profile.c#L1447-L1503).

En nuestro panel, `profile=pass` es decorativo en la ruta de reproducción:
no selecciona una cadena de procesamiento. El stream ya es una salida TS
simplificada. Copiar el parámetro de Tvheadend no incorpora su motor.

## 4. PAT/PMT y formación del TS

El muxer pass de Tvheadend procesa bloques TS y, cuando está habilitada la
reescritura, reensambla tablas y las vuelve a packetizar con CRC. Su callback
PAT limita el programa; el callback PMT selecciona los componentes de la
suscripción y valida coherencia entre SID, PCR y componentes. Para cada ES
seleccionado copia la entrada con sus descriptores; no reduce sistemáticamente
todos los ES a `ES_info_length = 0`.

Fuentes:
- [PAT y PMT](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/muxer/muxer_pass.c#L319-L466).
- [Procesamiento por bloques TS](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/muxer/muxer_pass.c#L985-L1039).

En nuestro `build_pmt()` se pone a cero tanto `program_info_length` como
`ES_info_length`. Se eliminan CA, pero también idiomas, identificación de
AC-3/E-AC-3 cuando depende de descriptores, subtítulos y teletexto. Esto puede
reducir compatibilidad o impedir identificar correctamente ciertos componentes.
La mejora debe conservar los descriptores pertinentes y filtrar CA
selectivamente según la política de salida descifrada; no copiar una PMT con
CA sin analizar el resultado.

Otros límites propios:

- PAT/PMT se insertan cada 2000 paquetes de entrada, **no cada 100 ms reales**.
  El comentario del código no corresponde a una temporización: depende del
  bitrate del mux, de la fragmentación de lecturas y de la disponibilidad de
  datos. La emisión ocurre después de procesar el bloque, por lo que el primer
  payload puede preceder a las tablas generadas.
- `make_psi_packet()` solo maneja una sección que quepa en un paquete
  (máximo 183 bytes tras cabecera y pointer). Con los 32 ES sin descriptores
  actuales la PMT cabe; **añadir descriptores sin packetización multipaquete
  produciría un desbordamiento**. Antes hay que cambiar generador y pruebas.
- Los contadores CC de PAT/PMT se generan localmente; los CC de los ES se
  conservan. No hay métricas propias de errores CC o TEI para separar pérdida
  de señal y pérdida del pipeline.
- El PCR se conserva en los paquetes, no se reconstruye un nuevo reloj. La
  0.4.1 ya reenvía PCR separado; eso no equivale a un regulador de bitrate o a
  un remux CBR. El transporte HTTP tampoco exige TS CBR.

## 5. Colas y escritura HTTP

`http_stream_run()` recibe mensajes `SMT_START`, `SMT_MPEGTS`, `SMT_STOP`,
estados de servicio y descrambling. Inicializa el muxer tras START y pasa los
datos a `muxer_write_pkt()`. Usa plazos de arranque/paquetes y timeout de envío.
En la ruta de canal revisada la cola por defecto es 1 500 000 bytes y admite
el parámetro `qsize`; no es una promesa de segundos de caché.

Fuentes:
- [Bucle HTTP y estados](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L367-L559).
- [Cola de la suscripción HTTP](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/webui/webui.c#L1534-L1546).
- [Escrituras del muxer pass](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/muxer/muxer_pass.c#L866-L902).

Nuestro ring HTTP es de 4096 paquetes, 770 048 bytes. Su duración depende del
bitrate del servicio. Si un cliente se retrasa, se salta al tramo conservado:
puede generar una discontinuidad. Agrupar hasta siete paquetes en 0.4.1 reduce
llamadas a `send()`, pero no establece un ritmo PCR ni resuelve un consumidor
lento. La ruta M3U tiene además el socket UDP local: escribe un paquete por
datagrama y puede perder datos si se llena el buffer; no hay recuperación UDP.

## 6. CW y cambios de paridad: diferencia crítica para cortes periódicos

Tvheadend no se limita a guardar una CW par y una impar. En
`descrambler.c` utiliza validez, tiempos de claves/ECM, detección de cambios de
paridad, colas con timestamp y decisiones de actualización. Puede posponer un
cambio hasta analizar datos suficientes y distingue claves tardías. La función
CSA recibe grupos de paquetes, no necesariamente un único paquete por llamada.

Fuente: [procesamiento de cola y cambio de claves](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L1090-L1268).

Nuestro `ca_thread()` aplica inmediatamente cada mitad no nula de la respuesta
a los contextos CSA. `have_cw_even/odd` no representa una generación ni una
validez temporal del período criptográfico. No se distingue de forma completa
entre CW actual y futura. También:

- Se descifra paquete a paquete, aprovechando poco el backend bitslice.
- La cola solo conserva paquetes scrambled mientras no haya una CW marcada
  disponible. No mantiene el orden de todos los PID del servicio en ese lapso.
- `flush_scrambled()` descarta paquetes de paridad aún no disponible y vacía
  toda la cola; el buffer lleno tampoco genera una métrica de descarte.
- Los ECM repetidos pueden entrar en la cola sin deduplicación; no hay medida
  de edad de ECM ni correlación con el instante PCR del payload.

Esto convierte los cortes en cambios de clave en una **hipótesis prioritaria**
si son periódicos. Recibir `CW actualizada` no demuestra que se haya aplicado
la generación correcta a todos los paquetes. El usuario confirmó vídeo visible
en VLC, pero no hay una captura/medición que atribuya sus tirones a este punto.

## 7. Orden propuesto de implementación

1. **Observabilidad:** contadores CC/TEI, descartes DVR/cola/ring/UDP,
   tiempos ECM y cambios de paridad. Logs por servicio sin imprimir CW ni secretos.
2. **Servicio compartido:** identidad estable de canal, reserva de adaptador,
   distribución a varios suscriptores y cierre cuando no queden consumidores.
   Eliminar la diferencia de gestión entre M3U y streaming manual.
3. **PSI robusta:** conservar descriptores relevantes, packetización de tablas
   en múltiples TS, CRC/CC, emisión inicial y programación monotónica acotada.
4. **Descrambler temporal:** estado actual/pendiente por paridad y generación,
   orden de paquetes con PCR, límites temporales, deduplicación ECM compatible
   con reintentos y procesamiento CSA en lotes.
5. **Validación real:** grabación TS corta y análisis de CC/PCR/errores de
   decodificación, carga CPU, latencia ECM y comparación con Tvheadend usando
   la misma señal y acceso autorizado, sin competir por el mismo adaptador.

No conviene copiar archivos de Tvheadend al Makefile: dependen de su motor de
servicios, colas, tipos y planificador. Hay que portar unidades delimitadas o
usar Tvheadend como backend. Esta revisión no modifica el runtime ni publica
una nueva versión; documenta diferencias verificadas y el plan de trabajo.

### Seguimiento: implementación 0.5.0

Esta comparación describe 0.4.1. La 0.5.0 aplica identidades de canal, gestor
compartido con reserva de tuner, colas acotadas sin UDP local, errores de
arranque visibles, conservación de descriptores no CA y PSI multipaquete con
emisión monotónica. La gestión temporal de CW, CSA por lotes, varios SID por
tuner y métricas completas de continuidad siguen pendientes. Ver las notas
de versión y el README principal para el comportamiento actual.
