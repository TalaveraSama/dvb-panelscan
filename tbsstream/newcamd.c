/*
 *  newcamd.c - cliente newcamd (Code Word Client)
 *
 *  Portado fielmente de TVHeadend src/descrambler/cwc.c (DES, framing,
 *  derivación de claves, login y mensajes ECM/CW). Se usa OpenSSL para DES y
 *  MD5. El algoritmo de salado de contraseña (crypt_md5) también es de cwc.c.
 *
 *  GPLv3.
 */

#include "newcamd.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <poll.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>

#include <openssl/des.h>
#include <openssl/md5.h>
#include <openssl/rand.h>

#define CWS_NETMSGSIZE 1024
#define CWS_FIRSTCMDNO 0xe0

#define MSG_CLIENT_2_SERVER_LOGIN     (CWS_FIRSTCMDNO + 0)   /* 0xE0 */
#define MSG_CLIENT_2_SERVER_LOGIN_ACK (CWS_FIRSTCMDNO + 1)   /* 0xE1 */
#define MSG_CARD_DATA_REQ             (CWS_FIRSTCMDNO + 3)   /* 0xE3 */
#define MSG_KEEPALIVE                 (CWS_FIRSTCMDNO + 0x1d)/* 0xFD */
#define TVHEADEND_PROTOCOL_ID         0x6502

struct newcamd {
  int fd;
  DES_key_schedule k1, k2;
  uint16_t seq;
  uint8_t confedkey[14];
  char *salted;
};

static void set_err(char *err, size_t errlen, const char *fmt, const char *arg)
{
  if (err && errlen)
    snprintf(err, errlen, "%s%s", fmt, arg ? arg : "");
}

/* Lectura exacta de n bytes con timeout (ms). Devuelve 0 ok, -1 error. */
static int recv_full(int fd, uint8_t *buf, int n, int timeout_ms)
{
  int got = 0;
  while (got < n) {
    struct pollfd pfd = { .fd = fd, .events = POLLIN };
    int r = poll(&pfd, 1, timeout_ms);
    if (r <= 0) return -1;
    ssize_t k = recv(fd, buf + got, n - got, 0);
    if (k <= 0) return -1;
    got += k;
  }
  return 0;
}

static int send_full(int fd, const uint8_t *buf, int n)
{
  int sent = 0;
  while (sent < n) {
    ssize_t k = send(fd, buf + sent, n - sent, MSG_NOSIGNAL);
    if (k <= 0) return -1;
    sent += k;
  }
  return 0;
}

/* ---------------------------------------------------------------------------
 * DES (idéntico a cwc.c)
 * ------------------------------------------------------------------------- */
static void des_key_parity_adjust(uint8_t *key, uint8_t len)
{
  for (int i = 0; i < len; i++) {
    uint8_t parity = 1;
    for (int j = 1; j < 8; j++)
      if ((key[i] >> j) & 0x1) parity = (uint8_t)~parity & 0x01;
    key[i] |= parity;
  }
}

static void des_key_spread(uint8_t *normal, uint8_t *spread)
{
  spread[ 0] = normal[ 0] & 0xfe;
  spread[ 1] = ((normal[ 0] << 7) | (normal[ 1] >> 1)) & 0xfe;
  spread[ 2] = ((normal[ 1] << 6) | (normal[ 2] >> 2)) & 0xfe;
  spread[ 3] = ((normal[ 2] << 5) | (normal[ 3] >> 3)) & 0xfe;
  spread[ 4] = ((normal[ 3] << 4) | (normal[ 4] >> 4)) & 0xfe;
  spread[ 5] = ((normal[ 4] << 3) | (normal[ 5] >> 5)) & 0xfe;
  spread[ 6] = ((normal[ 5] << 2) | (normal[ 6] >> 6)) & 0xfe;
  spread[ 7] = normal[ 6] << 1;
  spread[ 8] = normal[ 7] & 0xfe;
  spread[ 9] = ((normal[ 7] << 7) | (normal[ 8] >> 1)) & 0xfe;
  spread[10] = ((normal[ 8] << 6) | (normal[ 9] >> 2)) & 0xfe;
  spread[11] = ((normal[ 9] << 5) | (normal[10] >> 3)) & 0xfe;
  spread[12] = ((normal[10] << 4) | (normal[11] >> 4)) & 0xfe;
  spread[13] = ((normal[11] << 3) | (normal[12] >> 5)) & 0xfe;
  spread[14] = ((normal[12] << 2) | (normal[13] >> 6)) & 0xfe;
  spread[15] = normal[13] << 1;
  des_key_parity_adjust(spread, 16);
}

static void des_set_keys(newcamd_t *nc, const uint8_t spread[16])
{
  DES_set_key_unchecked((DES_cblock *)spread,     &nc->k1);
  DES_set_key_unchecked((DES_cblock *)(spread+8), &nc->k2);
}

static int des_encrypt(newcamd_t *nc, uint8_t *buffer, int len)
{
  uint8_t checksum = 0, *ptr, padBytes[7];
  DES_cblock ivec;
  int noPadBytes;

  noPadBytes = (8 - ((len - 1) % 8)) % 8;
  if (len + noPadBytes + 1 >= CWS_NETMSGSIZE - 8) return -1;
  if (!RAND_bytes(padBytes, noPadBytes)) return -1;
  for (int i = 0; i < noPadBytes; i++) buffer[len++] = padBytes[i];
  for (int i = 2; i < len; i++) checksum ^= buffer[i];
  buffer[len++] = checksum;
  if (!RAND_bytes((unsigned char *)ivec, 8)) return -1;
  memcpy(buffer + len, ivec, 8);
  for (int i = 2; i < len; i += 8) {
    ptr = buffer + i;
    DES_ncbc_encrypt(ptr, ptr, 8, &nc->k1, &ivec, 1);
    DES_ecb_encrypt((DES_cblock *)ptr, (DES_cblock *)ptr, &nc->k2, 0);
    DES_ecb_encrypt((DES_cblock *)ptr, (DES_cblock *)ptr, &nc->k1, 1);
    memcpy(ivec, ptr, 8);
  }
  return len + 8;
}

static int des_decrypt(newcamd_t *nc, uint8_t *buffer, int len)
{
  DES_cblock ivec, nextIvec;
  uint8_t *ptr, checksum = 0;

  if ((len - 2) % 8 || (len - 2) < 16) return -1;
  len -= 8;
  memcpy(nextIvec, buffer + len, 8);
  for (int i = 2; i < len; i += 8) {
    ptr = buffer + i;
    memcpy(ivec, nextIvec, 8);
    memcpy(nextIvec, ptr, 8);
    DES_ecb_encrypt((DES_cblock *)ptr, (DES_cblock *)ptr, &nc->k1, 0);
    DES_ecb_encrypt((DES_cblock *)ptr, (DES_cblock *)ptr, &nc->k2, 1);
    DES_ncbc_encrypt(ptr, ptr, 8, &nc->k1, &ivec, 0);
  }
  for (int i = 2; i < len; i++) checksum ^= buffer[i];
  if (checksum) return -1;
  return len;
}

/* ---------------------------------------------------------------------------
 * crypt_md5 (de cwc.c, Poul-Henning Kamp)
 * ------------------------------------------------------------------------- */
static unsigned char itoa64[] =
  "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

static void to64(char *s, unsigned long v, int n)
{
  while (--n >= 0) { *s++ = itoa64[v & 0x3f]; v >>= 6; }
}

static char *crypt_md5(const char *pw, const char *salt)
{
  const char *magic = "$1$";
  char *p;
  const char *sp, *ep;
  unsigned char final[16];
  int sl, pl, i, j;
  MD5_CTX ctx, ctx1;
  unsigned long l;

  sp = salt;
  if (!strncmp(sp, magic, strlen(magic))) sp += strlen(magic);
  for (ep = sp; *ep && *ep != '$' && ep < (sp + 8); ep++) continue;
  sl = (int)(ep - sp);

  MD5_Init(&ctx);
  MD5_Update(&ctx, (unsigned char *)pw, strlen(pw));
  MD5_Update(&ctx, (unsigned char *)magic, strlen(magic));
  MD5_Update(&ctx, (unsigned char *)sp, sl);

  MD5_Init(&ctx1);
  MD5_Update(&ctx1, (unsigned char *)pw, strlen(pw));
  MD5_Update(&ctx1, (unsigned char *)sp, sl);
  MD5_Update(&ctx1, (unsigned char *)pw, strlen(pw));
  MD5_Final(final, &ctx1);
  for (pl = (int)strlen(pw); pl > 0; pl -= 16)
    MD5_Update(&ctx, (unsigned char *)final, pl > 16 ? 16 : pl);

  memset(final, 0, sizeof final);

  for (j = 0, i = (int)strlen(pw); i; i >>= 1)
    if (i & 1) MD5_Update(&ctx, (unsigned char *)final + j, 1);
    else       MD5_Update(&ctx, (unsigned char *)pw + j, 1);

  char *passwd = malloc(120);
  strcpy(passwd, magic);
  strncat(passwd, sp, sl);
  strcat(passwd, "$");

  MD5_Final(final, &ctx);

  for (i = 0; i < 1000; i++) {
    MD5_Init(&ctx1);
    if (i & 1) MD5_Update(&ctx1, (unsigned char *)pw, strlen(pw));
    else       MD5_Update(&ctx1, (unsigned char *)final, 16);
    if (i % 3) MD5_Update(&ctx1, (unsigned char *)sp, sl);
    if (i % 7) MD5_Update(&ctx1, (unsigned char *)pw, strlen(pw));
    if (i & 1) MD5_Update(&ctx1, (unsigned char *)final, 16);
    else       MD5_Update(&ctx1, (unsigned char *)pw, strlen(pw));
    MD5_Final(final, &ctx1);
  }

  p = passwd + strlen(passwd);
  l = (final[ 0]<<16) | (final[ 6]<<8) | final[12]; to64(p, l, 4); p += 4;
  l = (final[ 1]<<16) | (final[ 7]<<8) | final[13]; to64(p, l, 4); p += 4;
  l = (final[ 2]<<16) | (final[ 8]<<8) | final[14]; to64(p, l, 4); p += 4;
  l = (final[ 3]<<16) | (final[ 9]<<8) | final[15]; to64(p, l, 4); p += 4;
  l = (final[ 4]<<16) | (final[10]<<8) | final[ 5]; to64(p, l, 4); p += 4;
  l =                     final[11]                ; to64(p, l, 2); p += 2;
  *p = '\0';
  memset(final, 0, sizeof final);
  return passwd;
}

/* ---------------------------------------------------------------------------
 * Framing de mensajes (cwc_send_msg / cwc_read_message)
 * ------------------------------------------------------------------------- */
static int nc_send_msg(newcamd_t *nc, const uint8_t *msg, int len,
                       uint16_t sid, uint16_t caid, uint32_t provid,
                       uint16_t *_seq)
{
  if (len < 3 || len > CWS_NETMSGSIZE - 32) return -1;
  uint8_t buf[CWS_NETMSGSIZE];
  uint16_t seq = ++nc->seq;

  buf[0] = buf[1] = 0;
  buf[2] = (uint8_t)(seq >> 8);
  buf[3] = (uint8_t)(seq & 0xff);
  buf[4] = (uint8_t)(sid >> 8);
  buf[5] = (uint8_t)(sid & 0xff);
  buf[6] = (uint8_t)(caid >> 8);
  buf[7] = (uint8_t)(caid & 0xff);
  buf[8] = (uint8_t)(provid >> 16);
  buf[9] = (uint8_t)(provid >> 8);
  buf[10] = (uint8_t)(provid & 0xff);
  buf[11] = (uint8_t)(provid >> 24);

  memcpy(buf + 12, msg, len);
  len += 12;

  int total = des_encrypt(nc, buf, len);
  if (total <= 0) return -1;
  buf[0] = (uint8_t)((total - 2) >> 8);
  buf[1] = (uint8_t)((total - 2) & 0xff);

  if (send_full(nc->fd, buf, total) < 0) return -1;
  if (_seq) *_seq = seq;
  return 0;
}

/* Lee un mensaje, lo descifra y devuelve su longitud (>= 12). */
static int nc_read_msg(newcamd_t *nc, uint8_t *buf, int maxlen, int timeout_ms)
{
  uint8_t hdr[2];
  if (recv_full(nc->fd, hdr, 2, timeout_ms) < 0) return -1;
  int msglen = (hdr[0] << 8) | hdr[1];
  if (msglen < 12 || msglen > maxlen - 2) return -1;
  if (recv_full(nc->fd, buf + 2, msglen, 1000) < 0) return -1;
  buf[0] = hdr[0]; buf[1] = hdr[1];
  return des_decrypt(nc, buf, msglen + 2);
}

/* ---------------------------------------------------------------------------
 * Conexión y login
 * ------------------------------------------------------------------------- */
static int nc_connect_tcp(const char *host, int port, char *err, size_t errlen)
{
  struct addrinfo hints, *res;
  char portstr[16];
  snprintf(portstr, sizeof(portstr), "%d", port);
  memset(&hints, 0, sizeof(hints));
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;

  int r = getaddrinfo(host, portstr, &hints, &res);
  if (r != 0) { set_err(err, errlen, "getaddrinfo: ", gai_strerror(r)); return -1; }

  int fd = -1;
  for (struct addrinfo *ai = res; ai; ai = ai->ai_next) {
    fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
    if (fd < 0) continue;
    struct timeval tv = { .tv_sec = 5 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) break;
    close(fd); fd = -1;
  }
  freeaddrinfo(res);
  if (fd < 0) set_err(err, errlen, "connect: ", strerror(errno));
  return fd;
}

newcamd_t *newcamd_connect(const char *host, int port, const uint8_t deskey[14],
                           const char *user, const char *password,
                           char *err, size_t errlen)
{
  int fd = nc_connect_tcp(host, port, err, errlen);
  if (fd < 0) return NULL;

  newcamd_t *nc = calloc(1, sizeof(*nc));
  nc->fd = fd;
  memcpy(nc->confedkey, deskey, 14);
  nc->salted = crypt_md5(password ? password : "", "$1$abcdefgh$");

  /* 1) clave de login del servidor (14 bytes) */
  uint8_t loginkey[14];
  if (recv_full(fd, loginkey, 14, 5000) < 0) {
    set_err(err, errlen, "no se recibió la clave de login", NULL);
    goto fail;
  }

  /* 2) clave de login = DES key ^ login key */
  uint8_t des14[14], spread[16];
  for (int i = 0; i < 14; i++) des14[i] = nc->confedkey[i] ^ loginkey[i];
  des_key_spread(des14, spread);
  des_set_keys(nc, spread);

  /* 3) enviar login */
  size_t ul = strlen(user ? user : "") + 1;
  size_t pl = strlen(nc->salted) + 1;
  if (ul + pl > 255) { set_err(err, errlen, "usuario/contraseña muy largos", NULL); goto fail; }
  uint8_t login[256];
  login[0] = MSG_CLIENT_2_SERVER_LOGIN;
  login[1] = 0;
  login[2] = (uint8_t)(ul + pl);
  memcpy(login + 3, user ? user : "", ul);
  memcpy(login + 3 + ul, nc->salted, pl);
  if (nc_send_msg(nc, login, (int)(ul + pl + 3), TVHEADEND_PROTOCOL_ID, 0, 0, NULL) < 0) {
    set_err(err, errlen, "error enviando login", NULL); goto fail;
  }

  /* 4) esperar login ACK */
  uint8_t buf[CWS_NETMSGSIZE];
  int r = nc_read_msg(nc, buf, sizeof(buf), 5000);
  if (r < 15 || buf[12] != MSG_CLIENT_2_SERVER_LOGIN_ACK) {
    set_err(err, errlen, "login rechazado", NULL); goto fail;
  }

  /* 5) clave de sesión = DES key ^ salted_password */
  memcpy(des14, nc->confedkey, 14);
  for (size_t i = 0; i < strlen(nc->salted); i++)
    des14[i % 14] ^= (uint8_t)nc->salted[i];
  des_key_spread(des14, spread);
  des_set_keys(nc, spread);

  /* Complete the handshake before submitting ECMs (as in Tvheadend). */
  const uint8_t request[3] = {MSG_CARD_DATA_REQ, 0, 0};
  if (nc_send_msg(nc, request, 3, 0, 0, 0, NULL) < 0 ||
      (r = nc_read_msg(nc, buf, sizeof(buf), 5000)) < 15 ||
      buf[12] != MSG_CARD_DATA_REQ + 1) {
    set_err(err, errlen, "no se recibieron datos de tarjeta", NULL);
    goto fail;
  }
  return nc;

fail:
  close(fd);
  if (nc) { free(nc->salted); free(nc); }
  return NULL;
}

/* ---------------------------------------------------------------------------
 * ECM -> CW
 * ------------------------------------------------------------------------- */
int newcamd_send_ecm(newcamd_t *nc, uint16_t sid, uint16_t caid, uint32_t provid,
                     const uint8_t *ecm, int ecm_len,
                     uint8_t even[8], uint8_t odd[8], int timeout_ms)
{
  uint16_t seq = 0;
  if (nc_send_msg(nc, ecm, ecm_len, sid, caid, provid, &seq) < 0)
    return -1;

  uint8_t buf[CWS_NETMSGSIZE];
  for (;;) {
    int r = nc_read_msg(nc, buf, sizeof(buf), timeout_ms);
    if (r < 15) return -1;
    uint8_t type = buf[12];
    if (type == 0x80 || type == 0x81) {
      uint16_t rseq = ((uint16_t)buf[2] << 8) | buf[3];
      if (rseq != seq) continue;   /* respuesta a otro ECM */
      uint8_t *m = buf + 12;       /* m[0]=tipo, m[1..2]=reservado */
      int mlen = ((m[1] & 0x0f) << 8) | m[2];
      if (mlen != 16 || r < 31) return -1;
      mlen += 3;
      if (mlen < 19) return -1;    /* 1 + 2 + 8 + 8 como mínimo */
      memcpy(even, m + 3, 8);      /* buf[15..22] */
      memcpy(odd,  m + 11, 8);     /* buf[23..30] */
      return 0;
    }
    /* resto de mensajes (keepalive/card data): ignorar y seguir */
  }
}

void newcamd_close(newcamd_t *nc)
{
  if (!nc) return;
  if (nc->fd >= 0) close(nc->fd);
  free(nc->salted);
  free(nc);
}
