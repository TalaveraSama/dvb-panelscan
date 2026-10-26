#include "../tbsstream/csa.h"
#include <dvbcsa/dvbcsa.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
/* Compare the bitsliced wrapper against scalar-API encrypted reference data,
 * with both keys, partial blocks, adaptation fields and multiple clusters. */
static void test_mixed_clusters(void)
{
  int n = 4 * dvbcsa_bs_batch_size() + 17;
  uint8_t *ts = malloc((size_t)n * 188), *plain = malloc((size_t)n * 188);
  assert(ts && plain);
  uint8_t keys[2][8] = {{1,2,3,6,4,5,6,15}, {4,5,6,15,7,8,9,24}};
  struct dvbcsa_key_s *key[2] = {dvbcsa_key_alloc(), dvbcsa_key_alloc()};
  assert(key[0] && key[1]);
  for (int i = 0; i < 2; i++) dvbcsa_key_set(keys[i], key[i]);
  for (int i = 0; i < n; i++) {
    uint8_t *p = plain + i*188;
    for (int j = 0; j < 188; j++) p[j] = (i*37+j*13) & 255;
    p[0]=0x47; p[1]=1; p[2]=0; p[3]=0x10 | (i & 15);
    int offset = 4;
    if (i % 3) { /* all payload lengths 1..183 across the batch */
      p[3] |= 0x20; p[4] = 182 - (i % 183); p[5] = 0;
      offset += 1 + p[4];
    }
    memcpy(ts+i*188, p, 188);
    if (i % 7) {
      dvbcsa_encrypt(key[i & 1], ts+i*188+offset, 188-offset);
      ts[i*188+3] |= (i & 1) ? 0xc0 : 0x80;
    }
  }
  csa_t *c = csa_create(); assert(c);
  csa_set_key_even(c, keys[0]); csa_set_key_odd(c, keys[1]);
  csa_descramble(c, ts, n);
  assert(!memcmp(ts, plain, (size_t)n*188));
  csa_free(c); dvbcsa_key_free(key[0]); dvbcsa_key_free(key[1]);
  free(ts); free(plain);
}

int main(void) {
  test_mixed_clusters();
  uint8_t cw[8] = {1,2,3,6,4,5,6,15};
  uint8_t ts[188], expected[188];
  csa_t *c = csa_create();
  struct dvbcsa_key_s *key = dvbcsa_key_alloc();
  dvbcsa_key_set(cw, key);
  for (int odd = 0; odd <= 1; odd++) {
    memset(ts, 0x5a, sizeof(ts));
    ts[0]=0x47; ts[1]=0; ts[2]=0x100 & 0xff; ts[3]=0x10;
    memcpy(expected, ts, sizeof(ts));
    dvbcsa_encrypt(key, ts+4, 184);
    ts[3] |= odd ? 0xc0 : 0x80;
    if (odd) csa_set_key_odd(c, cw); else csa_set_key_even(c, cw);
    csa_descramble(c, ts, 1);
    assert(!memcmp(ts, expected, sizeof(ts)));
  }
  dvbcsa_key_free(key); csa_free(c);
  puts("CSA even/odd tests passed");
  return 0;
}
