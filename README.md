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
   servicio de PAT. La salida es **un servicio**, no todos los canales del mux.
3. Revisar `/var/lib/dvb-panelscan/stream.log` y los logs de OSCam. El cliente
   completa login + solicitud de datos de tarjeta; `CW actualizada` indica una
   respuesta ECM válida, no una comprobación visual del vídeo.
4. Confirmar CAID, lector activo y autorización de la cuenta. En simulcrypt se
   selecciona el primer descriptor CA: la selección por CAID/proveedor y la
   selección por proveedor siguen pendientes. La reconexión TCP/login usa espera
   progresiva (1–30 s) y descarta ECM en cola después de un fallo de transporte. No se implementan EMM ni renovación de derechos de tarjeta.

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
