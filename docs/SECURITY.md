# Seguridad y acceso · 0.9.0

## Cambio importante al actualizar

0.9.0 exige login. No hay contraseña predeterminada ni alta anónima desde la web.
Los enlaces de streaming de versiones anteriores necesitan un token nuevo. La
referencia 0.8.3 se conserva, pero no incluye estas protecciones de acceso.

Tras instalar el paquete, con acceso a la consola del servidor:

```sh
sudo systemctl stop dvb-panel
sudo -u dvb-panel env DVB_PANEL_DATA_DIR=/var/lib/dvb-panelscan \
  python3 /usr/lib/dvb-panelscan/server.py --set-admin admin
sudo systemctl start dvb-panel
```

La consola pide la contraseña dos veces, sin mostrarla. Mínimo 12 y máximo 256
caracteres; usar una contraseña larga y única. No ponerla en argumentos, scripts
compartidos o comandos que queden en el historial. Abrir el panel e iniciar sesión.
Para restablecer usuario/contraseña, repetir el procedimiento con el servicio
parado; se invalidan las sesiones y los tokens de reproducción anteriores.

## Protecciones implementadas

- Un administrador; no se añaden cuentas compartidas, roles, MFA ni SSO.
- PBKDF2-HMAC-SHA256 de 600 000 iteraciones, salt aleatoria y comparación constante.
- auth.json privado 0600; no contiene la contraseña ni el token de reproducción
  en claro. No cambia el almacenamiento de credenciales Newcamd, que sigue siendo
  privado pero no cifrado. Proteger y cifrar los respaldos del sistema.
- Sesiones aleatorias de 256 bits, solo RAM, máximo 64 y vencimiento absoluto de
  ocho horas. Reiniciar el servicio o cerrar sesión las revoca. Cookie HttpOnly,
  SameSite=Strict; Secure al habilitar HTTPS según instrucciones de abajo.
- CSRF en todas las operaciones autenticadas POST y comprobación de Origin.
- Diez intentos por dirección durante diez minutos, límites de estado en RAM y
  cuatro verificaciones de contraseña concurrentes. El proxy puede añadir límites
  de red. El contador se pierde al reiniciar; no es una protección DDoS completa.
- APIs, secretos, SSE, XMLTV y streams del panel requieren acceso. Logout/expiración
  corta los consumidores de sesión; rotar token corta los streams que usaban ese
  token cuando vuelven a enviar datos. Esto no detiene salidas manuales UDP/HTTP.
- CSP, protección contra framing, no-sniff y referrer-policy; sin recursos JS de
  CDN. Sin contraseña en URL ni acceso administrativo mediante token de medios.
- Eventos de canales siguen solo en vivo, sin archivos ni historial. Los contadores
  de login/sesiones no se guardan como logs. auth.json es configuración, no un log.

## VLC, IPTV y XMLTV

En **Seguridad y reproductores**, pulsar **Revocar anteriores y generar enlaces**.
Copiar lista M3U y XMLTV. El token se muestra una vez; el servidor guarda su hash.
Se añade a las URLs TS de la lista. Cualquiera que posea el enlace puede reproducir
canales y leer la guía, pero no administrar. No publicarlo en chats, capturas ni
logs de proxies. Rotarlo si se divulga. No hay recuperación del token original:
volver a generar revoca todos los enlaces anteriores. No se genera automáticamente
al descargar para evitar revocar reproductores sin aviso.

Solo se admiten URLs de streaming del propio panel al exportar listas protegidas,
para no enviar el token a un servidor externo configurado como base_url. Los logos
locales son públicos: no contienen configuración, programación ni credenciales.

## HTTPS y red

**Login no cifra HTTP.** Usar VPN/LAN de confianza o un proxy HTTPS bien configurado;
no abrir el puerto directamente a Internet. Mantener el servicio en 127.0.0.1
cuando haya proxy en el mismo equipo. El override listen.conf anterior se conserva:
revisar firewall y exposición después de actualizar.

Tras configurar correctamente el certificado y el proxy, añadir un override:

```ini
[Service]
Environment=DVB_PANEL_SECURE_COOKIE=1
```

Hacer daemon-reload y reiniciar. Activa Secure/HSTS y enlaces exportados https.
No activarlo si todavía se entra por HTTP: el navegador no enviará la cookie.
El panel no confía en X-Forwarded-Proto de clientes arbitrarios. El proxy debe
preservar el Host público, permitir SSE sin buffering y no registrar tokens ni
cuerpos de login. El ejemplo es una opción de entorno, no instala TLS por sí solo.

Las salidas UDP y HTTP directas de tbsstream no pasan por el login del panel.
Restringirlas con firewall/red privada; no publicarlas. No se añaden TLS al motor,
DRM ni autorización del proveedor. Newcamd y redistribución requieren permisos.
Estas medidas no sustituyen una auditoría de seguridad ni la revisión de licencias
necesaria antes de comercializar el producto.
