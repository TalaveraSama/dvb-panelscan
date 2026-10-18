/* nctest.c - prueba el login newcamd contra el servidor (OSCam). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "newcamd.h"

int main(int argc, char **argv)
{
  if (argc < 6) {
    fprintf(stderr, "uso: %s host port deskey_hex user pass\n", argv[0]);
    return 2;
  }
  const char *host = argv[1];
  int port = atoi(argv[2]);
  const char *hex = argv[3];
  const char *user = argv[4];
  const char *pass = argv[5];

  uint8_t deskey[14] = {0};
  int i = 0;
  for (const char *s = hex; *s && i < 14; ) {
    while (*s && !isxdigit((unsigned char)*s)) s++;
    int u = *s ? (isdigit((unsigned char)*s) ? *s-'0' : tolower((unsigned char)*s)-'a'+10) : 0; if (*s) s++;
    while (*s && !isxdigit((unsigned char)*s)) s++;
    int l = *s ? (isdigit((unsigned char)*s) ? *s-'0' : tolower((unsigned char)*s)-'a'+10) : 0; if (*s) s++;
    deskey[i++] = (uint8_t)((u << 4) | l);
  }

  char err[256];
  newcamd_t *nc = newcamd_connect(host, port, deskey, user, pass, err, sizeof(err));
  if (nc) {
    printf("LOGIN_OK\n");
    newcamd_close(nc);
    return 0;
  }
  printf("LOGIN_FAIL: %s\n", err);
  return 1;
}
