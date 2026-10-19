# Newcamd en Tvheadend: conexión compartida y arranque del descrambler

## Fuente y alcance

Revisión del repositorio oficial `tvheadend/tvheadend`, master fijado a
[`8837853a69aa822b0203eb3909c5912dc831312e`](https://github.com/tvheadend/tvheadend/commit/8837853a69aa822b0203eb3909c5912dc831312e),
comparado con dvb-panelscan 0.5.1. No se ha conectado al servidor OSCam del
usuario ni medido su señal. Esta revisión no cambia el runtime ni publica
otro instalador.

## 1. Una conexión por instancia de cliente, varios contextos de servicio

`cc_thread()` establece un socket TCP, lo asigna a `cc_fd`, ejecuta
`cc_session()` y reconecta si esa sesión termina. `cwc_init_session()` realiza
el login, deriva la clave de sesión y solicita/decodifica CARD_DATA.

`cc_service_start()` mantiene los servicios en `cc_services`, cada uno con
sus PID ECM y secciones. Antes de asociarlos comprueba tarjetas/CAID disponibles
y condiciones de selección. No crea un socket nuevo por cada servicio.

Por tanto, diez servicios activos pueden enviar ECM sobre una conexión de una
misma instancia Newcamd. Varias instancias/configuraciones, cuentas o puertos
pueden tener otras conexiones. El número de canales visibles en OSCam no debe
confundirse automáticamente con el número de sockets/login.

Referencias:
- [Conexión y reconexión: cclient.c, cc_thread](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L736-L809).
- [Inicio de servicios: cc_service_start](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L1120-L1215).
- [Login y datos de tarjeta: cwc_init_session](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cwc.c#L562-L609).

## 2. Multiplexación de peticiones y respuestas ECM

`cwc_send_ecm()` extrae el SID del servicio y envía SID, CAID y proveedor.
`cwc_send_msg()` asigna un número de secuencia y forma/cifra el mensaje.
La sección guarda la secuencia en `es_seq`.

`cc_write_message()` encola la escritura. El bucle `cc_session()` atiende
entrada del socket, cola de salida, keepalive y desconexión: no hace una
espera síncrona de respuesta por cada canal dentro del bucle de vídeo.

Al llegar una respuesta 0x80/0x81, `cwc_running_reply()` extrae la secuencia y
`cc_find_pending_section()` busca la sección pendiente entre los servicios.
También contempla respuestas duplicadas/inesperadas. `cc_ecm_reply()` marca
el resultado de esa sección, mide el tiempo y notifica claves al descrambler
del servicio correcto; respuestas negativas no se convierten en claves.

```text
servicio A -> ECM(seq 41, SID A) --+
servicio B -> ECM(seq 42, SID B) --+--> cola -> socket Newcamd -> OSCam
                                 |
respuesta(seq 42) -> sección pendiente B -> descrambler B
respuesta(seq 41) -> sección pendiente A -> descrambler A
```

Referencias:
- [Framing y secuencia](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cwc.c#L216-L272).
- [Envío por servicio](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cwc.c#L643-L656).
- [Cola, recepción y keepalive](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L621-L730).
- [Correlación y duplicados](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L538-L565).
- [Respuesta ECM y entrega de claves](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/cclient.c#L405-L528).

## 3. Recibir CW no equivale a aplicarla inmediatamente al payload

`descrambler_keys()` almacena por separado las mitades par/impar no vacías,
sus tiempos y qué cambió. Añade una marca de actualización mediante
`descrambler_data_add_key()`. Según el estado de la cola/paridad, la marca
puede insertarse al principio o al final.

En el recorrido de descifrado se consumen esas marcas y `key_flush()` vacía
el lote CSA anterior antes de aplicar las actualizaciones. Los datos en cola
tienen timestamp y se examinan con la información de paridad, cambios y
claves tardías (`key_update`, `key_changed`, `key_late`). No es simplemente
«llega respuesta de red, sobrescribir ambos contextos CSA y continuar».

Esto describe el orden y las marcas de ese código, no un historial ilimitado
de CW ni una garantía de que cualquier respuesta tardía sea recuperable.

Referencias:
- [Cola de datos y marcas de claves](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L108-L159).
- [Recepción de CW y marcas](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L694-L824).
- [Estado temporal y flush CSA](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L893-L986).
- [Consumo de cola y descifrado](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L1090-L1220).

## 4. Arranque: no elegir la paridad por un solo paquete

El código comenta explícitamente que el primer paquete TS puede ser erróneo.
Cuando no ha establecido el inicio de clave, comprueba una muestra de la
cola con `dr_initial_paritycheck`; puede cortar paquetes iniciales que no
permiten establecer una paridad coherente. Además limita el buffer de espera
cuando todavía no tiene las claves.

En los valores base observados, `interval` empieza en 10 000 ms y
`paritycheck` en 20 paquetes, modificables mediante hints del sistema CA.
El margen ECM se deriva del intervalo. **Ese valor de 10 segundos no es una
espera fija de arranque ni demuestra por qué el vídeo del usuario se estabiliza
a los 10 segundos.** El chequeo inicial se expresa en paquetes, no en una pausa
incondicional de diez segundos.

Referencias:
- [Valores y hints del sistema CA](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L403-L468).
- [Chequeo inicial y límite del buffer](https://github.com/tvheadend/tvheadend/blob/8837853a69aa822b0203eb3909c5912dc831312e/src/descrambler/descrambler.c#L1230-L1277).

## 5. Diferencias verificadas en dvb-panelscan 0.5.1

- Cada proceso `tbsstream` mantiene su propio cliente Newcamd. Compartir un
  proceso entre espectadores del mismo canal no centraliza ECM de procesos
  de canales distintos.
- `newcamd_send_ecm()` envía y espera una respuesta síncrona en el hilo CA.
  Es asíncrono respecto al vídeo, pero no es el cliente multicanal de Tvheadend.
- CARD_DATA se solicita, pero la selección de CA de la PMT no aprovecha toda
  la información de tarjetas/proveedores como `cc_service_start()`.
- Las mitades no nulas recibidas se aplican directamente a CSA bajo mutex.
  No hay marcas de CW en la misma línea temporal que los paquetes pendientes.
- Se conserva el orden del servicio y se usa CSA por lotes desde 0.5.1, pero
  no hay comprobación inicial de paridad equivalente a la de Tvheadend.
- Una cola cuyo primer paquete necesita una mitad aún no disponible espera
  esa clave, o termina descartando al llenarse. Un comienzo en el borde de un
  período criptográfico es una hipótesis para el arranque problemático; no se
  ha confirmado con registros/captura del usuario.
- La respuesta HTTP puede comenzar al recibir PSI, antes de que haya vídeo
  descifrado. «TS recibido» no significa que el decodificador tenga un punto
  de acceso válido o una CW utilizable.

## 6. Siguiente implementación: dos problemas separados

### A. Sincronización inicial y actualizaciones de claves

Prioridad para estudiar el problema de arranque de un canal: tiempos por
paquete/lote, muestra inicial de paridad, recepción de cada mitad, marcas de
actualización en orden y descarte explícito/acotado de datos iniciales no
utilizables. Conservar audio/PCR y no aplicar claves futuras a payload antiguo.
No copiar un umbral de Tvheadend sin adaptar su significado a nuestra cola,
que incluye también paquetes claros del servicio.

Pruebas necesarias: entrada en mitad de período, primera CW con una mitad
nula, ECM anticipado/tardío, cambio de paridad, respuestas negativas y
reconexión. Comparar payload descifrado y orden, no solo que lleguen bytes.

### B. Cliente Newcamd centralizado

Necesita un servicio/broker con un único dueño del socket, cola de escritura,
registro de peticiones pendientes por secuencia y contexto independiente por
servicio/CA/proveedor. Los contextos deben distinguir también muxes que repitan
SID. Requiere límites, expiración y cancelación, tratamiento de wrap de
secuencia, invalidación al reconectar, keepalive y entrega de CW al contexto
correcto. Compartir un descriptor entre procesos o serializar todo sin estado
por servicio no basta.

Pruebas necesarias con servidor simulado: varios servicios, respuestas en
orden inverso, duplicadas, retrasadas, reconexión y secuencias reutilizadas;
ninguna CW de un servicio debe llegar a otro. IPC local restringido y sin
imprimir CW/credenciales en logs.

Centralizar Newcamd no implementa por sí solo varios SID por tuner: eso exige
además un motor DVB por mux con extracción y contexto CA por servicio, o usar
Tvheadend como backend real. Tampoco añade HLS ni repara señal/TS dañado.

## Conclusión

La diferencia más relevante para los primeros segundos no es abrir diez
conexiones ni añadir HLS: es sincronizar la paridad inicial y la aplicación de
CW con los paquetes. La conexión compartida sirve al diseño multicanal, pero
no prueba ni resuelve por sí sola el arranque de un único canal. El caso real
requiere correlacionar logs ECM/TS desde el inicio y una captura antes/después
de estabilizarse; no se declara una nueva corrección sin esa verificación.
