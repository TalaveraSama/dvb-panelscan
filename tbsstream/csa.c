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
  int have_even, have_odd;
  csa_stats_t stats;
};

static void csa_set_key(struct dvbcsa_bs_key_s *key, const uint8_t cw[8])
{
  dvbcsa_bs_key_set(cw, key);
}

csa_t *csa_create(void)
{
  csa_t *c = calloc(1, sizeof(*c));
  if (!c) return NULL;
  c->cluster_size = (int)dvbcsa_bs_batch_size();
  if (c->cluster_size < 1) c->cluster_size = 1;
  c->batch_even = calloc((size_t)c->cluster_size + 1, sizeof(*c->batch_even));
  c->batch_odd  = calloc((size_t)c->cluster_size + 1, sizeof(*c->batch_odd));
  c->key_even = dvbcsa_bs_key_alloc();
  c->key_odd  = dvbcsa_bs_key_alloc();
  if (!c->batch_even || !c->batch_odd || !c->key_even || !c->key_odd) {
    csa_free(c); return NULL;
  }
  return c;
}

void csa_set_key_even(csa_t *c, const uint8_t cw[8]) { if (c && cw) { csa_set_key(c->key_even, cw); c->have_even = 1; } }
void csa_set_key_odd (csa_t *c, const uint8_t cw[8]) { if (c && cw) { csa_set_key(c->key_odd, cw); c->have_odd = 1; } }

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

void csa_clear_keys(csa_t *c)
{
  if (!c) return;
  const uint8_t zero[8] = {0};
  csa_set_key(c->key_even, zero); csa_set_key(c->key_odd, zero);
  c->have_even = c->have_odd = 0;
}

int csa_have_keys(const csa_t *c) { return c && c->have_even && c->have_odd; }
void csa_get_stats(const csa_t *c, csa_stats_t *out)
{
  if (out) { if (c) *out = c->stats; else memset(out, 0, sizeof(*out)); }
}

static int descramble(csa_t *c, uint8_t *ts, uint8_t **list, int npkts, int *pending)
{
  int done = 0, left = 0;
  if (pending) *pending = 0;
  if (!c || (!ts && !list) || npkts <= 0) return 0;
  for (int n = 0; n < npkts; n++) {
    uint8_t *pkt = list ? list[n] : ts + (size_t)n * 188;
    if (!pkt) { c->stats.pkt_invalid++; continue; }
    int afc = (pkt[3] >> 4) & 3;
    if (pkt[0] != 0x47 || (pkt[1] & 0x80) || !afc ||
        (afc == 2 && pkt[4] != 183) || (afc == 3 && pkt[4] > 182) ||
        (pkt[3] & 0xc0) == 0x40) {
      c->stats.pkt_invalid++; continue;
    }
    if (!(pkt[3] & 0x80)) { c->stats.pkt_clear++; continue; }
    int odd = !!(pkt[3] & 0x40);
    if (odd ? !c->have_odd : !c->have_even) {
      left++; c->stats.pkt_nokey++; continue; /* Preserve bytes AND scrambling flags. */
    }
    if (afc == 2) { pkt[3] &= 0x3f; continue; }
    int offset = afc == 3 ? 5 + pkt[4] : 4;
    /* DVB-CSA includes a stream cipher for residual bytes. Pass the FULL
     * payload length, not (188-offset)&~7. Truncation leaves corrupt tails. */
    struct dvbcsa_bs_batch_s *b = odd ? c->batch_odd : c->batch_even;
    int *fill = odd ? &c->fill_odd : &c->fill_even;
    b[*fill].data = pkt + offset;
    b[*fill].len = (unsigned)(188 - offset);
    (*fill)++;
    pkt[3] &= 0x3f;
    done++; c->stats.pkt_decrypted++;
    if (*fill == c->cluster_size) csa_flush(c);
  }
  csa_flush(c);
  if (pending) *pending = left;
  return done;
}

int csa_descramble_ex(csa_t *c, uint8_t *ts, int n, int *pending)
{ return descramble(c, ts, NULL, n, pending); }
int csa_descramble(csa_t *c, uint8_t *ts, int n)
{ return csa_descramble_ex(c, ts, n, NULL); }
int csa_descramble_list(csa_t *c, uint8_t **pkts, int n)
{ return descramble(c, NULL, pkts, n, NULL); }

void csa_free(csa_t *c)
{
  if (!c) return;
  if (c->key_even) dvbcsa_bs_key_free(c->key_even);
  if (c->key_odd) dvbcsa_bs_key_free(c->key_odd);
  free(c->batch_even);
  free(c->batch_odd);
  free(c);
}
