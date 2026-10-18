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
4. **newcamd**: conexión + login (DES) → envía cada ECM (sección `0x80/0x81`)
   → recibe la CW (par/impar). Protocolo portado de `cwc.c`.
5. **CSA**: descifra los paquetes con bit de scrambling usando libdvbcsa
   (port de `tvhcsa.c`).
6. **Remux**: genera PAT y PMT **limpios** (sin descriptores CA) y los inyecta
   periódicamente; reenvía los ES descifrados y descarta los ECM.
7. **Salida**: UDP (con `sendto`) y HTTP (ring buffer + servidor mínimo).

## Avisos importantes

- ⚠️ **Sin probar en este entorno** (no hay Linux ni hardware DVB ni servidor
  newcamd aquí). Compílalo en Linux y pruébalo con hardware real.
- La conexión newcamd es **síncrona** (el envío de ECM bloquea ~1s el hilo
  principal); una versión más avanzada movería el CA a un hilo propio.
- El **formato del ECM enviado** (sección sin los 4 bytes de CRC) es la
  convención más común, pero puede variar entre servidores newcamd; si un
  servidor no responde CW, es el primer punto a ajustar en `send_ecm()`.
- DVB-S: la frecuencia se pasa ya convertida por el LNB (sin DiSEqC/rotor).
- Para multicast fuera de la LAN hay que ajustar el TTL del socket
  (`IP_MULTICAST_TTL`) — aquí se deja el valor por defecto (1).

## Licencia

GPLv3 (reutiliza código de TVHeadend: `cwc.c`, `tvhcsa.c`, `crypt_md5`).
