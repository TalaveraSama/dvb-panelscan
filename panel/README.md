# Mini DVB-Scan · Panel completo

Panel web (HTML/JS + Python stdlib, sin dependencias) para administrar el
**sintonizador TBS/DVB-S2**: transponders, escaneo, EPG, listas M3U8,
descifrado newcamd y streaming UDP/HTTP.

## Funciones

| Sección | Qué hace |
|---|---|
| **Adaptadores** | Lista `/dev/dvb/adapterN` y frontends, y si `dvb2m3u8`/`tbsstream` están disponibles. |
| **Transponders** | Agregar / listar / borrar / escanear transponders. Se guardan en `transponders.json`. |
| **Canales y EPG** | Tabla de canales con SID, nombre, proveedor, TV/Radio y **EPG now/next**. Filtro de búsqueda. |
| **M3U8** | Exporta `#EXTM3U` con `tvg-id`, `tvg-name`, `group-title` (acepta `?base_url=`). |
| **Newcamd** | Servidor + puerto + usuario + contraseña + DES key (14 bytes hex). Se guarda en `newcamd.json` con permisos 0600. |
| **Streaming** | Arranca/para `tbsstream` (descifrado newcamd + salida UDP + HTTP). |

## Arrancar

```sh
python3 server.py --host 0.0.0.0 --port 8081
# o como servicio:
systemd-run --unit=dvb-panel --working-directory=$(pwd) /usr/bin/python3 server.py --host 0.0.0.0 --port 8081
```

Abrir **http://<ip>:8081**.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Plataforma, adaptadores, binarios, estado stream |
| GET/POST | `/api/transponders` | Listar / agregar transponder |
| POST | `/api/transponders/<id>/delete` | Borrar transponder |
| POST | `/api/scan` | Escanear (`{"id":"..."}` o parámetros sueltos) |
| GET | `/api/channels` · `/api/epg` | Canales + EPG del último escaneo |
| GET | `/api/playlist.m3u8` | Lista M3U8 |
| GET/POST | `/api/newcamd` | Config newcamd |
| POST | `/api/stream/start` | Iniciar tbsstream (transponder + newcamd + udp + http_port) |
| POST | `/api/stream/stop` | Detener stream |
| GET | `/api/stream/status` | Estado del stream |

## Notas

- El servicio empaquetado usa el usuario `dvb-panel` y el grupo `video`; no necesita root si `/dev/dvb` tiene permisos adecuados.
- El transponder se define por: **tipo, frecuencia de satélite (MHz), LNB LO, SR, pol, adaptador**.
  La frecuencia de satélite se convierte a FI con el LNB LO (9750 = universal Ku baja).
- El escaneo usa `dvb2m3u8` (que ya incluye el fix del voltaje del LNB y el filtro
  demux "full TS" que el driver TBSECP3 necesita).
- El descifrado real necesita el **lector de smartcard** conectado y OSCam estable.

## M3U8 / stream TS (cómo se genera la URL)

La lista M3U8 sigue el formato de TVHeadend (`src/webui/webui.c`). Cada canal genera:

```
#EXTINF:-1 tvg-id="<sid>" tvg-name="<nombre>" group-title="<red>",<nombre>
http://<host>:<puerto>/stream/channelid/<sid>?profile=pass
```

- `<host>:<puerto>` se toma del header `Host` del navegador (o se pasa `?base_url=`).
- `base_url` acepta el placeholder `{sid}`: `http://IP:9981/stream/channelid/{sid}`.
- `?profile=pass` es el perfil "passthrough" (TVHeadend); aquí es decorativo.
- El **TS real** lo sirve `tbsstream` en `/stream.ts` (HTTP) y por UDP
  (`udp://@239.x.x.x:puerto`), que es lo que alimenta el modulador IP→analógico.

## Licencia

GPLv3.

Para instalar en Ubuntu y consultar limitaciones de acceso condicional, ver [README principal](../README.md#release-040--ubuntu). El test TCP no valida login ni descifrado.

## Desde 0.5.0

Las URLs automáticas son `/stream/channel/<transponder_id>:<SID>?profile=pass`.
Reexportar M3U y XMLTV después de actualizar. Varias reproducciones del mismo
canal comparten proceso; otro canal en el mismo tuner o escaneo concurrente se
rechaza. Modo manual y M3U usan el mismo gestor. Registros por servicio
`stream-<adapter>-<SID>.log` visibles en Streaming. El endpoint de estado incluye
`sessions`; **Detener todas** las cierra y desconecta reproductores.
`--stdout` entrega TS del binario al panel sin UDP local. Ver README principal
y notas de versión para límites y migración; los ejemplos SID-only anteriores
siguen disponibles solo si el SID no es ambiguo.
