# Mosaico de canales · 0.10.0

En Canales y EPG se abre un mosaico por transpondedor. El selector permite volver
a la tabla existente. La búsqueda afecta a ambas vistas. No inicia canales ni
retoca la sintonía: las tarjetas inactivas son servicios escaneados sin sesión.

Cada grupo muestra nombre, adaptador, frecuencia, polarización y symbol rate.
Cada tarjeta muestra canal, logo cuando existe, SID, perfil y título EPG disponible.
El título EPG se refresca al volver a entrar en la pestaña o pulsar Actualizar vista;
la telemetría se actualiza aproximadamente cada dos segundos como el streaming.
El contador de activos incluye sesiones en ejecución, aunque tengan errores de CA.
Los recuentos se refieren a canales visibles después del filtro.

## Qué significan las métricas

- kbit/s = diferencia de bytes del worker × 8 / intervalo en milisegundos.
  Incluye el TS de ese servicio, no solo paquetes de vídeo. No es el ancho de
  banda de la salida IP ni del mux completo. Usa unidades decimales.
- Primera muestra o cambio de PID: Midiendo. Intervalos mayores de diez segundos,
  resets de contador y valores inválidos no se convierten en tasas falsas.
- Verde: la telemetría informa CW recientes. No prueba imagen o audio correctos.
- Azul: sesión TS en ejecución sin confirmación de CW recientes.
- Ámbar: espera, cero incremento de bytes o telemetría no disponible.
- Rojo: error de entrada o estado ECM de error reportado.
- Gris: canal inactivo o deshabilitado. No se monitoriza TS sin abrir el canal.

Los estados incluyen texto para no depender solo del color. Si el sondeo falla,
se borra el bitrate anterior y aparece Sin telemetría, no un verde congelado.
Los datos de bitrate se calculan en RAM en el navegador; no se guardan como logs.
No se muestran porcentajes de señal, BER/UNC ni contadores PES porque no están
expuestos por el backend. No se confunden errores ECM con errores de vídeo.

## Acciones

El menú ⋯ de cada tarjeta permite editar perfil/metadatos, abrir TS en navegador,
ver programación, entrar en eventos en vivo del canal y detener solo esa sesión.
Abrir TS utiliza la sesión del administrador. Para un reproductor externo, usar
la lista privada con token desde Seguridad y reproductores, como en 0.9.0.
Los menús no se reconstruyen durante el sondeo, evitando perder foco o selección.
Diseño adaptable a pantalla estrecha. No reutiliza código o recursos de Astra/Cesbo.
