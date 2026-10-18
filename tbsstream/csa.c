/*
 *  csa.c - descrambler CSA vía libdvbcsa (adaptado de tvhcsa.c)
 *
 *  GPLv3.
 */

#include "csa.h"

#include <stdlib.h>
#include <string.h>
#include <dvbcsa/dvbcsa.h>

struct csa {
  struct dvbcsa_bs_key_s *key_even;
  struct dvbcsa_bs_key_s *key_odd;
  struct dvbcsa_bs_batch_s *batch_even;
  struct dvbcsa_bs_batch_s *batch_odd;
  int cluster_size;
  int fill_even;
  int fill_odd;
};

static void csa_set_key(struct dvbcsa_bs_key_s *key, const uint8_t cw[8])
{
  dvbcsa_bs_key_set(cw, key);
}

csa_t *csa_create(void)
{
  csa_t *c = calloc(1, sizeof(*c));
  c->cluster_size = (int)dvbcsa_bs_batch_size();
  if (c->cluster_size < 1) c->cluster_size = 1;
  c->batch_even = calloc((size_t)c->cluster_size + 1, sizeof(*c->batch_even));
  c->batch_odd  = calloc((size_t)c->cluster_size + 1, sizeof(*c->batch_odd));
  c->key_even = dvbcsa_bs_key_alloc();
  c->key_odd  = dvbcsa_bs_key_alloc();
  return c;
}

void csa_set_key_even(csa_t *c, const uint8_t cw[8]) { csa_set_key(c->key_even, cw); }
void csa_set_key_odd (csa_t *c, const uint8_t cw[8]) { csa_set_key(c->key_odd,  cw); }

static void csa_flush(csa_t *c)
{
  if (c->fill_even) {
    c->batch_even[c->fill_even].data = NULL;
    dvbcsa_bs_decrypt(c->key_even, c->batch_even, 184);
    c->fill_even = 0;
  }
  if (c->fill_odd) {
    c->batch_odd[c->fill_odd].data = NULL;
    dvbcsa_bs_decrypt(c->key_odd, c->batch_odd, 184);
    c->fill_odd = 0;
  }
}

void csa_descramble(csa_t *c, uint8_t *ts, int npkts)
{
  for (int n = 0; n < npkts; n++) {
    uint8_t *pkt = ts + n * 188;
    if ((pkt[3] & 0x80) == 0) continue;   /* clear o reservado */

    int ev_od = pkt[3] & 0x40;
    pkt[3] &= 0x3f;                        /* marcarlo como descifrado */

    int offset, len;
    if (pkt[3] & 0x20) {                   /* adaptation field presente */
      if (!(pkt[3] & 0x10)) continue;      /* sin payload */
      offset = 4 + pkt[4] + 1;
      if (offset >= 188) continue;
      len = 188 - offset;
    } else {
      offset = 4;
      len = 184;
    }

    struct dvbcsa_bs_batch_s *b = ev_od ? c->batch_odd : c->batch_even;
    int *fill = ev_od ? &c->fill_odd : &c->fill_even;
    b[*fill].data = pkt + offset;
    b[*fill].len = (unsigned int)len;
    (*fill)++;
    if (*fill == c->cluster_size)
      csa_flush(c);
  }
  csa_flush(c);
}

void csa_free(csa_t *c)
{
  if (!c) return;
  dvbcsa_bs_key_free(c->key_even);
  dvbcsa_bs_key_free(c->key_odd);
  free(c->batch_even);
  free(c->batch_odd);
  free(c);
}
