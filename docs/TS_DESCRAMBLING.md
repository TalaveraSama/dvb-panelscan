# TS y descifrado: comportamiento y correcciones hasta 0.18.0

## Cambios

- Un indicador de CW disponible no basta para la siguiente aparición de una
  paridad. Se conserva una revisión por clave, incrementada cuando cambia su
  contenido. Al volver a una paridad usada se espera una revisión posterior;
  recibir otra vez la misma clave no hace válida una clave antigua para otro ciclo.
- Cada paquete cifrado en espera que tenía una clave utilizable conserva una
  copia privada en RAM de esa clave/revisión. Las respuestas posteriores no
  sustituyen su clave. Los lotes CSA se separan al cambiar paridad o revisión.
- Los paquetes sin clave esperan en la cola acotada del servicio. No se permite
  que audio/PCR/paquetes claros posteriores adelanten la cabecera bloqueada.
  Alcanzar el límite produce un descarte contabilizado, no memoria ilimitada.
- Se conservan hasta 16 combinaciones CAID/PID ECM distintas de la PMT, incluidas
  las de descriptores de ES. La selección inicial sigue el orden de la PMT;
  dos respuestas fallidas/vacías o 12 segundos sin CW válida permiten avanzar
  al siguiente candidato. Una PMT repetida no reinicia la selección.
- El transporte desconectado se reconecta; un único error de transporte no
  dispara por sí mismo el contador de alternativas. El plazo sin progreso puede
  avanzar de candidato incluso si la conexión sigue caída.
- Cada selección tiene una generación. Una respuesta pendiente de una selección
  anterior no modifica las claves actuales. Cambiar selección elimina ECM en cola,
  invalida claves y descarta el payload pendiente (contabilizado).

## Límites importantes

Esto reduce fallos concretos del motor, pero no certifica vídeo decodificado.
Una CW no nula no demuestra que sea correcta. No existe comparación de imagen
ni verificación del códec dentro del panel.

El guard por paridad es conservador: emisoras que reutilizan deliberadamente la
misma CW en sucesivos ciclos pueden quedar esperando. No es un descifrador de
histórico: si la espera abarca varios períodos, no se garantiza recuperar los
paquetes antiguos. Los datos que no tenían clave al encolarse dependen de una
respuesta posterior; no hay una asociación universal ECM→período disponible en
Newcamd que permita garantizar su correspondencia.

La alternativa CA utiliza respuesta/tiempo, no selección por tarjeta anunciada
ni validación del vídeo. No interpreta todos los formatos privados de proveedor;
se conserva el proveedor existente (0 cuando no se conoce). No soporta claves
distintas simultáneas para cada ES ni añade AES/CSA3, PowerVu o acceso no autorizado.
No promete abrir todos los canales HD: la resolución no determina el cifrado.

Una actualización no repara paquetes perdidos en DVB, RF, red o por consumidores
lentos. No modifica timestamps artificialmente ni aplica filtros de nitidez.

## Prueba recomendada tras actualizar

1. Detén las reproducciones antes de actualizar; la actualización reinicia motores.
2. Conserva una copia privada de tu configuración y el paquete anterior para
   poder volver a una versión conocida si tu emisora usa CW repetidas.
3. Abre **Eventos en vivo**, inicia directamente en VLC un SD que antes tenía
   bloques verdes y observa varios cambios de clave durante 2–3 minutos.
4. Repite con el HD, sin Flussonic primero. Después compara mediante Flussonic.
5. Revisa los eventos de **Acceso condicional: CAID ... ECM PID ...**, **Esperando
   clave nueva para cambio de paridad** y las estadísticas acumuladas:
   `tei`, `cc_errors`, `service_drops`, `pending`, `ecm_drops`.
6. Anota canal/TP/SID y el momento del defecto. Comparte solo eventos estructurados;
   no contraseñas, DES keys, CW ni URLs con tokens. No se guardan registros.

Un cambio de CA puede producir un corte mientras prueba alternativas. Si ningún
candidato entrega CW válida, el panel no inventa claves ni atribuye el fallo a CPU.

## Validación automatizada

`tests/test_transport.c` cifra paquetes sintéticos con libdvbcsa y comprueba la
salida byte a byte para par/impar, respuesta tardía, paquetes claros retenidos,
claves diferentes en cola y respuestas de contextos antiguos. `test_demux.c`
comprueba PMT con varias CA y conservación de la selección. Se ejecutan con
AddressSanitizer/UBSan en CI junto con las pruebas de transporte y broker.

`tests/test_events.py` verifica que los diagnósticos se muestran en vivo sin
reenviar líneas arbitrarias ni secretos. La prueba real con TBS/Newcamd del usuario
sigue pendiente; no se ha accedido por SSH a su servidor desde este entorno.

## Sincronización de transporte (0.16.1)

La sincronía de bytes y la sincronía temporal A/V no son la misma cosa:

- El lector C busca tres cabeceras plausibles separadas por 188 bytes antes de
  adquirir alineación. Conserva fragmentos entre lecturas y vuelve a buscar al
  perder la cabecera. No soporta TS de 192/204 bytes en esta entrada.
- Se rechazan AFC reservado y campos de adaptación que no caben en el paquete.
- Al perder alineación se descartan las secciones parciales. TEI invalida la
  sección de ese PID; salto de continuidad o discontinuity_indicator PSI también
  reinicia el ensamblado y espera PUSI. Una copia con el mismo CC no se concatena.
- pointer_field solo completa la sección anterior. Después comienza otra sección
  desde cero; un resto viejo incompleto nunca se concatena con la nueva. Se
  aceptan payloads de continuación pequeños y PUSI con solo pointer=0.
- En 0.16.1 no se cambiaban PCR/PTS/DTS ni la cadencia de entrega. En 0.18.0
  se conserva el contenido de timestamps y se regula la entrega según el reloj
  descrito abajo. Esto no valida la deriva del reloj A/V del emisor.

Los contadores acumulados `sync_losses`, `sync_bytes`, `invalid_ts` y
`psi_discontinuities` se publican en **Eventos en vivo** junto a los anteriores.
`sync_bytes` incluye basura inicial descartada, no solo pérdidas durante emisión.
No hay registro histórico. Una pérdida de payload ya recibida no se puede
reconstruir; si el paquete conserva una cabecera válida pero su contenido está
corrupto, esta comprobación no valida la imagen ni todos los errores del códec.

La salida stdout del motor sigue siendo exclusivamente TS alineado; el panel
rechaza una salida no alineada en vez de adivinar cómo reparar un binario incompatible.

## Ajustes ECM / CA por canal (0.17.0)

### Investigación web y decisiones

Se revisó el código primario de Tvheadend, fijado al commit
`8837853a69aa822b0203eb3909c5912dc831312e`:

- [cclient.c, líneas 1135–1165](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L1135-L1165):
  selección con PID preferido/bloqueado, CAID forzado y compatibilidad de tarjeta.
  Nuestra opción manual bloquea una pareja PMT; **no** implementa todavía el
  descubrimiento completo de tarjetas/proveedores de Tvheadend.
- [Configuración de descrambler](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/data/conf/descrambler):
  trata intervalo ECM, multipid, CW constante, comprobación de paridad y sistemas
  de paridad ECM estándar/invertida como opciones por CA. No existe una regla
  universal que permita deducir un período real solo de recibir CW no nulas.

Los valores de recuperación 15/20 segundos y supresión de repetidos 1 segundo son
**decisiones de este proyecto**, no tiempos copiados de Tvheadend ni garantía de
la latencia de un servidor Newcamd. No se copió una política universal de claves.
Las limitaciones del guard conservador de 0.16.0 siguen vigentes.

### Uso en RCN Novelas del caso reportado

Los eventos del usuario mostraron CW disponibles en CAID `0B00`, ECM PID `5169`,
y respuestas negativas en otras alternativas. Eso justifica probar esa pareja
**en ese canal**, no imponerla a todos los canales o a otro transpondedor.

1. En **Canales y EPG**, abre **Editar** en RCN Novelas (TP:SID `3be839f6:29`).
2. Mantén el perfil Newcamd autorizado correspondiente.
3. Escribe **CAID fijo: 0B00**, **ECM PID fijo: 5169** y guarda.
4. Se detiene solamente esa sesión: reabre la reproducción y observa eventos.
5. Para volver a automático borra ambos campos y guarda.

La pareja debe aparecer en la PMT actual; si falta, se informa del error y no se
elige silenciosamente otro sistema. El escaneo y los perfiles no se sobrescriben.
No se incluye el valor en la identidad del canal ni cambian permisos IPTV/EPG.
En CLI: `--caid 0B00 --ecm-pid 5169`. Los dos argumentos son obligatorios juntos.

### Comportamiento automático y medición

- Antes del primer éxito conserva el sondeo de dos fallos o 12 s sin progreso.
- Tras un éxito espera al menos 15 s sin éxito y 3 respuestas fallidas para
  solicitar rotación; 20 s sin progreso también permite avanzar. Los fallos
  invalidan la disponibilidad de claves; la espera no significa aceptar claves
  antiguas indefinidamente. El bloqueo manual deshabilita toda rotación.
- Reordenar la PMT manteniendo la misma pareja no borra claves, cola ni contexto.
- ECM repetidos se comparan por sección completa/contexto y se suprimen durante
  como máximo 1 s. Un cambio de bytes, tabla ECM o selección se procesa sin esa
  espera. Las repeticiones no se cuentan como pérdidas ni como CW nuevas.
- `ca_switch_drops`: payload descartado por cambio/reinicio de contexto CA.
- `queue_full_drops`: payload descartado por alcanzar el límite de espera.
- `service_drops`: suma total de los anteriores desde el inicio del proceso.
- `ecm_repeats`: retransmisiones idénticas suprimidas, no pérdidas de vídeo.

Comparar dos muestras separadas en el tiempo, no interpretar un contador acumulado
como pérdida instantánea. La latencia del evento ECM se mide desde el worker;
0–1 ms puede ser caché del broker, no una consulta fresca al servidor remoto.

No se promete que fijar la pareja cure recepción RF, CW equivocadas, algoritmos no
soportados o errores del reproductor. Probar SD/HD directamente y después mediante
Flussonic. No compartir contraseñas, claves o enlaces con tokens en los informes.


## Retardo y entrega regulada (0.18.0)

**Editar canal → Retardo de descifrado (ms)**: entero 0–2000, predeterminado
500 ms, persistido por TP:SID. Guardar detiene solo ese servicio; debe reabrirse.
CLI: `--descramble-delay-ms 500`. Cero desactiva la espera mínima, no el pacing.
El valor es una decisión inicial de este proyecto, no una medición de la señal
ni un valor universal de Astra.

### Pipeline y límites

- ECM sigue procesándose sin esperar el retardo de vídeo. El hilo TS conserva
  la hora monotónica de cada paquete, su orden y las snapshots de claves existentes.
  PAT/PMT regeneradas pasan por la misma cola, sin adelantar al payload retenido.
- CSA no procesa paquetes antes de cumplir el retardo. Una CW ausente conserva
  bloqueada la cabecera, pero solo hasta retardo + 2000 ms. Después los paquetes
  caducados se descartan y cuentan en `cw_timeout_drops` y `service_drops`.
  Esto no implementa un historial universal de claves por período: siguen los
  límites de asociación ECM/CW explicados arriba.
- Un hilo independiente entrega los paquetes descifrados a stdout/HTTP/UDP.
  La captura y el hilo ECM no duermen para regular bitrate ni esperan sockets
  de salida. stdout no bloqueante tiene un plazo de escritura de 500 ms: si el
  consumidor se atasca, el motor termina para requerir reconexión, no altera
  silenciosamente bytes parciales del TS. UDP usa envío no bloqueante.
- El reloj aprende espaciado de PCR del PID anunciado por PMT e interpola según
  paquetes del servicio. Tiene en cuenta wraparound de PCR; PCR repetido no cambia
  el ancla. Sin PCR utiliza espaciado de llegada; comienza con 75 µs por paquete
  mientras aprende. Si PCR falta durante más de 500 ms vuelve a llegada.
  No es un remultiplexor CBR ni un analizador completo de jitter.
- Hay 100 ms de margen inicial en la entrega. Tras retraso de más de 20 ms, el
  reloj se reasienta 20 ms por delante, en lugar de recuperar todo de golpe.
  PCR discontinuo/salto >500 ms o planificación >500 ms por delante reasientan
  el reloj. Se agrupan como máximo siete paquetes en una ventana de 1 ms.
- Tanto la cola previa a CSA como la de salida admiten hasta 65536 paquetes
  (aproximadamente 12 MiB de payload cada una, más metadatos). Memoria por servicio.
  Una cola llena se vacía con descarte contabilizado. Salida también descarta
  paquetes de edad mayor que retardo + 2000 ms. No se recupera vídeo ya perdido.
- Cambiar contexto CA limpia colas y cambia época de salida; un lote que estaba
  esperando reloj se descarta si su época queda obsoleta. Los bytes ya entregados
  al sistema operativo no se pueden retirar.

### Eventos y prueba real

En **Eventos en vivo** se añaden `cw_timeout_drops`, `output_pending`,
`output_drops`, `output_late`, `pcr_resets`, `pes_errors`, `udp_output_drops`. Son acumulados salvo
ocupación de cola; comparar dos muestras de una misma sesión. No se guardan
archivos ni se conservan eventos sin visor.

`pes_errors` solo detecta prefijos PES inválidos en PUSI de los primeros ES de
vídeo/audio cuando caben completos en el paquete. No analiza todos los ES, códecs,
cabeceras fragmentadas ni la imagen. `pcr_resets` cuenta reajustes del reloj, no
es una medida de jitter PCR ni prueba de recepción correcta.

Probar con 500 ms por defecto durante varios cambios de clave, SD y HD directo
al panel y después por Flussonic. Subir a 1000 ms solo como comparación si hay
esperas de CW; no oculta claves incorrectas, RF dañada o paquetes perdidos. No
cambiar simultáneamente CAID/PID y retardo, para poder atribuir diferencias.

Pruebas: `test_pacing.c` verifica PCR/wrap/discontinuidad, reloj tardío, fallback,
PCR repetido y límites. `test_transport.c` verifica retardo, caducidad, salida
asíncrona ordenada y límites de cola además de las pruebas de CSA existentes.
No equivale a una validación con la señal del usuario.
