# Conexiones Newcamd y descifrado: revisión tras 0.18.0

El usuario informa que 0.18.0 no mejora los pegones, incluso empeora, y que Ncam
muestra dos conexiones de la cuenta al reproducir un canal. Se trata como fallo
pendiente: ni los tests ni las releases anteriores validaron su señal real.

## Lo comprobado y lo que no

- El panel crea un broker por configuración Newcamd, no por cliente HTTP. Los
  workers usan sockets Unix y el proceso de captura no necesita Newcamd.
  El broker C mantiene un único puntero upstream y cierra el anterior al fallar.
  Esto no demuestra cuántos procesos existen en el VPS ni cuántas conexiones
  conserva Ncam tras una desconexión. No se ha accedido al VPS.
- `/api/newcamd/test` abría una conexión TCP adicional incondicionalmente. Se
  corrige para no abrirla mientras existe un broker activo de esa configuración.
  El resultado dice explícitamente que no verifica login/CW. Esta apertura era
  transitoria al pulsar el botón; **no explica por sí sola dos sesiones permanentes**.
- La identidad del broker antes incluía la cadena de comando completa (binario y
  representación textual de la DES key). Ahora usa configuración normalizada:
  host sin espacios y sin diferencia de mayúsculas, puerto, usuario, contraseña,
  DES key normalizada. Nombres de perfiles, ruta del binario y mayúsculas/colones
  de la DES key no crean otro broker. No colapsa aliases DNS con IP ni servidores
  distintos; no transforma mayúsculas ni espacios de usuario/contraseña.
- No se elimina el guard CW/paridad ni se altera la asociación de claves a ciegas.
  Los límites de CW repetida, retardo, caducidad y snapshots siguen documentados.

## ¿Cambiar la biblioteca CSA?

Se inspeccionó la fuente de Tvheadend fijada a
`8837853a69aa822b0203eb3909c5912dc831312e`:
https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/tvhcsa.c

Su ruta `ENABLE_DVBCSA` usa `dvbcsa_bs_decrypt` y claves separadas par/impar,
igual que la biblioteca empleada aquí. Esto no hace equivalentes los motores:
la selección CA, períodos de claves, colas y recuperación son lógica aparte.
No existe evidencia de que sustituir la primitiva CSA elimine el fallo reportado.
Se amplían pruebas del wrapper: varios clusters, claves par/impar distintas,
paquetes claros, campos de adaptación, longitudes de payload y bloques parciales,
comparando con datos cifrados mediante la API escalar de libdvbcsa. Es una
comparación de rutas de la misma biblioteca, no validación de la señal ni un
segundo motor independiente. No se incorpora otra biblioteca sin evaluación.

## Diagnóstico en el servidor sin revelar credenciales

`sudo python3 tools/diagnose-ca.py` imprime una instantánea de `/proc`, sin red,
sin detener procesos, sin modificar configuración y sin archivos de registro.
Solo muestra PID, PPID, rol y estados TCP con destinos anonimizados `peer-N`.
No imprime comandos, cuentas, DES keys, contraseñas, IPs, puertos ni URLs.
`service-local-ca` no debería tener TCP Newcamd; `ca-broker` puede tener uno.
Astra/Tvheadend también se muestran como `other-headend` si siguen ejecutándose.

No equiparar procesos con conexiones: un mux, un worker y un broker son tres
procesos pero no tres sesiones Newcamd. No equiparar dos lectores HTTP con dos
sesiones Newcamd. Una instantánea tampoco prueba que ambas pertenezcan a la
misma cuenta o que una entrada de Ncam siga establecida en Linux.

Siguiente paso: cotejar la instantánea en pleno fallo con las dos conexiones de
Ncam (estados/tiempos, sin credenciales). Después identificar duplicación real,
reconexiones, guard CW, pérdidas o reloj de salida antes de reemplazar el motor.
No se debe seguir aumentando retardo ni prometer otra cura sin esos datos.

El entorno del agente no dispone de acceso de red SSH al VPS. No se solicita ni
almacena contraseña; se requiere ejecutar la comprobación localmente o habilitar
un entorno autorizado que sí tenga acceso. La investigación inicial no desplegó cambios. El ciclo de reconexión HTTP se
modifica posteriormente en 0.18.1, descrito a continuación; no se instala
automáticamente en el VPS.


## Reutilización ante reconexiones HTTP (0.18.1)

El usuario observó que al pegarse el reproductor solicita de nuevo el canal y
aparecen dos llamados. El código anterior liberaba el último consumidor cerrando
inmediatamente worker, mux y broker sin otra sesión referenciándolos. Un nuevo
GET después de ese cierre iniciaba todo otra vez. El registro Ncam muestra dos
sesiones y reconexiones, pero no permite atribuir cada una a un PID ni demuestra
que todos sus rechazos/pegones procedan de esta ruta.

El panel activa ahora 10 segundos de gracia para una sesión bajo demanda con TS
previo y proceso vivo. Mantiene recepción/ECM durante ese tiempo. Un GET del mismo
TP:SID reutiliza el objeto y los PIDs; cancela su caducidad y se incorpora al final
actual del ring, sin reenviar el atraso. No se instala otra conexión upstream por
ese GET. Un fallo de transporte del propio broker aún puede causar reconexión.

La caducidad usa un timer daemon y token único, protegido por el mismo lock que
acquire/release. Un callback cancelado no cierra una retención posterior ni un
proceso reemplazado. Hay hasta 16 sesiones en gracia y su cierre ocurre sin más
peticiones. `reconnect_waiting` identifica la retención en estado de sesión;
eventos de espera/reutilización se emiten solo a visores conectados.

Detener/editar/deshabilitar canal o Detener todas no esperan la gracia. Escaneo y
adquisición de un canal distinto liberan retenciones en ese adaptador. Si el GET
del canal siguiente llega antes del cierre del anterior, ese cierre no conserva
al anterior enviando ECM. Suscripciones activas de otros canales siguen aisladas.
Los procesos muertos y los arranques sin datos no se retienen. La API interna de
StreamManager deja gracia cero salvo activación explícita por el panel.

Pruebas con procesos reales verifican reconexión HTTP con los mismos PIDs de
worker, mux y broker; timer sin peticiones; callback viejo tras renovación;
release de una referencia obsoleta; stop inmediato; escaneo y cambio de canal.
No se afirma que esto repare `rejected caid`, `no server match`, `cwc NOK(old)` o
que el mismo socket upstream sobreviva a un cierre iniciado por Ncam.
