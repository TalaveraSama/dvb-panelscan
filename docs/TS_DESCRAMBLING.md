# TS y descifrado: correcciones 0.16.0

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
- No se cambian PCR/PTS/DTS, la cadencia del emisor ni el orden de servicio.
  Las pruebas verifican conservación byte a byte del PCR y payload reenviado.
  Esto no mide ni corrige jitter PCR ni deriva de reloj A/V.

Los contadores acumulados `sync_losses`, `sync_bytes`, `invalid_ts` y
`psi_discontinuities` se publican en **Eventos en vivo** junto a los anteriores.
`sync_bytes` incluye basura inicial descartada, no solo pérdidas durante emisión.
No hay registro histórico. Una pérdida de payload ya recibida no se puede
reconstruir; si el paquete conserva una cabecera válida pero su contenido está
corrupto, esta comprobación no valida la imagen ni todos los errores del códec.

La salida stdout del motor sigue siendo exclusivamente TS alineado; el panel
rechaza una salida no alineada en vez de adivinar cómo reparar un binario incompatible.
