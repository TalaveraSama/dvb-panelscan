# 0.7.0 · Estado ECM, perfiles y edición de canales

## Luces del canal

Dashboard y Streaming se actualizan cada dos segundos. Cada worker escribe en
su registro el resultado ECM, latencia, presencia de mitades CW no nulas,
reloj monotónico y PID. El panel lee incrementalmente solo eventos de ese proceso,
no eventos anteriores a su inicio ni de otro worker con el mismo SID/log.
No se publican las claves ni el contenido de ECM.

| Color | Significado |
|---|---|
| Verde con brillo | Al menos dos éxitos consecutivos con CW no nula en los últimos 15 s y último resultado positivo |
| Rojo | Último ECM fallido, CW vacía, éxito caducado, o canal cifrado sin ECM después de 15 s de arranque |
| Amarillo | Esperando PMT/ECM o solo una CW válida, aún sin continuidad |
| Azul | PMT de servicio libre, no requiere ECM |
| Gris | Inactivo/detenido; si falla la consulta al panel muestra «Sin telemetría» |

Un error reinicia la comprobación de continuidad: hacen falta dos nuevos éxitos
para volver a verde. El brillo respeta `prefers-reduced-motion`. La etiqueta de
texto y el tooltip permiten interpretar el estado sin depender del color. Los
15 s son una ventana de observación, no una pausa ni una validación de época CW.

**Verde no certifica imagen/audio correctos** ni fotogramas decodificados: puede
haber problemas de señal, pérdida de TS, códec o aplicación temporal de claves.
No abre todos los canales para comprobarlos; solo monitoriza sesiones bajo
demanda o manuales activas. La latencia incluye posibles respuestas de caché
positiva del broker (máximo 1 s), no solo consultas nuevas al servidor CA.

## Perfiles Newcamd

En Acceso condicional:

1. La cuenta existente permanece como **Predeterminado**, sin migración destructiva.
2. «Nuevo perfil»: nombre, host, puerto, usuario, contraseña y DES key.
3. Guardar, volver a Dashboard y editar el canal para seleccionar el perfil.
4. Abrir su URL. Los canales con la misma configuración comparten broker;
   configuraciones distintas usan brokers diferentes, sin volver a sintonizar.

Guardar un perfil detiene solo los canales que lo tienen asignado (incluidos los
que heredan Predeterminado). Reabrir sus URLs para aplicar. Eliminar un perfil
asignado devuelve 409; primero reasignar sus canales. No se elimina Predeterminado.
«Comprobar conexión TCP» usa el perfil seleccionado **guardado**; no valida login,
CAID, tarjeta, permisos ni CW. No se implementa fallback automático de cuentas.

## Editor de canal

Botón «Editar» en la lista del Dashboard:

- Nombre visible en Dashboard, EPG, M3U y XMLTV.
- Grupo M3U (`group-title`).
- Número (`tvg-chno`); no reordena por sí mismo la playlist.
- Logo HTTP/HTTPS (`tvg-logo`); el panel no descarga esa URL.
- Perfil Newcamd.
- Habilitado. Deshabilitar retira el canal del M3U/XMLTV y rechaza su URL con 403,
  pero lo mantiene visible/editable en el Dashboard.

Guardar detiene solo ese canal; reabrirlo aplica los cambios. Los cambios se
conservan en `channels.json` por **TP:SID**, separados de los resultados del
escaneo, por lo que sobreviven al reescaneo. No modifica SID, frecuencia, PIDs,
CAID o PCR/PTS. Un SID duplicado en otro TP no se edita ni elimina accidentalmente.
Borrar un transpondedor elimina sus ajustes y detiene sus canales, no otros.

Los perfiles extra se guardan en `profiles.json`; la cuenta predeterminada sigue
en `newcamd.json`. Archivos privados 0600 y sustitución atómica. El listado de
perfiles excluye contraseña/DES; el detalle las entrega para editar en este panel
local, igual que la configuración anterior. **Sigue sin autenticación**: no exponer
a Internet; usar LAN confiable o proxy con autenticación/TLS. Las credenciales del
broker siguen siendo visibles al administrador del host en argv.

## API

- GET `/api/newcamd/profiles`: metadatos sin contraseña/DES.
- GET `/api/newcamd/profiles/<id>`: detalle del perfil (incluido `default`).
- POST `/api/newcamd/profiles`: crear o actualizar (`id` existente para actualizar).
- POST `/api/newcamd/profiles/<id>/delete`: eliminar si no está asignado.
- POST `/api/channels/<TP:SID>/edit`: `name`, `group`, `number`, `logo`, `enabled`,
  `newcamd_profile`. Validación de tipos y campos; no acepta modificar SID.
- GET `/api/stream/status`: por sesión, `ecm` con estado, último resultado,
  latencia, máscara de mitades presentes (no sus valores), edad del último éxito
  y número de éxitos recientes. También `newcamd_profile` y `profile_name`.

## Alcance frente a Astra/Cesbo

Se añaden controles de operación de canales, no una réplica completa. Se mantiene
el motor multicanal de 0.6.0. No hay failover CA automático, balanceo de lectores,
transcodificación, HLS, analizador de vídeo, EMM, acceso de usuarios ni programación
de canales. La selección avanzada CAID/proveedor y la gestión temporal completa
CW siguen pendientes. Pruebas con TS/upstream simulados no sustituyen la
validación DVB/CA autorizada en el servidor del usuario.

## Transpondedor editable y herencia de softcam (0.11.0)

En Dashboard, **Editar** junto a Escanear y Borrar carga el TP en el formulario.
Corregir frecuencia, LO, SR, polarización, tipo, adaptador, tiempo, nombre o perfil,
y pulsar Guardar cambios. Cancelar no modifica el servidor. Se conserva el ID del
TP: no se borran servicios, overrides TP:SID ni el EPG guardado por editar.

**Softcam / perfil Newcamd predeterminado** elige entre el global y los perfiles
creados en Acceso condicional. Se usa cuando un canal no tiene excepción propia.
En Editar canal, **Heredar del transpondedor** elimina la excepción; seleccionar
**Predeterminado** explícitamente fuerza el global, no el del TP. Las asignaciones
explícitas de versiones anteriores se conservan y no se sobreescriben en bloque.
El perfil efectivo se usa para arrancar el broker, mostrar estado y detener
servicios cuando se modifica una cuenta. No se elimina un perfil referido por un TP.

Para cambiar sintonía o perfil, detener primero las sesiones de los adaptadores
implicados y esperar al fin del escaneo. La API devuelve 409 sin guardar si están
ocupados; cambiar solo el nombre es posible durante streaming (no durante scan).
No se resintoniza ni se corta el flujo silenciosamente al guardar. Si cambió la
señal, reescanear para verificar SIDs; los canales/EPG anteriores se conservan pero
pueden no corresponder. Para otro satélite es preferible crear un TP independiente.

API POST `/api/transponders/ID/edit`: actualización parcial de campos permitidos;
rechaza reemplazar ID/scan, valores inválidos y perfiles inexistentes. Se mantiene
el POST de creación. Borrar es una operación separada y destructiva con advertencia.
La palabra softcam aquí designa perfiles Newcamd; no se añadió otro protocolo,
conmutación DiSEqC ni selección de tono de 22 kHz.
