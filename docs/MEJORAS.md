# Revisión e integración del código recibido · 0.19.0

Base: 0.18.1. Se revisaron los 18 archivos completos de `CODIGO-A-APLICAR.md`.
No son un parche contra esa base y **no se sustituyeron indiscriminadamente**:
el servidor/panel recibido no conserva varias funciones y protecciones actuales.

## Lo integrado y adaptado

| Área recibida | Integración en la versión actual |
| --- | --- |
| `tbsstream/csa.h`, `csa.c` | Disponibilidad por paridad, reset, estadísticas, pendientes y API para lista de paquetes. Se conservan batching nativo y validación TS, y el payload completo. Sin clave no se modifica el paquete ni su indicador de cifrado. |
| `tbsstream/newcamd.h`, `newcamd.c` | API compatible por defecto con 525, variante 524 explícita, keepalive, metadatos CARD_DATA con límites, longitud lógica sin padding, CW de exactamente 16 bytes y error explícito para formato de 32 bytes. Se mantienen secuencia/tabla y multiplexación del broker actual. |
| `tbsstream/main.c` | Selección `--newcamd-proto 525\|524` y reset del estado de claves en invalidación/cambio de CA. No se reemplazan captura compartida, selección CA, colas, snapshots CW ni salida regulada actuales. |
| `tbsstream/Makefile` | Compila herramienta local, `make test`, dependencias de cabeceras y soporte de variables estándar de compilación/enlace e instalación `PREFIX`/`DESTDIR`. Se mantienen libdvbcsa/OpenSSL como dependencias obligatorias. |
| `tbsstream/tools/ncdiag.c` | Se adapta como herramienta **solo local**; no se importa el cliente de pruebas en red recibido. Instalación con nombre `dvb-ncdiag` para evitar colisiones. |
| Cuatro archivos de pruebas recibidos | Se adaptan a `tests/` existente, no se reemplazan sus regresiones. Login/tarjeta, 524/525, keepalive, padding, secuencia/tabla, deadline y CW no soportadas, broker multicanal y CSA con payload residual. La longitud ECM ya se comprueba con demux real; no se incorpora el chequeo textual redundante ni la afirmación universal de CRC. |
| `panel/server.py`, `panel/static/{index.html,app.js,style.css}` | Nueva pestaña Diagnóstico CA dentro del panel actual. Protocolo por perfil y estado del broker existente. Se conservan autenticación, CSRF, perfiles por canal/transponder, EPG/UTC, IPTV y eventos en vivo. |
| `docs/MEJORAS.md`, `README.md`, `.gitignore` | Documentación reescrita para describir lo realmente integrado y sus límites. Se ignora el binario local generado, no las fuentes ni pruebas. |

Además, el paquete instala la herramienta y CI comprueba el archivo instalado y
las nuevas pruebas. No se cambió la política de registros: no se guardan eventos
en disco y la suscripción de eventos existe solo mientras se abre Logs/Eventos.
El diagnóstico conserva únicamente el último estado técnico no secreto en RAM,
igual que la telemetría ECM; no conserva un transcript ni habilita otra suscripción.
Los contadores CSA están disponibles en la API C, no se presenta un nuevo contador
web como si fuera una validación del vídeo real.

## Cambios recibidos que NO se aplican literalmente

1. **Recortar el payload CSA a múltiplos de 8.** DVB-CSA también procesa los bytes
   residuales. El test recibido cifraba y descifraba la misma longitud recortada,
   por lo que podía pasar sin validar el payload real completo. Nuestra regresión
   cifra los 175 bytes de un payload con adaptación, demuestra que descifrar solo
   168 es incorrecto y verifica los 175 con el wrapper. También se cubren múltiples
   clusters, ambas paridades y longitudes de 1–184 bytes.
2. **Ignorar la secuencia de respuesta Newcamd.** Permitiría instalar una CW
   ajena/antigua. Se mantienen secuencia y tabla/paridad, incluso en el broker.
   Ignorar respuestas ajenas no renueva el deadline del cliente directo.
3. **Usar el tamaño DES con padding cuando la longitud declarada es imposible.**
   Se rechaza el mensaje. Padding/checksum no son bytes de una CW.
4. **Aceptar 32 bytes como AES y copiar mitades de 8 bytes.** El motor es DVB-CSA,
   no AES/CSA3. Se devuelve -4 sin copiar ninguna clave y se muestra error de
   formato no soportado. No se confunde transporte 3DES Newcamd con AES del TS.
5. **Diagnóstico que recibe secretos por argv, imprime CW o abre otra conexión.**
   El panel no hace eso. No admite host, cuenta, ECM ni comandos suministrados por
   la petición. La herramienta nueva no tiene funciones de red. El botón ya
   existente de prueba de puerto conserva la reutilización del broker activo.
6. **Reemplazar el servidor y crear `stream.log`.** Se conserva el servidor actual
   con autenticación/CSRF, mux/broker compartidos, reconexión de 10 segundos,
   EPG/UTC, IPTV y registros exclusivamente en vivo. No se restaura escritura de logs.
7. **Presentar como nuevas correcciones ECM completo, PCR o keepalive/reconexión.**
   Ya existían en la base. ECM se envía con `section_length + 3`; no se afirma
   que todos los ECM privados incluyan CRC. No se reescriben PCR/PTS/DTS.

## Uso

- **Acceso condicional → Protocolo**: dejar **525**, salvo que el servidor requiera
  expresamente **524**. Al guardar se detienen los canales del perfil, como antes.
  Configuraciones anteriores sin `proto` siguen usando 525. No hay autodetección.
- El protocolo forma parte de la identidad del broker. Perfiles equivalentes con
  el mismo protocolo comparten broker; 524 y 525 son configuraciones distintas.
- **Diagnóstico CA → Actualizar estado**: observa brokers/canales administrados,
  PIDs, último estado de conexión, CAID anunciado, número de proveedores y ECM.
  `connect_count` cuenta handshakes exitosos desde el inicio de ese broker, no
  sockets externos ni todos los intentos TCP. No muestra host/cuenta/serial/CW/ECM.
- **Autoprueba CSA · sin red**: prueba datos sintéticos locales, no suscripción.
  `POST /api/diag/selftest` necesita sesión administrativa y CSRF, cuerpo `{}`,
  permite una ejecución simultánea y tiene límite de cinco segundos. GET
  `/api/diag` también exige sesión administrativa; el token de reproducción no sirve.

```sh
make -C tbsstream test
# Con el paquete instalado:
dvb-ncdiag --selftest --json
```

Las dependencias de compilación siguen siendo compilador C, libdvbcsa-dev y
libssl-dev; no se descargan automáticamente bibliotecas. Para rutas no estándar
usar CPPFLAGS/LDFLAGS/LIBS. Python/HTTP y DOM se prueban en `tests/`, además de C
con AddressSanitizer/UndefinedBehaviorSanitizer en CI.

## Evidencia y límites

- CARD_DATA: se contrasta la disposición con [Tvheadend cwc.c, revisión fijada](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cwc.c):
  CAID en bytes 4–5 del mensaje, contador en 14, proveedores desde 15 en registros
  de 11 bytes. El número de tarjeta de bytes 6–13 no se almacena ni se expone.
- 524/525: cabeceras de 8/12 bytes y SID 6–7/4–5, contrastados con
  [OSCam module-newcamd.c](https://github.com/gfto/oscam/blob/0f07ad0cd71ef71d4db39bb73bf53e139f004963/module-newcamd.c).
  Hay pruebas de login TCP local, tarjeta y broker para ambos protocolos;
  no son una prueba contra el servidor Ncam real del usuario.
- CSA se compara entre las API escalar y bitslice de **la misma libdvbcsa**.
  No equivale a una implementación independiente ni a validar la señal recibida.
- Este diagnóstico no identifica otros programas externos que usen la misma
  cuenta ni demuestra que el servidor haya cerrado una sesión TCP anterior.
- No se ha accedido al VPS ni validado reproducción real de esta versión.
  No se promete solucionar con estas pruebas el pegón inicial, CW antiguas,
  rechazos CAID/proveedor o la calidad de la señal. No se añade soporte AES,
  CSA3, PowerVu ni modo siempre activo.
