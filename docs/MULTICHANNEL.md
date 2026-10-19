# Motor multicanal 0.6.0

## Topología implementada

```text
adapter 0 -> captura DVB --mux-only -> ring mux acotado (32 MiB)
                                      |-> worker --stdin SID A -> cola salida -> clientes A
                                      |-> worker --stdin SID B -> cola salida -> clientes B
adapter 1 -> captura DVB --mux-only -> ring mux acotado (32 MiB)
                                      |-> worker --stdin SID C -> cola salida -> clientes C

workers A/B/C -> sockets UNIX privados -> broker Newcamd -> una conexión TCP/login
                                          | pendientes por secuencia
                                          | respuestas fuera de orden
                                          | timeout/NOK individual
```

El panel inicia una captura por adaptador y sintonía, no una por SID. Los workers
no abren frontend/demux/DVR. El multiplex se recibe una vez y se reparte mediante
pipes a los servicios activos. Cada worker conserva su propio demux, CSA, claves,
cola y PAT/PMT de salida. No hay una nueva sintonización al abrir otro servicio del
mismo mux. Una sintonía diferente o un escaneo en un adaptador ocupado devuelve
409; no se reasigna un tuner ni se presupone que sus conexiones de antena sean
intercambiables.

La identidad del mux incluye tipo, frecuencia, LO, polarización, symbol rate y
adaptador, pero no el timeout de adquisición. La del canal sigue siendo TP:SID.
Dos IDs con la misma sintonía pueden compartir captura. El SID sin TP solo se
acepta cuando no es ambiguo.

## Suscripciones y ciclo de vida

- Varias peticiones del mismo TP:SID comparten worker y salida.
- Otro SID del mismo mux crea otro worker, conservando captura y broker.
- Cada feeder escribe únicamente al pipe de su worker, en su propio hilo: un
  servicio parado o lento no bloquea el lector del mux ni los demás feeders.
- El ring del mux es de 32 MiB; un feeder rezagado termina su worker con un error
  explícito. No recibe silenciosamente paquetes salteados. La salida por servicio
  conserva hasta 8 MiB y desconecta al suscriptor rezagado.
- Una sesión bajo demanda termina al salir su último cliente del panel. Una
  sesión manual permanece fijada hasta detenerla. Los clientes directos no se
  cuentan en este refcount: hay que iniciar manualmente para usar ese puerto.
- Detener un canal no libera la captura si quedan otros servicios. Al salir el
  último canal se cierra captura. El broker se cierra al salir su último servicio
  en cualquier adaptador. Se comprueban también procesos muertos antes de iniciar.
- Usar puerto HTTP **0** para las URLs del panel (nuevo valor del formulario).
  Si se necesitan salidas directas, cada canal debe usar un puerto distinto.

## Acceso condicional

Una configuración Newcamd idéntica (binario, endpoint y credenciales) comparte un
broker entre adaptadores. Una configuración distinta crea otro broker. Cambiar
la configuración de un canal ya activo requiere detenerlo y abrirlo de nuevo.

El broker mantiene hasta **256 workers** y múltiples ECM pendientes en la única
conexión upstream. Cada petición conserva SID/CAID/proveedor y obtiene una
secuencia propia. Las CW se devuelven solamente al worker correspondiente, aunque
lleguen fuera de orden. No se espera sincrónicamente una CW antes de enviar el
ECM de otro servicio. Las retransmisiones idénticas del mismo worker pueden
reutilizar una respuesta positiva durante un máximo de un segundo; nunca se
comparte esa caché por SID entre muxes distintos.

- Deadline por ECM: 5000 ms, frente a 1500 ms en 0.5.1.
- `result=-3`: deadline del broker; mantiene la conexión compartida y descarta
  respuestas tardías de esa secuencia. No fuerza login de los demás canales.
- `result=-2`: respuesta negativa; solo afecta al solicitante.
- `result=-1`: fallo de transporte/protocolo. Si falla el upstream, se invalidan
  sus peticiones pendientes y se reconecta bajo demanda. Una reconexión/login
  real puede afectar a todos los servicios de esa configuración.
- El worker invalida sus indicadores de CW ante errores; no se mantiene una
  clave antigua indefinidamente como supuesto arreglo. Su cola de espera ahora
  tiene 65536 paquetes (12.32 MB decimales), siempre acotada y con descartes
  contados si se llena. No es un búfer de duración garantizada ni añade una
  espera fija: se drena al disponer de claves.
- Keepalive periódico de 30 segundos en la conexión compartida.

Los sockets UNIX se crean dentro de un directorio temporal 0700 y con modo 0600,
no escuchan en TCP público y desaparecen al cerrar el broker. Las credenciales
solo se pasan al proceso del broker; siguen siendo visibles al administrador en
argv. Ni ECM ni CW se imprimen en los logs.

## Diagnóstico

`/api/stream/status` conserva `sessions`, con `pid` del worker, `mux_pid`, `ca_pid`,
`ca_running`, `mux_queue_bytes` e `input_error`. Mismo adaptador/mux debe mostrar
el mismo `mux_pid`; configuración CA compartida, el mismo `ca_pid`.

Registros privados en el directorio de datos:

- `stream-<adapter>-<SID>.log`: demux, tiempos/resultados ECM y estadísticas TS.
- `mux-<adapter>.log`: sintonización, lock y errores del lector DVB.
- `ca.log`: conexión upstream y deadlines del broker. Varias configuraciones
  escriben aquí; no se incluyen datos sensibles.

Los tres son accesibles desde Streaming o desde
`/api/stream/log?channel=<TP:SID>&scope=service|mux|ca`, sin aceptar rutas arbitrarias.
TEI/CC siguen correspondiendo al multiplex completo que recibe cada worker, no a
una medición de señal exclusiva del canal.

## Prueba en el servidor TBS

1. Actualizar conjuntamente C y panel con el servicio detenido. Conservar datos y
   overrides de systemd. Cerrar reproductores antiguos y recargar con Ctrl+F5.
2. Abrir `.../stream/channel/ad648bb5:672?profile=pass` y
   `.../stream/channel/ad648bb5:643?profile=pass` en dos reproductores, **sin cerrar
   el primero**. Usar SIDs realmente presentes y autorizados en ese mux.
3. Comprobar dos workers y un solo `mux_pid` / `ca_pid` en estado. Detener 672:
   643 debe seguir reproduciéndose, con el mismo `mux_pid`.
4. Abrir un canal de otro transpondedor asignado a otro adaptador: otro `mux_pid`,
   mismo `ca_pid` si usa la misma configuración.
5. Reproducir SD+HD simultáneamente al menos dos minutos y revisar incrementos de
   `service_drops`, TEI/CC, deadlines y clientes lentos. El timeout anterior de
   1500 ms ya no debe aparecer como límite del cliente nuevo.
6. Verificar visualmente imagen/audio y, si es posible, registrar errores de
   decodificación. TS recibido o una CW no certifican vídeo correcto.

## Validación automatizada y límites

Las pruebas Python cubren refcounts, inicios concurrentes, dos SID compartiendo
captura, dos adaptadores, parada independiente, proceso muerto, limpieza tras
fallos y aislamiento de un worker detenido. La integración sustituye **solo**
la captura DVB por un multiplex sintético de dos servicios: ejecuta el broker y
los workers C reales, comprueba HTTP, PIDs separados y PAT reescrita con CRC.

`tests/test_broker.c` usa upstream cifrado simulado para verificar peticiones
simultáneas antes de responder, CW fuera de orden, deduplicación, deadline sin
bloquear otro canal, rechazo negativo y descarte de una CW tardía sin reconectar.
Las regresiones C de CSA/paridades, demux y transporte se ejecutan con ASan/UBSan.

Esto no convierte el proyecto en una copia completa de Tvheadend. El reparto
copia el TS completo a cada worker, en lugar de un demux central por PID: aumenta
el trabajo con muchos servicios y debe medirse en hardware. No hay benchmark ni
validación DVB real en este entorno. No se descifran todos los canales al
escanear: se activan bajo demanda o mediante inicio manual.

Siguen pendientes la aplicación temporal completa de generaciones CW/marcadores,
la comprobación inicial de paridad de Tvheadend, EMM y selección avanzada de CAID
/simulcrypt. No hay HLS, transcodificación ni reparación artificial de PCR/PTS.
Los límites/permisos del servidor CA siguen aplicándose. El panel sigue sin
autenticación y los logs sin rotación: utilizar una LAN confiable o proxy seguro.
