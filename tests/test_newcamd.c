#include "../tbsstream/newcamd.c"
#include <assert.h>

int main(void) {
  /* Published md5crypt vector, independent of framing roundtrip. */
  char *hash = crypt_md5("password", "$1$saltqwer$");
  assert(!strcmp(hash, "$1$saltqwer$yCutmodwBoXKLyFtgW5r31"));
  free(hash);
  int sockets[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
  newcamd_t a = {.fd=sockets[0]}, b = {.fd=sockets[1]};
  uint8_t key[14] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14}, spread[16];
  des_key_spread(key, spread);
  des_set_keys(&a, spread); des_set_keys(&b, spread);
  uint8_t message[] = {0x80,0x70,5,1,2,3,4,5}, buf[1024];
  uint16_t seq;
  assert(nc_send_msg(&a, message, sizeof(message), 123, 0x100, 0, &seq) == 0);
  assert(nc_read_msg(&b, buf, sizeof(buf), 500) >= 20);
  assert(!memcmp(buf+12, message, sizeof(message)));
  assert(buf[4] == 0 && buf[5] == 123);
  assert(((buf[2]<<8)|buf[3]) == seq);
  assert(nc_send_msg(&a, message, 1024, 0, 0, 0, NULL) == -1);
  /* A valid encrypted short response must not be accepted as CW padding. */
  uint8_t denied[] = {0x80,0,0};
  b.seq = a.seq; /* next ECM uses the next sequence */
  assert(nc_send_msg(&b, denied, 3, 0, 0, 0, NULL) == 0);
  uint8_t even[8], odd[8];
  assert(newcamd_send_ecm(&a, 123, 0x100, 0, message, sizeof(message), even, odd, 500) == -2);
  close(sockets[0]); close(sockets[1]);
  puts("Newcamd framing tests passed");
  return 0;
}
