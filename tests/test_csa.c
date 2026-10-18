#include "../tbsstream/csa.h"
#include <dvbcsa/dvbcsa.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
int main(void) {
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
