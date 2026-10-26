# Importar configuración Astra / Cesbo · 0.13.0

## Antes de empezar

El login de DVB Panel es independiente del de Astra. Un 401 es un rechazo de
credenciales, no un error de importación. Entrar primero por navegador con el
usuario configurado en DVB Panel. Si se olvidó, restablecerlo mediante --set-admin
con el servicio parado según SECURITY.md. No compartir contraseñas ni el JSON
completo en chats: puede contener cuentas de acceso condicional y hashes de usuarios.

Guardar una copia privada del archivo original. Tener Astra y el panel sin usar
los mismos frontends; detener sesiones y finalizar escaneos antes de previsualizar.
No se detienen procesos de Astra ni se cambian sus archivos desde el importador.

## Interfaz

1. Abrir **Importar Astra / Cesbo** en el menú lateral del panel autenticado.
2. Seleccionar JSON menor de 1,9 MB. Se lee en RAM, sin localStorage ni uploads
   públicos. Usuarios, hashes, auth y outputs de Astra no se envían al servidor.
3. Asignar cada MAC a adapterN, comprobándolo con el registro de arranque del
   kernel. No se deduce de nombres 1,2,A,B ni del orden del archivo. Si ya se
   importó esa MAC, se ofrece su asignación anterior, que hay que verificar.
4. Seleccionar perfil DVB Panel para cada cam de Astra. Alternativamente activar
   importar cuentas no mapeadas. Se admiten solo Newcamd; requiere DES key válida
   de 14 bytes en deskey/key del archivo o en el campo manual. No se supone una
   clave predeterminada. Los perfiles mapeados no sobrescriben sus credenciales.
5. Pulsar Previsualizar. Revisar TP/adaptador, cambios, perfiles y advertencias.
   Todavía no se escribe nada. Cualquier cambio requiere otra vista previa.
6. Aplicar y confirmar. Se guarda respaldo privado; después revisar Dashboard,
   escanear un TP de prueba y comprobar servicios, CA y EPG de la emisión real.

Se asume **LNB universal 9750/10600**, visible en el diálogo. Para otro LNB no
usar esta importación sin adaptar los ajustes antes de escanear. Polarización R
se conserva con advertencia: no es V. Corregir el archivo local si el nombre dice
V pero el campo polarization es R y se verificó que la instalación es lineal V.

## Alcance

- dvb_tune: S/S2, frecuencia MHz, polarización y symbolrate ×1000; solo habilitados.
- make_stream: una entrada dvb://ID#pnr=SID&cam=CAM por canal. No importa failover,
  remux/transcoding, HLS ni salidas. Entradas múltiples/no DVB se omiten.
- Canales: nombres, enabled y perfil explícito por cam. Otros metadatos locales
  del mismo canal se conservan. Entradas TP:SID repetidas prefieren la habilitada;
  activos incompatibles abortan. Un TP deshabilitado no trae canales nuevos.
- El perfil más frecuente entre canales habilitados se usa como predeterminado
  del TP; las asignaciones individuales preservan las excepciones del archivo.
- Nuevos servicios solo tienen identidad/nombre; no inventan PID ni horarios EPG.
  Escanear completa información técnica actual. No garantiza descifrado autorizado.
- Coincidencia por astra_id + MAC de importaciones anteriores; si no, por frecuencia,
  polarización y adaptador exactos. No se adivina el satélite a partir de frecuencia.
  TP locales en otro adaptador permanecen; revisar avisos de duplicados.
- No borra TP locales que no estén en el archivo. Se mantienen IDs/EPG al actualizar
  una coincidencia. Repetir la misma importación actualiza, no duplica, esas entradas.

MAC queda como referencia source_mac, **no un binding permanente** del sintonizador.
Si Linux cambia la numeración después de reiniciar, revisar el mapa antes de recibir.
No se importa login, hashes, whitelist, autenticación, TLS, usuarios ni privilegios.

## Escritura y recuperación

`/var/lib/dvb-panelscan/import-backups/astra-*.json`: snapshot previo de perfiles,
transpondedores y canales, directorio 0700 y archivo 0600. Puede contener secretos
locales: no compartirlo ni servirlo desde un servidor web. Se conserva hasta que
el administrador lo elimine; no hay retención automática. No es un log de canales.

Durante la aplicación hay un diario privado astra-recovery.json y sustituciones
atómicas de los tres JSON. Error de escritura intenta restaurar el snapshot; un
reinicio antes de finalizar revierte la operación en _load. Si la restauración
falla por disco/permisos, corregirlo y reiniciar antes de otras modificaciones.
La recuperación no modifica auth.json, EPG ni overrides de systemd.

API POST /api/import/astra/preview y /apply, solo administrador + CSRF, hasta 2 MiB.
Payload: config, adapters (MAC en minúsculas→índice entero), profiles (CAM→perfil),
import_profiles booleano y deskey opcional. Aplicar requiere preview_token firmado
que corresponde a los mismos datos y al estado actual. No se devuelve raw JSON,
contraseñas ni hashes de acceso en la vista previa. Usar HTTPS/VPN o LAN de confianza.
