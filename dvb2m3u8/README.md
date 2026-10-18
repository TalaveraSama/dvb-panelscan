# dvb2m3u8 — escáner DVB/TBS → lista M3U8

Herramienta mínima e independiente en C que **sintoniza un adaptador DVB**
(incluidas tarjetas **TBS**, que usan la API DVB de Linux), **demultiplexa** el
flujo MPEG-TS, extrae las tablas PSI/SI y genera un **M3U8** con los
canales/servicios encontrados.

Reimplementa en un solo fichero la lógica esencial de TVHeadend
(`dvb.h`, `dvb_psi.c`, `epggrab/module/eit.c`), sin depender de su núcleo.
Solo depende de libc y de los headers DVB del kernel.

## Requisitos

- **Linux** con un adaptador DVB expuesto en `/dev/dvb/adapterN/` (tarjetas
  TBS, Digital Devices, USB DVB-T/C/S, etc.).
- Kernel con headers `linux/dvb/*` (cualquier kernel moderno).
- `gcc` + `make`.

## Compilar

```sh
make            # genera ./dvb2m3u8
```

## Uso

```sh
sudo ./dvb2m3u8 -t dvbt --freq-mhz 674 -o canales.m3u8
```

> `--freq` es en **Hz**; `--freq-mhz` es en **MHz**.

### DVB-T / T2 (terrestre)

```sh
# mux a 674 MHz, ancho de banda 8 MHz
sudo ./dvb2m3u8 -t dvbt  --freq-mhz 674 -o canales.m3u8 -v

# DVB-T2
sudo ./dvb2m3u8 -t dvbt2 --freq 570000000 -o canales.m3u8
```

### DVB-C (cable)

```sh
sudo ./dvb2m3u8 -t dvbc --freq 442000000 --symbol-rate 6900000 --fec AUTO -o canales.m3u8
```

### DVB-S / S2 (satélite, TBS por ejemplo)

```sh
# frecuencia en Hz ya convertida por el LNB; 13 V / 18 H
sudo ./dvb2m3u8 -t dvbs --freq 11836000000 --symbol-rate 27500000 --pol H -o canales.m3u8
```

### Opciones principales

| Opción | Descripción |
|---|---|
| `-a, --adapter N` | Adaptador `/dev/dvb/adapterN` (def 0) |
| `-F, --frontend N` | Frontend N (def 0) |
| `-t, --type T` | `dvbt`, `dvbt2`, `dvbc`, `dvbs`, `dvbs2` |
| `--freq Hz` / `--freq-mhz MHz` | Frecuencia (obligatoria) |
| `--symbol-rate R` | Symbol rate (obligatorio en DVB-C/S) |
| `--bandwidth Hz` | Ancho de banda DVB-T (def 8 MHz) |
| `--fec / --fec-hp / --fec-lp` | FEC: `AUTO,1_2,2_3,3_4,5_6,7_8,8_9,9_10` |
| `--pol H\|V\|L\|R` | Polarización satélite (def H) |
| `-T, --timeout S` | Segundos de escaneo (def 15) |
| `--no-epg` | No extraer now/next de la EPG (EIT) |
| `-o, --output F` | Fichero de salida (def stdout) |
| `--base-url URL` | Prefijo URL de cada canal |
| `-v, --verbose` | Log por stderr |

## Qué hace internamente

1. **Sintoniza** con `FE_SET_PROPERTY` (propiedades DTV_*), espera
   `FE_HAS_LOCK`.
2. **Lee el TS completo** desde `/dev/dvb/adapterN/dvr0` y realinea paquetes
   de 188 bytes (sync `0x47`).
3. **Reensambla secciones PSI/SI** por PID (con verificación CRC-32 MPEG):
   - **PAT** (0x00) → descubre los PID de las PMT.
   - **PMT** (0x02) → PCR_PID.
   - **SDT** (0x11) → `service_id`, nombre del canal y proveedor
     (descriptor 0x48).
   - **NIT** (0x10) → nombre de la red (descriptor 0x40).
   - **EIT** (0x12, present/following 0x4E/0x4F) → now/next (descriptor 0x4D).
4. **Decodifica texto** DVB (ISO 6937 con diacríticos, o UTF-8 si es válido).
5. **Escribe M3U8** con `#EXTM3U` + `#EXTINF` (tvg-id, tvg-name, group-title).

## Formato de salida

```
#EXTM3U
#EXTINF:-1 tvg-id="101" tvg-name="Canal 24h" group-title="RTVE",Canal 24h
dvb://adapter0/674000000/101
```

Con `--base-url http://192.168.1.10:9981/stream/service/` la URL queda
`http://192.168.1.10:9981/stream/service/101`, lista para apuntar a un
servidor de streaming (por ejemplo el propio TVHeadend).

## Limitaciones (a propósito, por ser "mínima")

- Un solo mux/frecuencia por ejecución (no escanea todas las frecuencias).
- DVB-S: se pasa la frecuencia ya convertida por el LNB; no hay DiSEqC/rotor
  ni tono 22 kHz (eso está en `linuxdvb_lnb.c` del kit).
- La decodificación de texto ISO 6937 es *best-effort* (cubre español y
  Europa occidental).
- No descifra canales (sin acceso condicional); eso está en `descrambler/`.

Para el resto de la lógica completa, mira el kit `../tvh-dvb-kit/`.

## Licencia

GPLv3 (reutiliza lógica de TVHeadend).
