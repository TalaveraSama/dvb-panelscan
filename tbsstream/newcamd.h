/*
 *  newcamd.h - cliente newcamd (Code Word Client) para descrambling DVB
 *
 *  Portado del protocolo implementado en TVHeadend (src/descrambler/cwc.c).
 *  Solo Linux; depende de OpenSSL (libcrypto) para DES y MD5.
 *
 *  GPLv3.
 */

#ifndef NEWCAMD_H
#define NEWCAMD_H

#include <stdint.h>
#include <stddef.h>

typedef struct newcamd newcamd_t;

/* Conecta y hace el login newcamd.
 *   host/puerto: servidor newcamd (OSCam/CCcam newcamd), normalmente puerto 15050.
 *   deskey: 14 bytes (clave DES de 2x7 bytes). Se pasa igual que TVHeadend.
 *   user/password: credenciales newcamd.
 * Devuelve el cliente o NULL (y escribe el motivo en err).
 */
newcamd_t *newcamd_connect(const char *host, int port,
                           const uint8_t deskey[14],
                           const char *user, const char *password,
                           char *err, size_t errlen);

/* Envía un ECM (sección 0x80/0x81) y espera la CW.
 *   ecm/ecm_len: sección ECM privada completa, sin truncar bytes.
 *   even/odd: 8 bytes cada una (claves de control par/impar).
 * Devuelve 0 si se obtuvo la CW; -1 en error de transporte/protocolo,
 * -2 si el servidor responde sin CW (sin autorización/lector disponible).
 */
int newcamd_send_ecm(newcamd_t *nc, uint16_t sid, uint16_t caid, uint32_t provid,
                     const uint8_t *ecm, int ecm_len,
                     uint8_t even[8], uint8_t odd[8], int timeout_ms);

void newcamd_close(newcamd_t *nc);

#endif /* NEWCAMD_H */
