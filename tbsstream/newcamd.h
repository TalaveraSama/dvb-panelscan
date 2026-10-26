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
#define NC_ECM_UNSUPPORTED (-4) /* non-CSA CW length; never truncate AES keys */
#define NC_MAX_PROVIDERS 255
typedef struct {
  uint16_t caid;
  unsigned provider_count;
  uint32_t providers[NC_MAX_PROVIDERS];
} newcamd_card_t;
/* Latest advertised non-secret card metadata; no serial number. */
void newcamd_get_card(const newcamd_t *nc, newcamd_card_t *card);
/* Explicit 524/525 selection. The original connect/broker APIs default to 525. */
newcamd_t *newcamd_connect_proto(const char *host, int port, const uint8_t deskey[14],
                                const char *user, const char *password, int protocol,
                                char *err, size_t errlen);
int newcamd_broker_proto(const char *path, const char *host, int port,
                        const uint8_t deskey[14], const char *user, const char *password, int protocol);
int newcamd_keepalive(newcamd_t *nc);

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
 * -2 si el servidor responde sin CW (sin autorización/lector disponible),
 * -4 (NC_ECM_UNSUPPORTED) para formato CW no soportado. Salidas vacías en error.
 */
int newcamd_send_ecm(newcamd_t *nc, uint16_t sid, uint16_t caid, uint32_t provid,
                     const uint8_t *ecm, int ecm_len,
                     uint8_t even[8], uint8_t odd[8], int timeout_ms);

/* Private IPC for panel service workers (not a public network protocol).
 * -3 means the request deadline expired; the stream connection remains framed.
 */
newcamd_t *newcamd_connect_local(const char *path, char *err, size_t errlen);
int newcamd_broker(const char *path, const char *host, int port,
                  const uint8_t deskey[14], const char *user, const char *password);

void newcamd_close(newcamd_t *nc);

#endif /* NEWCAMD_H */
