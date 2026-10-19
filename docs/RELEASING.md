# Versiones y referencia estable

## Base 0.8.3

Consolidación de 0.8.2, que el usuario confirmó funcionando en su TBS real:
multicanal, perfiles por canal, EPG recibido y horario UTC configurable.
0.8.3 no cambia el motor de captura ni los horarios; ordena y verifica el paquete.
Es una referencia de funcionamiento, no una garantía LTS ni una certificación de
hardware/proveedores no probados. Plataforma de paquete: Ubuntu 22.04 amd64.

`VERSION` es la fuente única del número de versión para paquete, panel, API y CI.
El número se encuentra también en `/usr/lib/dvb-panelscan/VERSION`,
`/usr/share/doc/dvb-panelscan/BUILD_INFO.txt` y `GET /api/status`.

## Próximos cambios

1. Partir del código de la referencia publicada; no modificar ni reemplazar sus
   assets o etiquetas. Mantener los cambios pequeños y registrar regresiones.
2. Actualizar VERSION y RELEASE_NOTES.md. Usar parche para correcciones,
   versión menor para funciones nuevas; no prometer compatibilidad no probada.
3. Ejecutar Python/HTTP, DOM, integraciones C y sanitizadores en CI.
4. Construir con `sh packaging/build-deb.sh`; no necesita argumentos ni ejecutarse
   desde el directorio raíz. La versión alternativa que no coincida se rechaza.
5. Ejecutar `python3 packaging/check-deb.py dist/*.deb` desde la raíz con un solo
   paquete en dist. Verifica módulos, logos, documentos, manifest y ausencia de
   configuraciones/EPG/logs del usuario.
6. CI instala, inicia, configura UTC−6/dos días, reinstala y comprueba que conserva
   los datos y el override local de systemd. Publicar solo con todas las pruebas verdes.
7. Un commit `Release vX.Y.Z: ...` publica la versión de VERSION en el workflow
   actual. Se exige coincidencia exacta. Las publicaciones son inmutables: si hay
   un error, publicar otra versión, nunca sustituir silenciosamente un paquete.

La publicación contiene `.deb`, `SHA256SUMS` y `BUILD_INFO.txt` con commit exacto.
El checksum protege integridad; no es una firma criptográfica de identidad.

## Actualizar o volver a una base

Guardar una copia privada de `/var/lib/dvb-panelscan` y
`/etc/systemd/system/dvb-panel.service.d` antes de una actualización importante.
Contienen credenciales: no compartirlos ni subirlos a Git. El paquete no los incluye
ni los elimina. No añadir archivos de logs de canales como parte del respaldo.

Parar dvb-panel, instalar el .deb elegido, iniciar y recargar con Ctrl+F5.
Para bajar de versión, apt requiere `--allow-downgrades`; restaurar una copia
compatible si una versión futura cambia el esquema de datos. No se garantiza
compatibilidad hacia atrás sin prueba. No borrar configuraciones para actualizar.

Los futuros cambios continúan mediante revisión; declarar una referencia estable
no implica fusionar automáticamente el PR abierto.
