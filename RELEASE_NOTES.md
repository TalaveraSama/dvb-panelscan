## DVB PanelScan 0.5.0 · Sesiones compartidas y PSI

Implementación delimitada de las mejoras identificadas al revisar Tvheadend.
No es una integración completa de su motor ni una garantía de eliminar todos los tirones.

### Cambios

- M3U y streaming manual usan el mismo gestor. Varios clientes del mismo canal comparten un proceso tbsstream, sin puente UDP local.
- Reserva del adaptador: otro SID y los escaneos se rechazan mientras el tuner está ocupado. **Un canal por adaptador**, aunque varios pertenezcan al mismo mux.
- URLs nuevas `/stream/channel/<transponder_id>:<SID>?profile=pass`; identidades equivalentes en M3U y XMLTV. URLs antiguas SID-only siguen funcionando si no hay ambigüedad.
- Perfil `pass` explícito, sin transcodificación; perfiles no soportados se rechazan. La lista no es HLS.
- Colas acotadas por sesión, consumidores independientes, conteo de clientes/bytes/desbordamientos y cierre del proceso al salir el último cliente bajo demanda.
- Inicio manual conserva la sesión hasta detenerla. Reutilización con salidas incompatibles devuelve error; no resintoniza silenciosamente.
- Espera de TS antes de responder HTTP 200; diagnóstico por servicio disponible desde el panel. TS presente no certifica vídeo descifrado.
- PMT conserva descriptores no CA (idiomas, audio, subtítulos, etc.) y elimina CA selectivamente; rechaza actualizaciones incompletas.
- Packetización PSI multipaquete con PUSI, pointer, CC y CRC; tablas iniciales antes del payload seleccionado y repetición basada en CLOCK_MONOTONIC (100 ms mientras llega TS).
- Binario añade `--stdout` para entregar exclusivamente TS al gestor; logs permanecen en stderr.

### Actualizar Ubuntu amd64

```sh
wget -O /tmp/dvb-panelscan_0.5.0_amd64.deb https://github.com/TalaveraSama/dvb-panelscan/releases/download/v0.5.0/dvb-panelscan_0.5.0_amd64.deb
sudo systemctl stop dvb-panel
sudo apt install /tmp/dvb-panelscan_0.5.0_amd64.deb
sudo systemctl start dvb-panel
```

Se conservan los transponders, Newcamd y los overrides systemd (incluido el acceso LAN configurado). Recargar el dashboard y **volver a descargar el M3U8 y XMLTV** para utilizar las identidades nuevas.

Registros nuevos: `/var/lib/dvb-panelscan/stream-<adapter>-<SID>.log`, accesibles con **Ver registro** en Streaming. No se escribe un stream.log global. Detener todas desconecta todas las sesiones gestionadas.

Para el stream directo `:9981/stream.ts`, iniciar manualmente antes de abrir el M3U. Si la sesión bajo demanda ya está activa sin ese puerto, usar su URL del panel o detener y reiniciar con las salidas deseadas. No se cambian salidas de un proceso activo.

### Verificación y límites

Pruebas con procesos reales sin DVB: clientes simultáneos, reutilización manual/HTTP, cierre, conflictos, timeout, error antes de HTTP 200, desbordamiento y M3U/XMLTV. Regresiones C con ASan/UBSan: descriptores/CA, PMT inválida, PSI multipaquete y continuidad, transporte, CSA y framing Newcamd. Instalación/arranque en Ubuntu 22.04 amd64 mediante Actions.

**Pendiente:** generación temporal de CW y cambios de paridad equivalentes a Tvheadend, CSA por lotes, EMM, selección CAID/proveedor en simulcrypt y métricas completas CC/TEI/ECM. Las claves siguen usando la lógica de 0.4.1; no se afirma resolver cortes por período criptográfico ni se ha validado esta versión end-to-end con hardware y acceso autorizado. No hay transcodificación ni varios SID simultáneos por tuner.

El panel sigue sin autenticación; loopback por defecto. No exponer a Internet. Las credenciales permanecen privadas en disco pero se pasan por argv al proceso; un administrador del host puede leerlas. Los logs por servicio no incluyen el comando ni CW en hexadecimal y todavía no tienen rotación automática.
