# Investigación: Astra/Cesbo y los tirones de reproducción

## Alcance

Revisión de documentación pública de Cesbo y del código local, realizada después
del reporte de tirones persistentes con las correcciones TS/ECM. No se accedió al
VPS ni se verificó la configuración desplegada. No se dispone del código privado
actual de Astra y no se afirma equivalencia con su implementación interna.
Esta investigación no publica una nueva versión ni modifica el motor.

## Fuentes primarias

1. Softcam API: https://astra.cesbo.com/en/api/softcam/
   Documenta `shift` como buffer de entrada en milisegundos antes del descifrado,
   heredable por streams; también opciones de timeout, split_cam y skip_ecm.
   La presencia de opciones no implica que estén activadas en el servidor del usuario.
2. UDP delivery: https://astra.cesbo.com/en/delivery/udp/
   Documenta `sync` como transmisión en un hilo separado con sincronización del
   bitrate, y `cbr` como modo de bitrate constante. No se debe extrapolar este
   comportamiento a toda salida HTTP ni confundir CBR con reparación de vídeo.
3. HTTP Relay: https://astra.cesbo.com/en/tools/http-relay/
   Documenta buffer máximo y precarga inicial por cliente. Los valores indicados
   son para la herramienta relay, no valores universales de Astra ni de este panel.
4. PES Error: https://astra.cesbo.com/en/troubleshooting/stream/pes-error/
   Distingue errores del contenido PES, que pueden proceder de descifrado incorrecto
   o recepción. Recibir CW no valida el vídeo.
5. Streams API: https://astra.cesbo.com/en/api/stream/
   Expone contadores separados de CC, PES, scrambled y PCR. Su campo sync_error
   no sustituye la medición PCR (la propia documentación indica limitaciones).
6. Analyzer: https://astra.cesbo.com/en/tools/mpeg-ts-analyzer/
   Permite analizar una entrada HTTP con duración limitada, sin guardar la señal.

## Diferencias verificadas en DVB Panel

- `flush_scrambled()` en `tbsstream/main.c` conserva orden y limita la memoria,
  pero vacía todos los paquetes disponibles en cuanto puede hacerlo. No planifica
  el envío usando PCR ni un reloj de salida independiente.
- Una espera de CW puede detener la entrega y después liberar el retraso en ráfaga.
  Esta posibilidad se deduce del código; aún no se midió en la señal del usuario.
- El tamaño máximo de la cola de descifrado no equivale a un `shift` temporal
  estable. No existe una opción temporal equivalente por canal en esta versión.
- El ring en `panel/streaming.py` es una ventana acotada para consumidores. Un cliente
  se incorpora al punto en vivo y los bloques disponibles se entregan de inmediato.
  No tiene precarga inicial configurable ni regulación de salida por PCR.
- Los diagnósticos CC/TEI/alineación y ECM existen, pero no hay analizador completo
  de PES ni de jitter PCR/PTS/DTS antes/después del descifrado.
- El guard conservador de claves por valor/paridad no equivale a un modelo completo
  de períodos de cifrado. Una CW no nula o repetida no prueba su correspondencia con
  cada paquete. Sus límites de CW constantes/repetidas están documentados aparte.

No se concluye que el servidor Newcamd, la RF, la red o el reproductor sean inocentes;
ni que la ausencia de pacing sea por sí sola la causa del caso reportado.

## Siguiente trabajo justificado

1. Medir salida: errores PES, continuidad por PID, PCR/intervalos y distribución
   temporal de entrega, separando estado antes/después del descifrado y transporte.
2. Capturar dos muestras de estadísticas de la misma sesión y confirmar si CA sigue
   fija o si hay nuevos descartes. No usar contadores acumulados como tasas.
3. Diseñar un retardo de descifrado configurable por canal, con tiempos monotónicos,
   claves asociadas al período correcto, límite de edad/memoria y recuperación.
   No agregar simplemente sleeps dentro de la captura o del hilo Newcamd.
4. Separar recepción, descifrado y salida. Si se confirma entrega en ráfagas, añadir
   un planificador de salida con política explícita para PCR ausente/discontinuo,
   wraparound, pérdida de datos y recuperación sin ráfagas ilimitadas.
5. Añadir precarga HTTP limitada y visible, sin prometer que corrige vídeo corrupto.
6. Probar con señales sintéticas cifradas/temporizadas y luego con SD y HD reales,
   directo al panel y mediante Flussonic. No cambiar timestamps ni transcodificar
   como reparación automática de pérdida de datos.

## Diagnóstico con Astra sin guardar registros

Si Astra está instalado, analizar la URL HTTP del panel, no abrir otro receptor DVB
sobre el mismo adaptador. Se puede usar `astra --analyze -n 30 URL` sin `-o` ni `--log`.
No publicar URL/token en chat ni redirigir eventos a archivos. El informe útil contiene
solo PID, CC/PES/PCR, bitrate y momentos del tirón. No reenviar encabezados, tokens,
contraseñas o claves. Rotar enlaces previamente expuestos.

## Comprobación adicional de código histórico público

Se inspeccionó un mirror histórico, **no el código de la versión comercial actual**:

- Repo: https://github.com/CkNoSFeRaTU/astra
- Commit fijado: `1031c3ed4441652ed15ab52efd26d701d52c899c`
- Descifrado: https://github.com/CkNoSFeRaTU/astra/blob/1031c3ed4441652ed15ab52efd26d701d52c899c/modules/softcam/decrypt.c
- Salida UDP: https://github.com/CkNoSFeRaTU/astra/blob/1031c3ed4441652ed15ab52efd26d701d52c899c/modules/udp/output.c

El descifrador histórico agrupa paquetes en bloques, alterna buffers de recepción
salida y aplica determinadas actualizaciones de CW después de procesar un bloque.
Su selección de descriptores consulta un módulo CAS. Esto respalda revisar los
límites de actualización de clave y la planificación, pero no demuestra que el
Astra moderno opere idénticamente ni justifica copiar el algoritmo sin pruebas.

El importador actual de este repo copia los campos básicos del perfil Newcamd;
no migra `shift`, `split_cam`, `skip_ecm` ni implementa sus equivalentes en el motor.
No se conoce si esas opciones estaban activadas en la instalación original del
usuario: no se conserva su JSON privado ni se ha accedido al servidor.

### Unidades y límites de la comparación

La API oficial actual describe `shift` en milisegundos. Documentación antigua de
entradas usaba otras unidades; no deben copiarse valores sin identificar versión
opción concreta. La documentación UDP de `sync` no demuestra regulación PCR para
toda salida HTTP. El buffer HTTP Relay tampoco prueba cómo está configurado el
servidor original. Se verificaron estas diferencias para evitar extrapolaciones.

No hay nueva release asociada a esta investigación: el motor sigue en 0.17.0.
