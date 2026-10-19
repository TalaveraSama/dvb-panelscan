# tbsstream — TBS/DVB + newcamd (descrambling) → UDP/HTTP

Herramienta C para Linux que **sintoniza una tarjeta TBS/DVB**, **descifra** el
servicio usando **llaves newcamd** (algoritmo CSA) y **reenvía el TS** por:

1. **UDP** (unicast o multicast) → para alimentar equipos **IP→analógico**
   (moduladores/headends).
2. **HTTP** → stream `TS` + lista `M3U8`.

```
DVB (frontend/dvr0)
  -> demux (PAT/PMT -> PIDs + ECM)
  -> newcamd (ECM -> CW par/impar)
  -> CSA (libdvbcsa, descifra vídeo/audio)
  -> remux (PAT/PMT limpios)
  -> salida: UDP (IP->analógico)  y  HTTP (TS + M3U8)
```

Es un reensamblado del código de TVHeadend: el **protocolo newcamd** está
portado de `src/descrambler/cwc.c`, el **CSA** de `src/descrambler/tvhcsa.c`
(vía libdvbcsa), y la sintonización/demux de `dvb2m3u8`.

## Roles multicanal usados por el panel (0.6.0)

- `--mux-only --stdout`: sintoniza y entrega todo el multiplex, sin CA ni remux.
- `--stdin --sid N --stdout --cam-socket PATH`: procesa un servicio desde stdin,
  sin abrir ningún dispositivo DVB; genera PAT/PMT propias y descifra con su CSA.
- `--ca-broker PATH --newcamd HOST:PORT --deskey ... --user ... --pass ...`:
  endpoint UNIX privado y una conexión Newcamd para varios servicios, con ECM
  pendientes correlacionados por secuencia y deadline individual de 5000 ms.

El panel administra el reparto, las suscripciones y la limpieza. Varias URLs de
un mismo mux comparten captura y broker; los ejemplos CLI monolíticos de abajo
siguen disponibles, pero no deben ejecutarse en paralelo sobre un mismo tuner.
Ver [arquitectura y pruebas](../docs/MULTICHANNEL.md). La cola CW es ahora de
65536 paquetes por servicio; la aplicación temporal completa de CW sigue pendiente.

## Dependencias

- Linux con headers DVB (`linux/dvb/*`).
- **libdvbcsa** (descrambler CSA): `libdvbcsa-dev`.
- **OpenSSL** (DES/MD5 para newcamd): `libssl-dev`.
- `gcc` + `make`.

```sh
sudo apt install libdvbcsa-dev libssl-dev build-essential
```

## Compilar

```sh
make            # genera ./tbsstream
```

## Uso

```sh
sudo ./tbsstream \
  -t dvbs --freq 11836000000 --symbol-rate 27500000 --pol H \
  --newcamd 192.168.1.2:15050 \
  --deskey 000102030405060708090a0b0c0d \
  --user user --pass miclave \
  --udp 239.1.1.1:1234 \
  --udp 192.168.1.100:5000 \
  --http-port 9981
```

- Sintoniza, descifra con newcamd y reenvía el TS por UDP a
  `239.1.1.1:1234` (multicast) y a `192.168.1.100:5000` (unicast).
- Sirve el stream en `http://<ip>:9981/stream.ts` y la lista en
  `http://<ip>:9981/playlist.m3u8`.

### Opciones

| Opción | Descripción |
|---|---|
| `-a, --adapter N` | Adaptador `/dev/dvb/adapterN` (def 0) |
| `-t, --type` | `dvbt`, `dvbt2`, `dvbc`, `dvbs`, `dvbs2` |
| `--freq Hz` / `--freq-mhz MHz` | Frecuencia |
| `--symbol-rate R` | Obligatorio en DVB-C/S |
| `--pol H\|V\|L\|R` | Polarización satélite |
| `-s, --sid N` | Service ID a descifrar (def: primero) |
| `--newcamd host:port` | Servidor newcamd (def 127.0.0.1:15050) |
| `--deskey HEX` | **Clave DES de 14 bytes** (28 hex, admite `:` separadores) |
| `--user`, `--pass` | Credenciales newcamd |
| `--udp ip:port` | Salida UDP (repetible; multicast/unicast) |
| `--http-port N` | Puerto HTTP (0 = desactivar; def 9981) |
| `--m3u8-base URL` | URL base para el M3U8 (def http://127.0.0.1:9981) |
| `-v, --verbose` | Log por stderr |

### Clave DES (`--deskey`)

Igual que TVHeadend (config "Code Word Client → DES key"): **14 bytes en hex**.
Ejemplo: `00:01:02:03:04:05:06:07:08:09:0a:0b:0c:0d`.

## Cómo funciona (internamente)

1. **Sintoniza** con `FE_SET_PROPERTY` y espera `FE_HAS_LOCK`.
2. **Lee el TS** de `dvr0` y realinea paquetes de 188 bytes.
3. **Demux** PAT → PID de la PMT; PMT → PIDs de vídeo/audio/PCR y el
   **ECM PID + CAID** (descriptor CA `0x09`).
4. **newcamd** (asíncrono): un hilo CA dedicado con cola de ECM. Hace el login
   (DES) y envía cada ECM (sección `0x80/0x81`) → recibe la CW (par/impar).
   Protocolo portado de `cwc.c`. El hilo principal **no se bloquea**.
5. **CSA**: descifra los paquetes con bit de scrambling usando libdvbcsa
   (port de `tvhcsa.c`). Los paquetes scrambled que llegan **antes** de tener la
   CW se **guardan en una cola** y se descifran en orden cuando llega la clave
   (espera acotada; no garantiza evitar cortes en los cambios de clave).
6. **Remux**: genera PAT y PMT **limpios** (sin descriptores CA) y los inyecta
   periódicamente; reenvía los ES descifrados y descarta los ECM.
7. **Salida**: UDP (con `sendto`) y HTTP (ring buffer + servidor mínimo).

## Avisos importantes (0.6.0)

- Compilación y regresiones con ASan/UBSan verificadas en Ubuntu mediante
  Actions. Falta validación end-to-end de esta versión con hardware y acceso
  condicional autorizados.
- Un servicio por proceso. La cola guarda en orden todos los paquetes del
  servicio, incluido PCR/audio claros, mientras espera una CW. Su límite es
  65536 paquetes; un desbordamiento se registra y contabiliza.
- CSA descifra en lotes de hasta 64 paquetes disponibles. No se añade espera
  para llenar el lote; se drena al terminar cada lectura y durante pausas DVR.
- No se implementa el seguimiento temporal completo de generaciones de CW
  de Tvheadend. No se garantiza resolver cortes de período criptográfico.
- Los ECM pendientes idénticos se deduplican; `ECM: ... time_ms=... result=...`
  informa latencia/resultado sin imprimir claves. La sección privada se envía
  completa, no se trunca arbitrariamente ni se elude autorización del servidor.
- `TS stats` cada 5 s informa contadores acumulados de TEI y saltos CC del
  mux de entrada, descartes/pendientes de servicio/ECM y trabajo CSA. No
  repara pérdidas ni certifica vídeo correcto por recibir CW.
- Se solicita un buffer demux de 4 MiB si el driver admite el ioctl; de lo
  contrario se registra el error y se conserva su configuración por defecto.
- `--stdout` entrega TS al panel, manteniendo diagnósticos en stderr.
- HTTP entrega TS continuo; su playlist M3U de canales **no es HLS**. Un
  empaquetador HLS externo no corrige claves fuera de período o datos perdidos.
- Para multicast fuera de la LAN hace falta configurar el TTL adecuadamente.


## Licencia

GPLv3 (reutiliza código de TVHeadend: `cwc.c`, `tvhcsa.c`, `crypt_md5`).
