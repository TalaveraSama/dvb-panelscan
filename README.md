# dvb-panelscan

Conjunto de herramientas en C y Python para **sintonizar tarjetas TBS/DVB-S2,
escaneo de canales, descrambling (acceso condicional newcamd) y salida UDP/HTTP
para headends IP→analógico**.

Desarrollado reutilizando lógica de [TVHeadend](https://github.com/tvheadend/tvheadend)
(protocolo newcamd `cwc.c`, descrambler CSA `tvhcsa.c`, demux/PSI `dvb_psi`).

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
