# Usuarios IPTV y Flussonic (0.15.0)

## Cuentas independientes

En **menú lateral → Usuarios IPTV**:

1. Introduce usuario, contraseña de 12–256 caracteres y marca las casillas de
   canales autorizados. En 0.14.1 puedes seleccionar todos los habilitados o
   quitar la selección; el contador indica cuántos permisos se guardarán.
2. Opcionalmente establece vencimiento en hora local del navegador.
3. Guarda y usa **Descargar M3U del usuario**, o copia la URL de lista y el token. Ningún canal seleccionado significa
   ningún canal autorizado; no existe un permiso implícito para canales futuros.
4. Para editar selecciona un usuario. Contraseña vacía conserva la actual.
   Guardar siempre revoca sus tokens anteriores; deshabilitar revoca su acceso.
   Cambiar el nombre crea otra cuenta, no renombra ni deshabilita la anterior.

Estas cuentas no son administradores ni perfiles Newcamd. No se cambia el
funcionamiento de descifrado ni el token global anterior del administrador.
No hay API Xtream Codes, facturación ni límite de conexiones en esta versión.

## Listas y señales del panel

Lista con contraseña (para clientes que no admitan Basic):

```
https://PANEL/iptv/playlist.m3u8?username=USUARIO&password=CONTRASENA_URL_ENCODED
```

También se admite HTTP Basic en la descarga de la lista y en cada señal.
Preferir la lista firmada generada por la interfaz:

```
https://PANEL/iptv/playlist.m3u8?token=TOKEN_USUARIO
```

La lista contiene solamente los canales autorizados, con URLs como:

```
https://PANEL/iptv/stream/TP:SID?profile=pass&token=TOKEN_USUARIO
```

Es MPEG-TS HTTP continuo, no HLS ni transcodificación. Flussonic puede tomar cada
URL como entrada HTTP. Crea una cuenta técnica específica para Flussonic con los
canales necesarios y sin vencimiento si corresponde. No uses la cuenta de un
cliente como entrada: revocarla cortaría la fuente para todos sus espectadores.
El panel comprueba autorización también durante la transmisión, no solo al abrir.

## Flussonic consulta al panel (on_play)

El endpoint implementa el contrato HTTP GET de autorización de reproducción de
Flussonic: `name`, `token`, 200 para permitir y 403 para denegar;
`X-AuthDuration: 30` solicita revalidación cada 30 segundos. Se aplica tanto a
`new_session` como a `update_session`. Otros parámetros de sesión se toleran,
pero no se usan para imponer un límite de conexiones.

Abre **menú lateral → Flussonic** y copia **Backend de autorización Flussonic (privado)**.
El selector de canal muestra el nombre seguro exacto que debes usar en Flussonic.
La ruta contiene una clave independiente que solo debe conocer Flussonic.
No entregues esta URL a los clientes. Restringe además su acceso en el firewall
al servidor Flussonic y utiliza HTTPS/VPN.

Nombres seguros de stream: `dvb_` seguido de la identidad `TP:SID` codificada
como hexadecimal UTF-8. También se acepta `TP:SID` literalmente. Ejemplo para
`tp:10`: `dvb_74703a3130`. No utilices el nombre visible del canal como identidad.
Para obtener el nombre desde una identidad real:

```bash
python3 -c 'print("dvb_" + "tp:10".encode().hex())'
```

Ejemplo **ilustrativo**, reemplaza identidad, origen y claves con los reales:

```text
stream dvb_74703a3130 {
  input https://PANEL/iptv/stream/tp:10?profile=pass&token=TOKEN_CUENTA_TECNICA;
  on_play https://PANEL/iptv/flussonic/CLAVE_BACKEND;
}
```

Cliente HLS de Flussonic:

```
https://FLUSSONIC/dvb_74703a3130/index.m3u8?token=TOKEN_USUARIO_CLIENTE
```

También puede usar `token=USUARIO%3ACONTRASENA_URL_ENCODED`; el panel valida
la contraseña contra el hash. Es preferible el token firmado: evita incluir la
contraseña y el coste PBKDF2 en cada revalidación. Si usas credenciales codifica
el valor completo `usuario:contraseña` como un solo parámetro de URL.

Deshabilitar, vencer o guardar el usuario hace que la siguiente consulta del
backend deniegue el acceso. Flussonic puede conservar la autorización durante
su ventana de caché; no se promete corte instantáneo ni se expulsan sesiones
mediante la API de administración de Flussonic. Los segmentos ya descargados
no pueden revocarse. Comprueba la configuración de tu versión de Flussonic.

## Seguridad y almacenamiento

- Administración de cuentas: sesión de administrador y CSRF existentes.
- `iptv.json` privado 0600, contraseña PBKDF2-SHA256 (600000 iteraciones),
  claves de firma/backend y revisión revocable por cuenta. No guarda contraseñas
  en claro; los tokens son credenciales bearer, no cifrado.
- El archivo forma parte del estado persistente: inclúyelo en tus respaldos
  privados; no lo publiques. Reiniciar no revoca los tokens.
- Las cuentas IPTV no pueden usar las rutas administrativas, EPG global ni
  streaming con token global. La lista personalizada no incluye XMLTV en esta versión.
- Intentos erróneos limitados por cuenta/IP (10 en 10 minutos). Con contraseña
  vía Flussonic, la IP observada es la del servidor, no la del espectador.
- Sin registros persistentes de acceso. Revisa también los logs del proxy y de
  Flussonic para evitar guardar claves o contraseñas contenidas en URLs.
- Las pruebas simulan el contrato HTTP; falta validación con Flussonic real y TBS.

Referencias del protocolo:
https://flussonic.com/doc/api/authorization/
https://flussonic.com/doc/authorize-clients/

## Si la lista solo contiene un canal

La lista muestra los canales habilitados que se autorizaron explícitamente al
usuario, no solamente los que tienen espectadores. No hace falta iniciarlos.
Selecciona el usuario, marca los canales (o todos los habilitados), guarda y
utiliza la nueva URL: el token anterior queda revocado. Los permisos existentes
no se amplían automáticamente al actualizar.

En versiones anteriores el selector múltiple requería Ctrl/Cmd o Shift; la 0.14.1
usa casillas. Si copias el contenido desde un chat puede aparecer como
`[URL](URL)` o `&amp;`; eso no es una línea válida de M3U. Descarga el archivo
directamente desde el panel; debe contener URLs de texto con `&token=` literal.

## Espacios del menú lateral (0.15.0)

- **Usuarios IPTV**: tabla de cuentas con estado y cantidad de canales, botón
  Editar, formulario de cuentas y generación/descarga de enlaces personales.
- **Flussonic**: backend privado, instrucciones de recepción/autorización,
  selección de canal y cálculo de nombre `dvb_...`. Configura Flussonic en su
  propio servidor: no se afirma conectividad ni se realiza despliegue remoto.
- **Seguridad y reproductores**: sigue gestionando el token global anterior.

Mover la interfaz no requiere recrear cuentas ni regenerar tokens. Al salir de
Usuarios IPTV se limpian los enlaces y la contraseña mostrados, no los permisos
guardados ni la validez de los tokens. Al salir de Flussonic se oculta la clave
privada del backend. Guardar una cuenta sí revoca sus enlaces como antes.

### Vista dedicada de usuarios

El menú **Usuarios** abre solamente la tabla de cuentas. Pulsa **Nuevo usuario**
para crear una o **Editar** en la fila de una cuenta existente. **Volver a usuarios**
cierra el editor y limpia contraseña y enlaces mostrados sin guardar cambios.
Flussonic y la seguridad general permanecen en sus espacios separados.
