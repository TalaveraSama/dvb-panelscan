## DVB PanelScan 0.4.0

### Ubuntu amd64

Paquete compilado y probado en Ubuntu 22.04 amd64, con dependencias OpenSSL 3 y libdvbcsa del sistema. Destinado a Ubuntu 22.04/24.04 amd64; instalación automatizada comprobada en 22.04.

```sh
sudo apt update
sudo apt install ./dvb-panelscan_0.4.0_amd64.deb
sudo systemctl enable --now dvb-panel
```

Abrir http://127.0.0.1:8081. Para acceso remoto seguro: `ssh -L 8081:127.0.0.1:8081 usuario@servidor`.
Comprobar la descarga con `sha256sum -c SHA256SUMS`.

### Cambios

- Dashboard adaptable con navegación lateral, métricas reales, estados vacíos y errores de conexión.
- Selección de SID para el stream; diagnóstico TCP de la configuración Newcamd guardada.
- Corrección del filtrado de secciones ECM, envío de la sección completa y selección del PID ECM.
- Lectura de descriptores CA en programa y ES; solicitud CARD_DATA después del login.
- Límites de mensajes y validación de longitud de respuestas CW; no se reemplazan claves válidas por mitades nulas.
- Reconexión de Newcamd con espera progresiva y descarte de ECM pendientes tras errores de transporte.
- Archivos de configuración privados y escritura atómica; argumentos secretos omitidos en respuestas de streaming.
- Bloqueo de traversal estático y de POST con Origin ajeno; control del ciclo de vida del proceso.
- Servicio systemd sin root (grupo video), vinculado únicamente a loopback por seguridad.

### Verificación y límites

GitHub Actions compila las herramientas, ejecuta pruebas Python/HTTP y regresiones C con ASan/UBSan para demux, framing Newcamd y CSA par/impar. Instala el `.deb`, inicia el servicio y comprueba `/api/status`.

**No se ha validado el descifrado end-to-end con hardware DVB ni una tarjeta/servidor autorizados.** El test TCP no verifica login ni autorización, y proceso activo no significa vídeo descifrado. En simulcrypt se elige el primer CA; selección explícita CAID/proveedor y EMM quedan pendientes. El panel no tiene autenticación: no exponerlo directamente a Internet. Consultar README para permisos, logs y limitaciones.

Los archivos «Source code» son fuentes, no instaladores. Descargar el `.deb` para Ubuntu amd64.
