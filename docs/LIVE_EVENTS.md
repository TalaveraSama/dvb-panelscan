# Eventos en vivo · 0.7.1

Abrir **Eventos en vivo** antes de iniciar/cerrar un canal. También se puede
entrar desde su botón en Streaming con el filtro de TP:SID ya seleccionado.
Se muestran hora, nivel, canal/nombre/adaptador, mensaje y detalle de ECM.

## Sin grabación

Los procesos escriben stderr a pipes, no a archivos. Un lector acotado lo drena
siempre para evitar bloquearlos y actualizar los indicadores ECM en memoria.
Solo determinados mensajes se transforman en eventos estructurados seguros;
no se transmite texto arbitrario ni ECM, CW o credenciales.

El bus no conserva eventos si no hay espectadores. Al suscribirse empieza vacío.
Cada visor mantiene una cola transitoria máxima de 500 eventos; si se retrasa,
se omiten los más antiguos y se informa. No bloquea el productor ni los otros
visores. Máximo 16 conexiones SSE. Los eventos no van al stderr del panel ni a
journald; tampoco se mantiene el antiguo registro HTTP de accesos por canal.

La pantalla conserva hasta 500 eventos exclusivamente en la memoria de la página.
No usa almacenamiento local/sesión, descargas ni base de datos. Se conecta solo
en esa vista y con la página visible. Cambiar de sección, esconder/cerrar la
página cierra EventSource y borra su lista; la desconexión se detecta en el
servidor mediante heartbeat y elimina su cola. Un nuevo visor no recibe replay.
La actualización visual se agrupa para evitar reconstruir la tabla por cada ECM.

## Eventos disponibles

- Inicio/finalización de worker y captura, con código de salida.
- ECM con/sin CW no nula, latencia y resultado; sin valores de claves.
- Servicio libre/cifrado detectado por PMT.
- Lock, ausencia de lock y errores comunes de dispositivo.
- Conexión/reconexión/error de acceso condicional y deadline del broker.
- Cola llena o consumidor/entrada del canal interrumpidos.
- Estadísticas TS: TEI, errores CC, descartes de servicio/ECM y pendientes.
  Son contadores acumulados del multiplex que recibe el worker, no prueba de
  pérdidas RF exclusivas de ese canal.

Los filtros operan sobre lo recibido en esta visita, nunca consultan un archivo.
Los eventos anteriores a abrir la vista y los ocurridos durante un corte no se
pueden recuperar. Los indicadores ECM normales siguen funcionando sin la vista,
porque conservan solo métricas de salud recientes en RAM, no una transcripción.

## Compatibilidad

`GET /api/events`: Server-Sent Events, `Cache-Control: no-store`, sin IDs de
replay. Envía `ready` al conectar y mensajes JSON `data` para eventos nuevos.
El proxy debe permitir conexiones largas sin buffering (`X-Accel-Buffering: no`).
`GET /api/stream/log` devuelve 410, en lugar de leer registros antiguos.

Los logs de versiones anteriores no se borran automáticamente: la actualización
no los abre ni amplía. Su eliminación es una decisión del administrador. Los
archivos de configuración siguen guardándose normalmente. El panel continúa sin
autenticación: usar solo una LAN confiable o un proxy seguro.
