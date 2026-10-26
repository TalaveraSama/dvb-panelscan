/* Local-only DVB-CSA diagnostic. GPLv3. No network, credentials or CW output. */
#include "../csa.h"
#include <dvbcsa/dvbcsa.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int selftest(void)
{
  int ok = 0, n = 3 * (int)dvbcsa_bs_batch_size() + 191, pending = 0;
  uint8_t keys[2][8] = {{1,2,3,6,4,5,6,15}, {4,5,6,15,7,8,9,24}};
  uint8_t *plain = malloc((size_t)n*188), *ts = malloc((size_t)n*188);
  uint8_t **list = malloc((size_t)n*sizeof(*list));
  csa_t *c = csa_create();
  struct dvbcsa_key_s *k[2] = {dvbcsa_key_alloc(), dvbcsa_key_alloc()};
  if (!plain || !ts || !list || !c || !k[0] || !k[1]) goto end;
  for (int i=0; i<2; i++) dvbcsa_key_set(keys[i], k[i]);
  for (int i=0; i<n; i++) {
    uint8_t *p = plain+i*188;
    for (int j=0; j<188; j++) p[j]=(i*37+j*13)&255;
    p[0]=0x47; p[1]=1; p[2]=0; p[3]=0x10 | (i&15);
    int off=4;
    if (i%3) { p[3]|=0x20; p[4]=182-(i%183); p[5]=0; off=5+p[4]; }
    memcpy(ts+i*188,p,188);
    dvbcsa_encrypt(k[i&1],ts+i*188+off,188-off);
    ts[i*188+3] |= (i&1) ? 0xc0 : 0x80;
    list[i]=ts+i*188;
  }
  if (csa_descramble_ex(c,ts,n,&pending)!=0 || pending!=n) goto end;
  csa_set_key_even(c,keys[0]); csa_set_key_odd(c,keys[1]);
  if (!csa_have_keys(c) || csa_descramble_list(c,list,n)!=n || memcmp(ts,plain,(size_t)n*188)) goto end;
  csa_stats_t stats; csa_get_stats(c,&stats);
  if (stats.pkt_nokey!=(uint64_t)n || stats.pkt_decrypted!=(uint64_t)n) goto end;
  csa_clear_keys(c);
  if (csa_have_keys(c)) goto end;
  ok=1;
end:
  for (int i=0; i<2; i++) if (k[i]) dvbcsa_key_free(k[i]);
  csa_free(c); free(plain); free(ts); free(list);
  return ok;
}

int main(int argc, char **argv)
{
  if (argc<2 || argc>3 || strcmp(argv[1],"--selftest") || (argc==3 && strcmp(argv[2],"--json"))) {
    fputs("Usage: dvb-ncdiag --selftest [--json]\nLocal only: no network or credentials accepted.\n",stderr);
    return 2;
  }
  int ok=selftest();
  if (argc==3) printf("{\"ok\":%s,\"csa\":%s,\"network_probe\":false}\n",ok?"true":"false",ok?"true":"false");
  else puts(ok ? "CSA self-test OK (even/odd, full payload, mixed clusters, missing keys, reset)" : "CSA self-test FAILED");
  return ok ? 0 : 1;
}
