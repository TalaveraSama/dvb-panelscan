# EPG de uno o varios días · 0.8.0

## Uso

1. En **Canales y EPG**, elegir de 1 a 14 días y guardar.
2. Dejar un canal del transpondedor abierto para recibir el multiplex, o escanear
   ese transpondedor cuando no tenga canales activos. No hace falta abrir todos
   sus servicios: la captura compartida contiene el EIT de ese mux.
3. Pulsar Actualizar vista, o volver a entrar en la pestaña. **Programas** muestra
   los eventos guardados por canal, con horarios locales del navegador.
4. XMLTV exporta esas mismas horas en UTC. Los nombres y perfiles se editan con
   **Editar**, tanto aquí como en Dashboard. Para dos satélites, crear dos perfiles
   Newcamd y asignarlos a los canales correspondientes, identificados por TP:SID.

## Qué significa conservar días

Ventana móvil de las próximas N×24 horas, incluyendo programas ya empezados que
aún no terminan. No es un archivo de emisiones pasadas. Se eliminan eventos
vencidos y los que empiezan fuera de ventana. Reducir el período los descarta;
aumentarlo requiere recibir nuevamente programación más lejana. Valor inicial:
un día. Máximo 14 días y 50 000 eventos en total.

La emisión decide cuánto EPG hay y cuánto tarda su ciclo. Si el satélite solo
emite ahora/después, la guía seguirá siendo corta aunque elijas 7 o 14 días. Un
escaneo corto puede recibir una parte; dejar el mux activo permite seguir
completándola. Sin canales activos no se inicia una recepción de fondo ni se
resintonizan adaptadores ocupados. No hay un raspador ni descarga de guías externas.

## Fuentes y aislamiento

- Escáner C: EIT actual 0x4E y schedule 0x50–0x5F, eventos con inicio MJD/BCD y
  duración BCD. Incluye eventos reales en su JSON para que el panel los ingiera.
- Recepción en vivo: una lectura EIT por captura de mux, no por espectador.
  Ensamblado por PID, CRC MPEG-2, punteros PUSI, errores TEI/continuidad y
  secciones de hasta 4096 bytes. Se enlaza el TSID con la PAT del mismo mux.
- Se ignoran EIT-other (0x4F y 0x60–0x6F): no se mezclan SIDs iguales de otros
  satélites. Solo se atribuyen eventos a servicios escaneados de esa sintonía.
- Títulos/horarios se actualizan por event_id y canal TP:SID. La captura en vivo
  admite short-event y fragmentos extended-event; el escáner importa descripción
  corta. Se admiten selectores de texto DVB frecuentes y acentos occidentales;
  no es una implementación de todos los juegos de caracteres privados.

## Persistencia y XMLTV

`/var/lib/dvb-panelscan/epg.json`: configuración y eventos, privado 0600, sustitución
atómica cada 30 segundos si hubo cambios y al parar normalmente (SIGTERM/Ctrl+C).
Un corte de energía puede perder los últimos 30 segundos. El lector TS no escribe
archivos: solo actualiza datos acotados en RAM. Los antiguos resultados de escaneo
siguen disponibles, pero sus títulos sin hora no se convierten en programas con
horarios inventados. Sin datos temporales fiables, XMLTV incluye el canal y no
inventa bloques de una hora.

Esto **no cambia Eventos en vivo**: los logs de canales siguen sin guardarse. La
programación EPG sí se guarda porque es la opción solicitada. No se guardan CW,
contraseñas ni ECM dentro del EPG. HTML/XML se escapan y se eliminan controles no
válidos. XMLTV excluye canales deshabilitados.

## API y límites

- GET/POST `/api/epg/settings`: `{"days": 1}` hasta 14; tipos/rangos validados.
- GET `/api/epg/channel?channel=TP:SID`: días y eventos recibidos de ese canal.
- GET `/api/epg`: resumen de canales y número de programas guardados.
- GET `/api/epg.xml`: XMLTV con tiempos reales, filtrable por transpondedor.

La vista de programas muestra como máximo 500 filas; XMLTV usa todos los eventos
que entren en la ventana. No se añaden un programador de grabaciones, un selector
de satélite/DiSEqC, ni recopilación de guías de muxes que el hardware no recibe.
Las pruebas usan EIT sintético: el EPG real depende de las tablas del proveedor.
