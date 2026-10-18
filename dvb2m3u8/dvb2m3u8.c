/*
 *  dvb2m3u8 - minimal DVB/TBS scanner -> M3U8 playlist generator
 *
 *  Sintoniza un adaptador DVB (incluidas tarjetas TBS) usando la API DVB de
 *  Linux, demultiplexa el flujo MPEG-TS, extrae las tablas PSI/SI
 *  (PAT/PMT/SDT/NIT/EIT) y genera una lista M3U8 con los servicios/canales
 *  encontrados, sus nombres (SDT) y el now/next de la EPG (EIT).
 *
 *  La lógica de parseo y las constantes siguen el código de TVHeadend
 *  (src/input/mpegts/dvb.h, dvb_psi.c, epggrab/module/eit.c), reducidas a lo
 *  esencial para que esto compile de forma independiente (solo libc + headers
 *  DVB del kernel).
 *
 *  Solo Linux. Compilar:  gcc -O2 -Wall -o dvb2m3u8 dvb2m3u8.c
 *
 *  Licencia: GPLv3 (por reutilizar lógica de TVHeadend).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <poll.h>
#include <getopt.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <linux/dvb/frontend.h>
#include <linux/dvb/version.h>
#include <linux/dvb/dmx.h>

/* =========================================================================
 *  Constantes DVB (de TVHeadend src/input/mpegts/dvb.h)
 * ========================================================================= */
#define PID_PAT  0x0000
#define PID_NIT  0x0010
#define PID_SDT  0x0011
#define PID_EIT  0x0012

#define TID_PAT  0x00
#define TID_PMT  0x02
#define TID_NIT_ACTUAL  0x40
#define TID_NIT_OTHER   0x41
#define TID_SDT_ACTUAL  0x42
#define TID_SDT_OTHER   0x46
#define TID_EIT_PF_ACTUAL  0x4E
#define TID_EIT_PF_OTHER   0x4F
#define TID_EIT_SCHED_ACTUAL 0x50
#define TID_EIT_SCHED_OTHER  0x60

#define DESC_NETWORK_NAME 0x40
#define DESC_SERVICE      0x48
#define DESC_SHORT_EVENT  0x4D

/* =========================================================================
 *  Utilidades
 * ========================================================================= */

static int verbose = 0;
#define LOGV(...) do { if (verbose) fprintf(stderr, __VA_ARGS__); } while (0)

static uint16_t be16(const uint8_t *p) { return ((uint16_t)p[0] << 8) | p[1]; }

static uint8_t bcd2i(uint8_t b) { return (uint8_t)((b >> 4) * 10 + (b & 0x0F)); }

/* MJD + BCD (DVB) -> tiempo Unix (UTC) */
static time_t dvb_time(uint16_t mjd, const uint8_t *bcd)
{
  int days = (int)mjd - 40587;            /* MJD 40587 == 1970-01-01 */
  int hh = bcd2i(bcd[0]), mm = bcd2i(bcd[1]), ss = bcd2i(bcd[2]);
  return (time_t)days * 86400 + hh * 3600 + mm * 60 + ss;
}

/* CRC-32 MPEG-2 (polinomio 0x04C11DB7, sin reflexión, resultado 0 si es válido) */
static uint32_t crc32_mpeg(const uint8_t *data, int len)
{
  uint32_t crc = 0xFFFFFFFFu;
  for (int i = 0; i < len; i++) {
    crc ^= (uint32_t)data[i] << 24;
    for (int j = 0; j < 8; j++)
      crc = (crc & 0x80000000u) ? (crc << 1) ^ 0x04C11DB7u : (crc << 1);
  }
  return crc;
}

static int valid_utf8(const uint8_t *s, int len)
{
  int i = 0;
  while (i < len) {
    uint8_t c = s[i];
    if (c < 0x80) { i++; continue; }
    int n;
    if ((c & 0xE0) == 0xC0) n = 1;
    else if ((c & 0xF0) == 0xE0) n = 2;
    else if ((c & 0xF8) == 0xF0) n = 3;
    else return 0;
    if (i + n >= len) return 0;
    for (int k = 1; k <= n; k++)
      if ((s[i + k] & 0xC0) != 0x80) return 0;
    i += n + 1;
  }
  return 1;
}

static void utf8_put(char *dst, size_t *pos, size_t cap, unsigned cp)
{
  if (*pos + 4 >= cap) return;
  if (cp < 0x80) dst[(*pos)++] = (char)cp;
  else if (cp < 0x800) {
    dst[(*pos)++] = (char)(0xC0 | (cp >> 6));
    dst[(*pos)++] = (char)(0x80 | (cp & 0x3F));
  } else if (cp < 0x10000) {
    dst[(*pos)++] = (char)(0xE0 | (cp >> 12));
    dst[(*pos)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
    dst[(*pos)++] = (char)(0x80 | (cp & 0x3F));
  } else {
    dst[(*pos)++] = (char)(0xF0 | (cp >> 18));
    dst[(*pos)++] = (char)(0x80 | ((cp >> 12) & 0x3F));
    dst[(*pos)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
    dst[(*pos)++] = (char)(0x80 | (cp & 0x3F));
  }
}

/* Composición de diacrítico (0xC1..0xCF) + letra base -> punto de código.
 * Cubre los casos más habituales (español y Europa occidental). */
struct compose { uint8_t diac, base; unsigned cp; };
static const struct compose composes[] = {
  /* grave 0xC1 */
  {0xC1,'A',0xC0},{0xC1,'E',0xC8},{0xC1,'I',0xCC},{0xC1,'O',0xD2},{0xC1,'U',0xD9},
  {0xC1,'a',0xE0},{0xC1,'e',0xE8},{0xC1,'i',0xEC},{0xC1,'o',0xF2},{0xC1,'u',0xF9},
  /* acute 0xC2 */
  {0xC2,'A',0xC1},{0xC2,'E',0xC9},{0xC2,'I',0xCD},{0xC2,'O',0xD3},{0xC2,'U',0xDA},
  {0xC2,'Y',0xDD},{0xC2,'a',0xE1},{0xC2,'e',0xE9},{0xC2,'i',0xED},{0xC2,'o',0xF3},
  {0xC2,'u',0xFA},{0xC2,'y',0xFD},{0xC2,'c',0x107},{0xC2,'C',0x106},{0xC2,'n',0x144},
  {0xC2,'N',0x143},{0xC2,'s',0x15B},{0xC2,'S',0x15A},{0xC2,'z',0x17A},{0xC2,'Z',0x179},
  {0xC2,'l',0x13A},{0xC2,'L',0x139},{0xC2,'r',0x155},{0xC2,'R',0x154},
  /* circumflex 0xC3 */
  {0xC3,'A',0xC2},{0xC3,'E',0xCA},{0xC3,'I',0xCE},{0xC3,'O',0xD4},{0xC3,'U',0xDB},
  {0xC3,'a',0xE2},{0xC3,'e',0xEA},{0xC3,'i',0xEE},{0xC3,'o',0xF4},{0xC3,'u',0xFB},
  /* tilde 0xC4 */
  {0xC4,'A',0xC3},{0xC4,'N',0xD1},{0xC4,'O',0xD5},
  {0xC4,'a',0xE3},{0xC4,'n',0xF1},{0xC4,'o',0xF5},
  /* diaeresis 0xC8 */
  {0xC8,'A',0xC4},{0xC8,'E',0xCB},{0xC8,'I',0xCF},{0xC8,'O',0xD6},{0xC8,'U',0xDC},
  {0xC8,'Y',0x178},{0xC8,'a',0xE4},{0xC8,'e',0xEB},{0xC8,'i',0xEF},{0xC8,'o',0xF6},
  {0xC8,'u',0xFC},{0xC8,'y',0xFF},
  /* ring 0xCA */
  {0xCA,'A',0xC5},{0xCA,'a',0xE5},{0xCA,'U',0x16E},{0xCA,'u',0x16F},
  /* cedilla 0xCB */
  {0xCB,'C',0xC7},{0xCB,'c',0xE7},{0xCB,'S',0x15E},{0xCB,'s',0x15F},
  {0xCB,'G',0x122},{0xCB,'g',0x123},{0xCB,'K',0x136},{0xCB,'k',0x137},
  {0xCB,'L',0x13B},{0xCB,'l',0x13C},{0xCB,'N',0x145},{0xCB,'n',0x146},
  {0xCB,'R',0x156},{0xCB,'r',0x157},{0xCB,'T',0x162},{0xCB,'t',0x163},
  /* caron 0xCF */
  {0xCF,'C',0x10C},{0xCF,'c',0x10D},{0xCF,'D',0x10E},{0xCF,'d',0x10F},
  {0xCF,'E',0x11A},{0xCF,'e',0x11B},{0xCF,'L',0x13D},{0xCF,'l',0x13E},
  {0xCF,'N',0x147},{0xCF,'n',0x148},{0xCF,'R',0x158},{0xCF,'r',0x159},
  {0xCF,'S',0x160},{0xCF,'s',0x161},{0xCF,'T',0x164},{0xCF,'t',0x165},
  {0xCF,'Z',0x17D},{0xCF,'z',0x17E},
};

static unsigned compose_char(uint8_t diac, uint8_t base)
{
  for (size_t i = 0; i < sizeof(composes)/sizeof(composes[0]); i++)
    if (composes[i].diac == diac && composes[i].base == (uint8_t)base)
      return composes[i].cp;
  return 0; /* no se pudo componer */
}

/* Decodifica texto DVB (ISO 6937 por defecto) a UTF-8, best-effort. */
static void decode_text(char *dst, size_t cap, const uint8_t *src, int len)
{
  size_t pos = 0;
  dst[0] = 0;
  if (len <= 0) return;

  if (valid_utf8(src, len)) {
    int n = len < (int)cap - 1 ? len : (int)cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
    return;
  }

  for (int i = 0; i < len && pos + 4 < cap; i++) {
    uint8_t c = src[i];
    if (c < 0x80) { dst[pos++] = (char)c; continue; }
    if (c >= 0xC1 && c <= 0xCF) {          /* diacrítico combinante */
      if (i + 1 < len) {
        uint8_t base = src[++i];
        unsigned cp = compose_char(c, base);
        if (cp) { utf8_put(dst, &pos, cap, cp); continue; }
        if (base < 0x80) { dst[pos++] = (char)base; continue; }
      }
      continue;
    }
    /* resto de 0xA0..0xFF (muestra razonable, sin tabla completa) */
    if (c >= 0xA0) { dst[pos++] = (char)c; continue; } /* aprox. Latin-1 */
  }
  dst[pos] = 0;
}

/* =========================================================================
 *  Modelo de datos: servicios encontrados
 * ========================================================================= */
typedef struct service {
  uint16_t sid;
  uint16_t pmt_pid;
  uint16_t pcr_pid;
  uint8_t  type;              /* service_type de la SDT */
  char name[128];
  char provider[128];
  char now[256];
  char next_title[256];
  struct service *next;
} service_t;

static service_t *services = NULL;
static char network_name[128];

static service_t *service_find(uint16_t sid)
{
  for (service_t *s = services; s; s = s->next)
    if (s->sid == sid) return s;
  return NULL;
}

static service_t *service_add(uint16_t sid)
{
  service_t *s = service_find(sid);
  if (!s) {
    s = calloc(1, sizeof(*s));
    s->sid = sid;
    s->next = services;
    services = s;
  }
  return s;
}

static service_t *service_by_pmt(uint16_t pid)
{
  for (service_t *s = services; s; s = s->next)
    if (s->pmt_pid == pid) return s;
  return NULL;
}

/* =========================================================================
 *  Parseo de tablas PSI/SI (equivalentes a dvb_psi.c, reducido)
 * ========================================================================= */

static void track_pid(uint16_t pid);   /* definida más abajo */

static void parse_pat(const uint8_t *b, int len)
{
  if (len < 8) return;
  uint16_t tsid = be16(b);
  (void)tsid;
  for (int i = 5; i + 4 <= len; i += 4) {
    uint16_t prog = be16(b + i);
    uint16_t pid  = ((uint16_t)(b[i+2] & 0x1F) << 8) | b[i+3];
    if (prog == 0) continue;                    /* NIT PID, ya conocido */
    service_add(prog)->pmt_pid = pid;
    track_pid(pid);
  }
}

static void parse_pmt(uint16_t pid, const uint8_t *b, int len)
{
  service_t *s = service_by_pmt(pid);
  if (len < 9) return;
  uint16_t pcr = ((uint16_t)(b[5] & 0x1F) << 8) | b[6];
  if (s) s->pcr_pid = pcr;
}

static void parse_sdt(const uint8_t *b, int len)
{
  if (len < 8) return;
  int i = 8;                                  /* tras tsid/onid/versión */
  while (i + 5 <= len) {
    uint16_t sid = be16(b + i);
    int dll = ((b[i+3] & 0x0F) << 8) | b[i+4];
    const uint8_t *d = b + i + 5;
    int dlen = dll;
    if (i + 5 + dll > len) break;

    service_t *s = service_add(sid);
    for (int j = 0; j + 2 <= dlen; ) {
      uint8_t tag = d[j], dl = d[j+1];
      if (j + 2 + dl > dlen) break;
      const uint8_t *dd = d + j + 2;
      if (tag == DESC_SERVICE && dl >= 3) {
        s->type = dd[0];
        int plen = dd[1];
        if (plen > 0 && 2 + plen < dl)
          decode_text(s->provider, sizeof(s->provider), dd + 2, plen);
        int nlen = dd[2 + plen];
        if (nlen > 0 && 3 + plen + nlen <= dl)
          decode_text(s->name, sizeof(s->name), dd + 3 + plen, nlen);
      }
      j += 2 + dl;
    }
    i += 5 + dll;
  }
}

static void parse_nit(const uint8_t *b, int len)
{
  if (len < 7) return;
  int dll = ((b[5] & 0x0F) << 8) | b[6];
  if (7 + dll > len) dll = len - 7;
  const uint8_t *d = b + 7;
  for (int j = 0; j + 2 <= dll; ) {
    uint8_t tag = d[j], dl = d[j+1];
    if (j + 2 + dl > dll) break;
    if (tag == DESC_NETWORK_NAME && dl > 0)
      decode_text(network_name, sizeof(network_name), d + j + 2, dl);
    j += 2 + dl;
  }
}

static void parse_eit(uint8_t tid, const uint8_t *b, int len)
{
  /* now/next solo en present/following (0x4E/0x4F); el schedule se ignora */
  if (tid != TID_EIT_PF_ACTUAL && tid != TID_EIT_PF_OTHER) return;
  if (len < 11) return;
  uint16_t sid = be16(b);
  int i = 11;
  int ev = 0;
  while (i + 12 <= len) {
    uint16_t eid = be16(b + i);
    (void)eid;
    time_t start = dvb_time(be16(b + i + 2), b + i + 4);
    int dll = ((b[i+10] & 0x0F) << 8) | b[i+11];
    const uint8_t *d = b + i + 12;
    int dlen = dll;
    if (i + 12 + dll > len) break;

    char title[256]; title[0] = 0;
    for (int j = 0; j + 2 <= dlen; ) {
      uint8_t tag = d[j], dl = d[j+1];
      if (j + 2 + dl > dlen) break;
      if (tag == DESC_SHORT_EVENT && dl >= 5) {
        int nlen = d[j + 5];
        if (nlen > 0 && 6 + nlen <= dl)
          decode_text(title, sizeof(title), d + j + 6, nlen);
      }
      j += 2 + dl;
    }

    service_t *s = service_find(sid);
    if (s && title[0]) {
      if (ev == 0 && !s->now[0])  { snprintf(s->now,  sizeof(s->now),  "%s", title); }
      if (ev == 1 && !s->next_title[0]) { snprintf(s->next_title, sizeof(s->next_title), "%s", title); }
    }
    ev++;
    i += 12 + dll;
    (void)start;
  }
}

/* =========================================================================
 *  Reensamblado de secciones por PID
 * ========================================================================= */
#define MAX_TRACKED 64
static uint16_t tracked[MAX_TRACKED];
static int ntracked = 0;

static int is_tracked(uint16_t pid)
{
  for (int i = 0; i < ntracked; i++)
    if (tracked[i] == pid) return 1;
  return 0;
}

static void track_pid(uint16_t pid)
{
  if (pid == 0x1FFF || pid == 0) return;
  if (ntracked < MAX_TRACKED && !is_tracked(pid))
    tracked[ntracked++] = pid;
}

typedef struct { uint16_t pid; uint8_t buf[4096]; int len; } secbuf_t;
static secbuf_t secbufs[MAX_TRACKED];
static int nsecbufs = 0;

static secbuf_t *secbuf_get(uint16_t pid)
{
  for (int i = 0; i < nsecbufs; i++)
    if (secbufs[i].pid == pid) return &secbufs[i];
  if (nsecbufs >= MAX_TRACKED) return NULL;
  secbuf_t *sb = &secbufs[nsecbufs++];
  memset(sb, 0, sizeof(*sb));
  sb->pid = pid;
  return sb;
}

static void handle_section(uint16_t pid, const uint8_t *s)
{
  uint8_t tid = s[0];
  int slen = ((s[1] & 0x0F) << 8) | s[2];
  const uint8_t *body = s + 3;
  int blen = slen - 4;
  if (blen < 0) return;

  switch (pid) {
    case PID_PAT: if (tid == TID_PAT) parse_pat(body, blen); break;
    case PID_SDT: if (tid == TID_SDT_ACTUAL || tid == TID_SDT_OTHER) parse_sdt(body, blen); break;
    case PID_NIT: if (tid == TID_NIT_ACTUAL || tid == TID_NIT_OTHER) parse_nit(body, blen); break;
    case PID_EIT:
      parse_eit(tid, body, blen);
      break;
    default:
      if (tid == TID_PMT) parse_pmt(pid, body, blen);
      break;
  }
}

static void process_sections(secbuf_t *sb)
{
  while (sb->len >= 3) {
    int slen = ((sb->buf[1] & 0x0F) << 8) | sb->buf[2];
    int total = 3 + slen;
    if (slen > 1021 || total <= 3) { sb->len = 0; return; }  /* sección corrupta */
    if (sb->len < total) break;                    /* sección incompleta */
    if ((sb->buf[1] & 0x80) && crc32_mpeg(sb->buf, total) == 0)
      handle_section(sb->pid, sb->buf);            /* solo secciones largas */
    memmove(sb->buf, sb->buf + total, sb->len - total);
    sb->len -= total;
  }
}

static void feed_section(uint16_t pid, const uint8_t *data, int len, int pusi)
{
  secbuf_t *sb = secbuf_get(pid);
  if (!sb) return;

  if (pusi) {
    if (len < 1) return;
    int pointer = data[0];
    const uint8_t *p = data + 1;
    int plen = len - 1;
    if (pointer > 0) {
      int take = pointer < plen ? pointer : plen;
      if (sb->len + take < (int)sizeof(sb->buf)) {
        memcpy(sb->buf + sb->len, p, take);
        sb->len += take;
        process_sections(sb);
      }
      p += take;
      plen -= take;
    }
    if (plen > 0) {
      if (sb->len + plen < (int)sizeof(sb->buf)) {
        memcpy(sb->buf + sb->len, p, plen);
        sb->len += plen;
        process_sections(sb);
      }
    }
  } else {
    if (sb->len + len < (int)sizeof(sb->buf)) {
      memcpy(sb->buf + sb->len, data, len);
      sb->len += len;
      process_sections(sb);
    }
  }
}

/* =========================================================================
 *  Lectura del transport stream
 * ========================================================================= */
static void process_packet(const uint8_t *pkt)
{
  if (pkt[0] != 0x47) return;
  int pusi = (pkt[1] & 0x40) != 0;
  uint16_t pid = ((uint16_t)(pkt[1] & 0x1F) << 8) | pkt[2];
  int afc = (pkt[3] >> 4) & 0x03;
  if (!is_tracked(pid)) return;
  int off = 4;
  if (afc == 0x02) return;               /* solo adaptation field */
  if (afc == 0x03) off += 1 + pkt[4];    /* adaptation + payload */
  if (off > 184) return;
  feed_section(pid, pkt + off, 188 - off, pusi);
}

static void process_buffer(uint8_t *buf, int len)
{
  int off = -1;
  for (int i = 0; i + 376 < len && i < 188; i++) {
    if (buf[i] == 0x47 && buf[i+188] == 0x47 && buf[i+376] == 0x47) { off = i; break; }
  }
  if (off < 0) return;
  for (int p = off; p + 188 <= len; p += 188)
    process_packet(buf + p);
}

/* =========================================================================
 *  Sintonización DVB (FE_SET_PROPERTY)
 * ========================================================================= */
typedef struct {
  int adapter, frontend;
  int delsys;          /* SYS_DVBT / DVBT2 / DVBC_ANNEX_A / DVBS / DVBS2 */
  int64_t freq_hz;     /* frecuencia de satélite; si lnb_lo_hz=0 es la FI */
  int64_t lnb_lo_hz;   /* LO del LNB (0 = sin conversión, freq ya es FI) */
  uint32_t bandwidth_hz;
  uint32_t symbol_rate;
  int mod, fec, fec_hp, fec_lp, tx_mode, guard, hier, rolloff;
  char pol;             /* 'H','V','L','R' */
  int timeout_s;
  int with_epg;
  int with_json;
  const char *outfile;
  const char *base_url;
} opts_t;

static void dtv(struct dtv_property *p, int *n, uint32_t cmd, uint32_t data)
{
  p[*n].cmd = cmd;
  p[*n].u.data = data;
  (*n)++;
}

static int frontend_tune(int fd, const opts_t *o)
{
  struct dtv_property p[32];
  int n = 0;

  dtv(p, &n, DTV_CLEAR, 0);
  dtv(p, &n, DTV_DELIVERY_SYSTEM, (uint32_t)o->delsys);
  /* Conversión frecuencia de satélite -> FI con el LO del LNB (si aplica).
     El driver TBS (TBSECP3) espera la FI en kHz, no en Hz. */
  int64_t if_hz = o->freq_hz;
  if (o->lnb_lo_hz > 0 && o->freq_hz > o->lnb_lo_hz)
    if_hz = o->freq_hz - o->lnb_lo_hz;
  dtv(p, &n, DTV_FREQUENCY, (uint32_t)(if_hz / 1000));
  dtv(p, &n, DTV_INVERSION, INVERSION_AUTO);

  switch (o->delsys) {
    case SYS_DVBT:
    case SYS_DVBT2:
      dtv(p, &n, DTV_BANDWIDTH_HZ, o->bandwidth_hz ? o->bandwidth_hz : 8000000);
      dtv(p, &n, DTV_CODE_RATE_HP, o->fec_hp ? (uint32_t)o->fec_hp : FEC_AUTO);
      dtv(p, &n, DTV_CODE_RATE_LP, o->fec_lp ? (uint32_t)o->fec_lp : FEC_AUTO);
      dtv(p, &n, DTV_MODULATION, o->mod ? (uint32_t)o->mod : QAM_AUTO);
      dtv(p, &n, DTV_TRANSMISSION_MODE, o->tx_mode ? (uint32_t)o->tx_mode : TRANSMISSION_MODE_AUTO);
      dtv(p, &n, DTV_GUARD_INTERVAL, o->guard ? (uint32_t)o->guard : GUARD_INTERVAL_AUTO);
      dtv(p, &n, DTV_HIERARCHY, o->hier ? (uint32_t)o->hier : HIERARCHY_AUTO);
      break;

    case SYS_DVBC_ANNEX_A:
      dtv(p, &n, DTV_MODULATION, o->mod ? (uint32_t)o->mod : QAM_AUTO);
      dtv(p, &n, DTV_SYMBOL_RATE, o->symbol_rate);
      dtv(p, &n, DTV_INNER_FEC, o->fec ? (uint32_t)o->fec : FEC_AUTO);
      break;

    case SYS_DVBS:
      dtv(p, &n, DTV_MODULATION, o->mod ? (uint32_t)o->mod : QPSK);
      dtv(p, &n, DTV_SYMBOL_RATE, o->symbol_rate);
      dtv(p, &n, DTV_INNER_FEC, o->fec ? (uint32_t)o->fec : FEC_AUTO);
      break;
    case SYS_DVBS2:
      dtv(p, &n, DTV_MODULATION, o->mod ? (uint32_t)o->mod : QPSK);
      dtv(p, &n, DTV_SYMBOL_RATE, o->symbol_rate);
      dtv(p, &n, DTV_INNER_FEC, o->fec ? (uint32_t)o->fec : FEC_AUTO);
      dtv(p, &n, DTV_ROLLOFF, o->rolloff ? (uint32_t)o->rolloff : ROLLOFF_35);
      dtv(p, &n, DTV_PILOT, PILOT_AUTO);
      break;
  }

  dtv(p, &n, DTV_TUNE, 0);

  struct dtv_properties cmd = { .num = (unsigned)n, .props = p };
  if (ioctl(fd, FE_SET_PROPERTY, &cmd) < 0) {
    fprintf(stderr, "FE_SET_PROPERTY: %s\n", strerror(errno));
    return -1;
  }

  /* Polarización LNB por voltaje (13V vertical/izq, 18V horizontal/der) */
  if (o->delsys == SYS_DVBS || o->delsys == SYS_DVBS2) {
    fe_sec_voltage_t v = (o->pol == 'V' || o->pol == 'L' || o->pol == 'v' || o->pol == 'l')
                       ? SEC_VOLTAGE_13 : SEC_VOLTAGE_18;
    ioctl(fd, FE_SET_VOLTAGE, v);
  }
  return 0;
}

static int wait_lock(int fd, int timeout_s)
{
  fe_status_t status;
  time_t deadline = time(NULL) + timeout_s;
  while (time(NULL) < deadline) {
    if (ioctl(fd, FE_READ_STATUS, &status) == 0) {
      if (status & FE_HAS_LOCK) return 0;
    }
    usleep(200000);
  }
  return -1;
}

static void print_signal(int fd)
{
  struct dtv_property p;
  struct dtv_properties cmd = { .num = 1, .props = &p };
  p.cmd = DTV_STAT_SIGNAL_STRENGTH;
  p.u.st.len = 1;
  p.u.st.stat[0].svalue = 0;
  if (ioctl(fd, FE_GET_PROPERTY, &cmd) == 0 && p.u.st.stat[0].svalue)
    LOGV("señal: %lld\n", (long long)p.u.st.stat[0].svalue);
}

/* =========================================================================
 *  Salida M3U8 / JSON
 * ========================================================================= */
static void sanitize(char *dst, size_t cap, const char *src)
{
  size_t j = 0;
  for (size_t i = 0; src[i] && j + 1 < cap; i++) {
    if (src[i] == '"' || src[i] == '\r' || src[i] == '\n') continue;
    dst[j++] = src[i];
  }
  dst[j] = 0;
}

static void json_str(FILE *out, const char *s)
{
  fputc('"', out);
  for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
    switch (*p) {
      case '"':  fputs("\\\"", out); break;
      case '\\': fputs("\\\\", out); break;
      case '\n': fputs("\\n", out); break;
      case '\r': fputs("\\r", out); break;
      case '\t': fputs("\\t", out); break;
      default:
        if (*p < 0x20) fprintf(out, "\\u%04x", *p);
        else fputc(*p, out);
    }
  }
  fputc('"', out);
}

static void emit_json(const opts_t *o, FILE *out)
{
  fprintf(out, "{\n  \"adapter\": %d,\n  \"frequency\": %lld,\n",
          o->adapter, (long long)o->freq_hz);
  fputs("  \"network\": ", out); json_str(out, network_name); fputs(",\n", out);
  fputs("  \"services\": [\n", out);
  int first = 1;
  for (service_t *s = services; s; s = s->next) {
    if (!s->name[0]) snprintf(s->name, sizeof(s->name), "Service %u", (unsigned)s->sid);
    fprintf(out, "%s    {\"sid\": %u, \"name\": ", first ? "" : ",\n", (unsigned)s->sid);
    json_str(out, s->name);
    fputs(", \"provider\": ", out); json_str(out, s->provider[0] ? s->provider : network_name);
    fprintf(out, ", \"type\": %u", s->type);
    if (s->now[0])  { fputs(", \"now\": ", out);  json_str(out, s->now); }
    if (s->next_title[0]) { fputs(", \"next\": ", out); json_str(out, s->next_title); }
    fputc('}', out);
    first = 0;
  }
  fputs("\n  ]\n}\n", out);
}

static void emit_m3u8(const opts_t *o, FILE *out)
{
  char nm[160], pv[160];
  fprintf(out, "#EXTM3U\n");
  for (service_t *s = services; s; s = s->next) {
    if (!s->name[0]) snprintf(s->name, sizeof(s->name), "Service %u", (unsigned)s->sid);
    sanitize(nm, sizeof(nm), s->name);
    sanitize(pv, sizeof(pv), s->provider[0] ? s->provider : (network_name[0] ? network_name : "DVB"));
    const char *radio = (s->type == 0x02 || s->type == 0x0A) ? " radio=\"true\"" : "";
    fprintf(out,
      "#EXTINF:-1 tvg-id=\"%u\" tvg-name=\"%s\" group-title=\"%s\"%s,%s\n",
      (unsigned)s->sid, nm, pv, radio, nm);
    if (o->base_url)
      fprintf(out, "%s%u\n", o->base_url, (unsigned)s->sid);
    else
      fprintf(out, "dvb://adapter%d/%lld/%u\n", o->adapter, (long long)o->freq_hz, (unsigned)s->sid);
  }
}

/* =========================================================================
 *  main
 * ========================================================================= */
static void usage(const char *argv0)
{
  fprintf(stderr,
    "Uso: %s -t <tipo> --freq <Hz> [opciones]\n"
    "\n"
    "  -a, --adapter N      adaptador /dev/dvb/adapterN (def 0)\n"
    "  -F, --frontend N     frontend N (def 0)\n"
    "  -t, --type T         dvbt | dvbt2 | dvbc | dvbs | dvbs2 (def dvbt)\n"
    "      --freq Hz        frecuencia de satélite (o FI si --lnb-lo 0)\n"
    "      --freq-mhz MHz   frecuencia de satélite en MHz\n"
    "      --lnb-lo MHz     LO del LNB (def 9750; 0 = la freq ya es FI)\n"
    "\n"
    "  DVB-T/T2:\n"
    "      --bandwidth Hz   ancho de banda (def 8000000)\n"
    "      --fec-hp / --fec-lp   FEC (AUTO,1_2,2_3,3_4,5_6,7_8)\n"
    "\n"
    "  DVB-C:\n"
    "      --symbol-rate R  symbol rate (obligatorio)\n"
    "      --fec            FEC (AUTO,1_2,2_3,3_4,5_6,7_8)\n"
    "\n"
    "  DVB-S/S2:\n"
    "      --symbol-rate R  symbol rate (obligatorio)\n"
    "      --pol P          polarización H|V|L|R (def H)\n"
    "      --fec            FEC (AUTO,1_2,2_3,3_4,5_6,7_8,9_10)\n"
    "\n"
    "  -T, --timeout S      segundos de escaneo (def 15)\n"
    "      --no-epg         no extraer now/next de la EPG (EIT)\n"
    "  -o, --output FILE    fichero de salida (def stdout)\n"
    "      --base-url URL   prefijo de la URL de cada canal\n"
    "      --json           salida JSON en vez de M3U8\n"
    "  -v, --verbose        más información por stderr\n"
    "  -h, --help\n", argv0);
}

static int parse_fec(const char *s)
{
  if (!s || !strcmp(s, "AUTO")) return FEC_AUTO;
  if (!strcmp(s, "1_2")) return FEC_1_2;
  if (!strcmp(s, "2_3")) return FEC_2_3;
  if (!strcmp(s, "3_4")) return FEC_3_4;
  if (!strcmp(s, "5_6")) return FEC_5_6;
  if (!strcmp(s, "7_8")) return FEC_7_8;
  if (!strcmp(s, "8_9")) return FEC_8_9;
  if (!strcmp(s, "9_10")) return FEC_9_10;
  return -1;
}

int main(int argc, char **argv)
{
  opts_t o;
  memset(&o, 0, sizeof(o));
  o.adapter = 0;
  o.frontend = 0;
  o.delsys = SYS_DVBT;
  o.timeout_s = 15;
  o.with_epg = 1;
  o.pol = 'H';
  o.lnb_lo_hz = 9750000000LL;   /* universal Ku, banda baja (9750 MHz) */
  o.fec = o.fec_hp = o.fec_lp = 0;

  char *type = NULL, *fec = NULL, *fec_hp = NULL, *fec_lp = NULL;
  char *pols = NULL;
  double freq_mhz = 0;

  enum { OPT_FREQ=1000, OPT_FREQ_MHZ, OPT_BW, OPT_SR, OPT_FEC, OPT_FEC_HP,
         OPT_FEC_LP, OPT_POL, OPT_NOEPG, OPT_BASEURL, OPT_JSON, OPT_LNBLO };

  static const struct option longopts[] = {
    {"adapter",    required_argument, 0, 'a'},
    {"frontend",   required_argument, 0, 'F'},
    {"type",       required_argument, 0, 't'},
    {"freq",       required_argument, 0, OPT_FREQ},
    {"freq-mhz",   required_argument, 0, OPT_FREQ_MHZ},
    {"bandwidth",  required_argument, 0, OPT_BW},
    {"symbol-rate",required_argument, 0, OPT_SR},
    {"fec",        required_argument, 0, OPT_FEC},
    {"fec-hp",     required_argument, 0, OPT_FEC_HP},
    {"fec-lp",     required_argument, 0, OPT_FEC_LP},
    {"pol",        required_argument, 0, OPT_POL},
    {"timeout",    required_argument, 0, 'T'},
    {"no-epg",     no_argument,       0, OPT_NOEPG},
    {"output",     required_argument, 0, 'o'},
    {"base-url",   required_argument, 0, OPT_BASEURL},
    {"json",       no_argument,       0, OPT_JSON},
    {"lnb-lo",     required_argument, 0, OPT_LNBLO},
    {"verbose",    no_argument,       0, 'v'},
    {"help",       no_argument,       0, 'h'},
    {0,0,0,0}
  };

  int c;
  while ((c = getopt_long(argc, argv, "a:F:t:T:o:vh", longopts, NULL)) != -1) {
    switch (c) {
      case 'a': o.adapter = atoi(optarg); break;
      case 'F': o.frontend = atoi(optarg); break;
      case 't': type = optarg; break;
      case OPT_FREQ: o.freq_hz = (int64_t)strtoll(optarg, NULL, 10); break;
      case OPT_FREQ_MHZ: freq_mhz = atof(optarg); break;
      case OPT_BW: o.bandwidth_hz = (uint32_t)strtoul(optarg, NULL, 10); break;
      case OPT_SR: o.symbol_rate = (uint32_t)strtoul(optarg, NULL, 10); break;
      case OPT_FEC: fec = optarg; break;
      case OPT_FEC_HP: fec_hp = optarg; break;
      case OPT_FEC_LP: fec_lp = optarg; break;
      case OPT_POL: pols = optarg; break;
      case 'T': o.timeout_s = atoi(optarg); break;
      case OPT_NOEPG: o.with_epg = 0; break;
      case 'o': o.outfile = optarg; break;
      case OPT_BASEURL: o.base_url = optarg; break;
      case OPT_JSON: o.with_json = 1; break;
      case OPT_LNBLO: o.lnb_lo_hz = (int64_t)(atof(optarg) * 1000000.0); break;
      case 'v': verbose = 1; break;
      case 'h': usage(argv[0]); return 0;
      default: usage(argv[0]); return 1;
    }
  }

  if (freq_mhz > 0) o.freq_hz = (int64_t)(freq_mhz * 1000000.0);

  if (!type) type = "dvbt";
  if (!strcmp(type, "dvbt"))  o.delsys = SYS_DVBT;
  else if (!strcmp(type, "dvbt2")) o.delsys = SYS_DVBT2;
  else if (!strcmp(type, "dvbc"))  o.delsys = SYS_DVBC_ANNEX_A;
  else if (!strcmp(type, "dvbs"))  o.delsys = SYS_DVBS;
  else if (!strcmp(type, "dvbs2")) o.delsys = SYS_DVBS2;
  else { fprintf(stderr, "tipo desconocido: %s\n", type); return 1; }

  if (!o.freq_hz) { fprintf(stderr, "falta --freq o --freq-mhz\n"); return 1; }
  if ((o.delsys == SYS_DVBC_ANNEX_A || o.delsys == SYS_DVBS || o.delsys == SYS_DVBS2)
      && !o.symbol_rate) {
    fprintf(stderr, "para %s hace falta --symbol-rate\n", type);
    return 1;
  }

  o.fec    = fec    ? parse_fec(fec)    : FEC_AUTO;
  o.fec_hp = fec_hp ? parse_fec(fec_hp) : FEC_AUTO;
  o.fec_lp = fec_lp ? parse_fec(fec_lp) : FEC_AUTO;

  if (pols) {
    if (*pols == 'V' || *pols == 'v')      o.pol = 'V';
    else if (*pols == 'L' || *pols == 'l') o.pol = 'L';
    else if (*pols == 'R' || *pols == 'r') o.pol = 'R';
    else                                   o.pol = 'H';
  }

  /* Apertura de dispositivos */
  char fe_path[128], dvr_path[128];
  snprintf(fe_path,  sizeof(fe_path),  "/dev/dvb/adapter%d/frontend%d", o.adapter, o.frontend);
  snprintf(dvr_path, sizeof(dvr_path), "/dev/dvb/adapter%d/dvr%d",      o.adapter, o.frontend);

  int fe = open(fe_path, O_RDWR);
  if (fe < 0) { fprintf(stderr, "open %s: %s\n", fe_path, strerror(errno)); return 1; }
  int dvr = open(dvr_path, O_RDONLY | O_NONBLOCK);
  if (dvr < 0) { fprintf(stderr, "open %s: %s\n", dvr_path, strerror(errno)); return 1; }

  LOGV("sintonizando adapter%d frontend%d a %lld Hz (%s)\n",
       o.adapter, o.frontend, (long long)o.freq_hz, type);

  if (frontend_tune(fe, &o) < 0) return 1;

  LOGV("esperando lock...\n");
  if (wait_lock(fe, o.timeout_s) < 0) {
    fprintf(stderr, "sin lock en %s tras %ds\n", fe_path, o.timeout_s);
    return 1;
  }
  LOGV("bloqueado (lock) OK\n");
  print_signal(fe);

  /* PIDs de interés iniciales */
  track_pid(PID_PAT);
  track_pid(PID_SDT);
  track_pid(PID_NIT);
  if (o.with_epg) track_pid(PID_EIT);

  /* Bucle de lectura */
  time_t deadline = time(NULL) + o.timeout_s;
  uint8_t buf[188 * 32];
  struct pollfd pfd = { .fd = dvr, .events = POLLIN };

  while (time(NULL) < deadline) {
    int r = poll(&pfd, 1, 1000);
    if (r <= 0) continue;
    ssize_t n = read(dvr, buf, sizeof(buf));
    if (n <= 0) continue;
    process_buffer(buf, (int)n);
  }

  close(dvr);
  close(fe);

  /* Salida */
  FILE *out = stdout;
  if (o.outfile && !(out = fopen(o.outfile, "w"))) {
    fprintf(stderr, "fopen %s: %s\n", o.outfile, strerror(errno));
    return 1;
  }
  if (o.with_json) emit_json(&o, out); else emit_m3u8(&o, out);
  if (out != stdout) fclose(out);

  int cnt = 0;
  for (service_t *s = services; s; s = s->next) cnt++;
  LOGV("%d servicios\n", cnt);

  return 0;
}
