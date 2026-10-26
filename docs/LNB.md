# LNB universal directo · 0.12.0

En Dashboard → Editar transpondedor → Modo LNB seleccionar **Universal automático
9750/10600**. Detener primero canales del adaptador. Guardar conserva canales/EPG,
y escanear con el modo nuevo. También se aplica al abrir canales en streaming:
no se necesita otro proceso de control del LNB.

| RF | LO | FI | Polarización / voltaje | Tono |
|---|---|---|---|---|
| 12230 V | 10600 | 1630 MHz | V / 13 V | 22 kHz ON |
| 12310 H | 10600 | 1710 MHz | H / 18 V | 22 kHz ON |
| 11174 H | 9750 | 1424 MHz | H / 18 V | 22 kHz OFF |

Umbral de banda alta: RF >= 11700 MHz. Esquema universal Ku estándar. El campo LO
manual queda desactivado en modo universal. TP existentes sin lnb_mode siguen en
manual para no modificar instalaciones que ya estaban funcionando. En manual/fijo
se utiliza el LO indicado y tono OFF. No se ofrece tono manual ON en esta versión.

Selección lineal: V=13 V, H=18 V. L=13 V y R=18 V se conservan como antes;
R no es un alias de V. En el JSON de Astra compartido, la entrada llamada
12230 V indicaba polarization R: confirmar polarización antes de trasladarla.

Los dos motores comparten dvb_lnb.h. Antes de DTV_TUNE se apaga el tono anterior,
se aplica voltaje y espera 300 ms, después se aplica el tono y espera 20 ms.
FE_SET_TONE/FE_SET_VOLTAGE fallidos abortan con mensaje específico. La salida de
escaneo muestra LO/FI/voltaje/tono al sintonizar; no crea archivos de logs.

API: lnb_mode=universal o manual; valor ausente equivale a manual. CLI de ambos
motores: --lnb-universal. Sin esa opción, --lnb-lo conserva modo fijo. El perfil
softcam se configura aparte: no arregla falta de lock. El nombre 61W/70W no mueve
la antena ni selecciona un switch; cada entrada debe recibir la antena adecuada.

No controla puertos DiSEqC, Unicable/SCR/JESS ni otros LO universales personalizados.
Cable directo de una salida twin/quad permite seleccionar su banda y polarización.
No conectar varios adaptadores con voltajes independientes a una única salida
mediante un divisor esperando V/H simultáneos. Un quattro de salidas fijas o un
multiswitch requiere analizar el cableado; no confundirlos con un twin/quad.

Las pruebas simulan ioctls y verifican bandas, umbral, FI, V/H y fallos. No pueden
certificar lock en una TBS/LNB reales desde el entorno de desarrollo. Si sigue sin
lock, revisar orientación, cable, SR, delivery system y que Astra no controle el
mismo frontend. Conservar una prueba por adaptador antes de migrar todos.
