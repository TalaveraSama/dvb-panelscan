# tvh-dvb-kit — módulos DVB/TBS, EPG, M3U, descrambling, muxer y transcodificación

Este directorio contiene los módulos **DVB/TBS**, **EPG**, **M3U**,
**descrambling (acceso condicional)**, **muxer (salida)** y **transcodificación**
del código fuente de [TVHeadend](https://github.com/tvheadend/tvheadend), copiados tal cual
(solo con la licencia original, GPLv3) y organizados para poder estudiarlos y
reutilizarlos. **No es un proyecto que compile por sí solo**: estos módulos
dependen del núcleo de TVHeadend (ver [Dependencias](#dependencias)).

> ⚠️ **Sobre "TBS"**: TVHeadend **no tiene un módulo específico para TBS**.
> Las tarjetas TBS (TurboSight, p. ej. TBS 6903/6981) son tarjetas DVB normales
> que el kernel expone con la **API DVB de Linux**, y se manejan con el mismo
> driver genérico `linuxdvb` que cualquier otra tarjeta DVB-C/S/T/ATSC.
> Por eso "TBS" y "DVB" son aquí el mismo módulo.

## Contenido

```
tvh-dvb-kit/
├── README.md
├── dvb/                  ← sintonizar + demultiplexar (TBS/DVB)
│   ├── linuxdvb/         ← driver del sintonizador vía API DVB de Linux
│   ├── mpegts/           ← demux MPEG-TS + tablas PSI/SI (PAT/PMT/SDT/NIT/EIT)
│   ├── mpegts.c / mpegts.h   ← capa MPEG-TS (input)
│   └── input.c / input.h     ← super-clases genéricas de entrada
├── epg/                  ← guía de programación (EPG)
│   ├── epg.c / epg.h / epgdb.c
│   ├── epggrab.c / epggrab.h
│   └── epggrab/          ← grabbers: EIT (por aire), XMLTV, OpenTV, PSIP…
├── descrambler/          ← acceso condicional (CA / descrambling)
├── muxer/                ← salida / muxing (pass, Matroska, libav)
├── transcoding/          ← transcodificación (SW + HW) y wrapper libav
├── salida/               ← pipeline de salida: streaming, profiles, esstream/esfilter, packet
└── m3u/                  ← parseo de listas M3U/M3U8
    └── m3u.c / m3u.h
```

## Módulo `dvb/` — TBS/DVB (sintonizar y demultiplexar)

| Carpeta / archivo | Función |
|---|---|
| `linuxdvb/linuxdvb.c` | Punto de entrada del driver Linux DVB: detecta adaptadores en `/dev/dvb/` |
| `linuxdvb/linuxdvb_adapter.c` | Un adaptador físico (`adapterN`) con sus frontends y demuxers |
| `linuxdvb/linuxdvb_frontend.c` | **Sintonización real** (ioctls `FE_SET_PROPERTY`, `FE_READ_STATUS`) y monitor de señal |
| `linuxdvb/linuxdvb_lnb.c` | Configuración LNB/DiSEqC (satélite) |
| `linuxdvb/linuxdvb_satconf.c`, `linuxdvb_rotor.c`, `linuxdvb_switch.c` | Config de satélite, rotor, conmutadores |
| `linuxdvb/linuxdvb_ca.c`, `linuxdvb_ddci.c`, `linuxdvb_en50494.c` | Acceso condicional / CI / CAM |
| `linuxdvb/linuxdvb_private.h`, `linuxdvb.h` | Estructuras internas y API del driver |
| `mpegts/mpegts_input.c` | `mpegts_input_t`: el sintonizador como objeto (hilos de entrada, colas) |
| `mpegts/mpegts_mux.c`, `mpegts_mux_dvb.c`, `mpegts_mux_sched.c` | Un *multiplex* (frecuencia/transpondedor) y su planificación |
| `mpegts/mpegts_network.c`, `mpegts_network_dvb.c`, `mpegts_network_scan.c` | Redes DVB y **escaneo de frecuencias** |
| `mpegts/mpegts_service.c` | Un *servicio* (canal) dentro del mux |
| `mpegts/mpegts_table.c`, `dvb_psi.c`, `dvb_psi_lib.c` | Reensamblado de secciones PSI/SI y **parseo de tablas** |
| `mpegts/dvb_psi_pmt.c` | Parseo de la PMT (qué PIDs son vídeo/audio/subtítulos) |
| `mpegts/dvb_charset.c` + `dvb_charset_tables.h` | Decodificación de texto DVB (ISO 6937, 8859-*, etc.) |
| `mpegts/dvb.h` | **Constantes DVB**: PIDs (PAT=0x00, SDT=0x11, EIT=0x12…), IDs de tablas, descriptores, enums de modulación/FEC |
| `mpegts/tsdemux.c` | Demultiplexor de paquetes MPEG-TS (188 bytes) por PID |
| `mpegts/scanfile.c` | Ficheros de scan (frecuencias predefinidas DVB) |
| `mpegts.c` / `mpegts.h` | Registro e inicialización de la capa MPEG-TS (`mpegts_init`) |
| `input.c` / `input.h` | Super-clases genéricas `tvh_input`, `tvh_input_instance`, `tvh_hardware` |

## Módulo `epg/` — EPG

| Archivo | Función |
|---|---|
| `epg.c` / `epg.h` | Modelo de EPG en memoria: `epg_broadcast`, `epg_event`, etc. |
| `epgdb.c` | Persistencia de la EPG en disco (`epgdb.v2`) |
| `epggrab.c` / `epggrab.h` | Orquestador de grabbers de EPG |
| `epggrab/channel.c` | Vinculación de canales con sus fuentes EPG |
| `epggrab/otamux.c` | Multiplexado OTA (decide qué transpondedor sintonizar para coger EIT) |
| `epggrab/module.c` | Clase base `epggrab_module_t` de un grabber |
| `epggrab/module/eit.c` | **Grabber EIT por aire**: parsea las tablas EIT (eventos now/next y schedule) |
| `epggrab/module/xmltv.c` | Grabber XMLTV |
| `epggrab/module/opentv.c`, `psip.c` | Grabbers OpenTV (Sky) y PSIP (ATSC) |
| `epggrab/support/freesat_huffman.c` | Decodificador Huffman de Freesat |

El parseo EIT concreto (descriptores `short_event` 0x4D, `extended_event` 0x4E)
está en `epggrab/module/eit.c`; el cálculo de horas a partir del formato
MJD+BCD de DVB está en `dvb/mpegts/dvb_support.c` (`dvb_convert_date`).

## Módulo `m3u/` — M3U/M3U8

| Archivo | Función |
|---|---|
| `m3u.c` / `m3u.h` | **Parsear** listas M3U/M3U8 (`parse_m3u`) — es el lado *entrada* (IPTV) |

⚠️ La **generación** de listas M3U (el lado *salida*, `/playlist`, `page_m3u`)
**no vive en un módulo aparte**: está embebida en el servidor web
`src/webui/webui.c` (funciones `http_m3u_playlist_add`, `page_m3u`,
`http_channel_list_playlist`). Como depende de todo el HTTP, aquí no se copia;
la herramienta `dvb2m3u8` (entregable hermano) incluye su propio generador M3U8
mínimo basado en el mismo formato (`#EXTM3U` + `#EXTINF`).

## Módulo `descrambler/` — acceso condicional (CA)

| Archivo | Función |
|---|---|
| `descrambler.c` / `descrambler.h` | Capa central `th_descrambler`: colas de ECM, gestión de claves CW |
| `caclient.c` / `caclient.h` | Cliente genérico de acceso condicional (tarjeta/servidor) |
| `cwc.c` | Protocolo **newcamd** (NewCamD) |
| `cccam.c` | Protocolo **CCcam** |
| `constcw.c` | Claves constantes (**const CW**) |
| `cclient.c` / `cclient.h` | Cliente de ECM/CAPMT hacia el servidor |
| `capmt.c` | Mensajes **CAPMT** (asigna PMT/ECM al descrambler) |
| `caid.c` / `caid.h` | Gestión de **CAIDs** (sistemas de acceso) |
| `emm_reass.c` / `emm_reass.h` | Reensamblado de **EMM** (gestión de derechos) |
| `dvbcam.c` / `dvbcam.h` | **CAM** vía interfaz común (CI) |
| `tvhcsa.c` / `tvhcsa.h` | **Descrambler CSA** (Common Scrambling Algorithm) |
| `tsdebugcw.c` | Depuración de TS con claves CW |

Depende de `service` (campo `s_dvb_caid`), de `mpegts` (el mismo kit) y de
`streaming` (la información de descrambling viaja con los paquetes).

## Módulo `muxer/` — salida (muxing)

| Archivo | Función |
|---|---|
| `muxer.c` / `muxer.h` | Clase base `muxer_t`: recibe paquetes y los escribe al destino |
| `muxer_pass.c` / `muxer_pass.h` | **Passthrough** de MPEG-TS (sin re-mux) |
| `muxer_mkv.c` / `muxer_mkv.h` | Contenedor **Matroska** (MKV), escrito a mano |
| `ebml.c` / `ebml.h` | Escritor **EBML** (formato de Matroska) |
| `muxer_libav.c` / `muxer_libav.h` | Contenedores vía **FFmpeg/libav** (mp4, mpegts, etc.) |
| `muxer_audioes.c` / `muxer_audioes.h` | Muxing de audio elemental |

El muxer lo **elige el perfil de streaming** (`profile` en `salida/`).

## Módulo `transcoding/` — transcodificación

| Carpeta / archivo | Función |
|---|---|
| `transcode.h` | API pública del transcodificador |
| `transcode/transcoder.c` | Núcleo: elige códec, resolución, bitrate |
| `transcode/video.c`, `audio.c`, `stream.c` | Transcodificación de vídeo y audio |
| `transcode/context.c`, `helpers.c` | Contextos libav y helpers |
| `transcode/hwaccels/*` | Aceleración por hardware (VAAPI…) |
| `codec/module.c`, `codec.c` | Registro y base de los códecs |
| `codec/codecs/*` | Códecs: aac, mpeg2video, mp2, flac, vorbis… |
| `codec/codecs/libs/*` | Encoders externos: libx26x (x264/x265), NVENC, VAAPI, OMX, libopus, libvpx, libfdk_aac |
| `codec/profile*.c` | Perfiles de transcodificación (vídeo/audio) |
| `codec/vainfo.c` / `vainfo.h` | Detección de capacidades VAAPI |
| `memutils.c` / `memutils.h` | Gestión de buffers AVFrame |
| `libav.c` / `libav.h` | **Wrapper de FFmpeg/libav** (compartido con `muxer/muxer_libav`) |

Requiere **libav/FFmpeg** instalado.

## Soporte `salida/` — pipeline de salida (compartido por muxer y transcoding)

| Archivo | Función |
|---|---|
| `streaming.c` / `streaming.h` | "Plumbing": conecta entrada → procesadores → destinos (colas de mensajes) |
| `profile.c` / `profile.h` | **Perfiles de streaming** (`pass`, `matroska`, `transcode`): eligen el muxer |
| `esstream.c` / `esstream.h` | Streams elementales (vídeo/audio/subtítulos) |
| `esfilter.c` / `esfilter.h` | Filtros de streams elementales |
| `packet.c` / `packet.h` | Paquete elemental `th_pkt_t` (PTS/DTS/duration) |

## Dependencias (para que estos módulos compilen)

Estos archivos **no son independientes**. Para volver a enlazarlos necesitas el
núcleo de TVHeadend, que *no* está en este kit:

- **`idnode` / `prop` / `proplib`** (`src/idnode.[ch]`, `prop.c`, `proplib.c`) — el sistema de clases/objetos. Casi todo (`mpegts_*`, `epg_*`, `muxer_*`, perfiles) es un `idnode`.
- **`htsmsg`** (`htsmsg*.c`) — formato de mensajes y serialización (binario/JSON/XML).
- **`settings` / `config`** (`settings.c`, `config.c`) — persistencia de configuración.
- **`tvhlog`, `tvh_thread`, `tvhtime`, `tvhpoll`, `atomic`, `queue.h`, `redblack.h`, `sbuf.h`** — infraestructura (logs, hilos, temporizadores, colas).
- **`global_lock` / `tasklet_lock`** — el modelo de concurrencia de TVHeadend.
- Para `linuxdvb`: `mpegts` (el mismo kit), `service`, `subscriptions`, `descrambler`, `profile`, `streaming`.
- Para `descrambler`: `service`, `mpegts`, `streaming`, `config`.
- Para `muxer` / `transcoding`: `salida/` (streaming, profile, esstream, esfilter, packet) y **libav/FFmpeg**.

En resumen: el valor de este kit es **estudiar y reutilizar la lógica**
(parseo PSI/SI, EIT, sintonización), no compilarlo suelto. Para una utilidad
**compilable e independiente**, usa `../dvb2m3u8/`.

## Licencia

Todo el código es © sus autores originales y está bajo **GPLv3**. Reutilízalo
bajo los términos de esa licencia.
