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

csa_t *csa_create(void);

void csa_set_key_even(csa_t *c, const uint8_t cw[8]);
void csa_set_key_odd (csa_t *c, const uint8_t cw[8]);

/* Descifra in-place un buffer de npkts paquetes TS (188 bytes c/u). */
void csa_descramble(csa_t *c, uint8_t *ts, int npkts);

void csa_free(csa_t *c);

#endif /* CSA_H */
