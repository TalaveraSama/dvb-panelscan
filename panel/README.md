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
| **Newcamd** | Servidor + puerto + usuario + contraseña + DES key (14 bytes hex). Se guarda en `newcamd.json`. |
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

- El panel debe correr como **root** (acceso a `/dev/dvb` y al LNB).
- El transponder se define por: **tipo, frecuencia de satélite (MHz), LNB LO, SR, pol, adaptador**.
  La frecuencia de satélite se convierte a FI con el LNB LO (9750 = universal Ku baja).
- El escaneo usa `dvb2m3u8` (que ya incluye el fix del voltaje del LNB y el filtro
  demux "full TS" que el driver TBSECP3 necesita).
- El descifrado real necesita el **lector de smartcard** conectado y OSCam estable.

## Licencia

GPLv3.
