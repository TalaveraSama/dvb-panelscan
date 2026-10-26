/*
 *  csa.h - descrambler CSA (DVB Common Scrambling Algorithm) vía libdvbcsa
 *
 *  Adaptado de TVHeadend src/descrambler/tvhcsa.c (camino CSA-CBC).
 *  Solo Linux; depende de libdvbcsa.
 *
 *  GPLv3.
 */

#ifndef CSA_H
#define CSA_H

#include <stdint.h>

typedef struct csa csa_t;

typedef struct {
  uint64_t pkt_decrypted, pkt_clear, pkt_nokey, pkt_invalid;
} csa_stats_t;

csa_t *csa_create(void);
/* Externally synchronize shared instances. Reset preserves statistics. */
void csa_clear_keys(csa_t *c);
/* True only when BOTH parities have keys. */
int csa_have_keys(const csa_t *c);
void csa_get_stats(const csa_t *c, csa_stats_t *out);

void csa_set_key_even(csa_t *c, const uint8_t cw[8]);
void csa_set_key_odd (csa_t *c, const uint8_t cw[8]);

/* In-place TS (188 bytes each): return payload packets processed. Missing-key
 * and invalid packets stay untouched. pending counts only missing-key packets. */
int csa_descramble(csa_t *c, uint8_t *ts, int npkts);
int csa_descramble_ex(csa_t *c, uint8_t *ts, int npkts, int *pending);
int csa_descramble_list(csa_t *c, uint8_t **pkts, int npkts);

void csa_free(csa_t *c);

#endif /* CSA_H */
