/*
 *  tbsstream - sintoniza TBS/DVB, descifra con newcamd (CSA) y reenvía el TS
 *
 *  Pipeline:
 *    DVB (frontend/dvr0)
 *      -> demux (PAT/PMT -> PIDs, ECM)
 *      -> newcamd (ECM -> CW par/impar)
 *      -> CSA (libdvbcsa, descifra vídeo/audio)
 *      -> remux (PAT/PMT limpios)
 *      -> salida: UDP (multicast/unicast, para IP->analógico) y HTTP (TS + M3U8)
 *
 *  Reutiliza lógica de TVHeadend (dvb_psi, tvhcsa, cwc) y de dvb2m3u8
 *  (sintonización/demux). Solo Linux. Depende de libdvbcsa y OpenSSL.
 *
 *  GPLv3.
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
#include <ctype.h>
#include <getopt.h>
#include <pthread.h>
#include <signal.h>

#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>

#include <linux/dvb/frontend.h>
#include <linux/dvb/version.h>
#include <linux/dvb/dmx.h>

#include "newcamd.h"
#include "csa.h"

/* -------------------------------------------------------------------------
 * Constantes
 * ------------------------------------------------------------------------- */
#define PID_PAT  0x0000
#define PID_PMT_BASE 0x02
#define TID_PAT  0x00
#define TID_PMT  0x02
#define DESC_CA  0x09

#define MAX_ES   32
#define TS_PKT   188
#define RING_PKTS 4096

static int verbose = 0;
#define LOGV(...) do { if (verbose) fprintf(stderr, __VA_ARGS__); } while (0)

/* -------------------------------------------------------------------------
 * CRC-32 MPEG-2
 * ------------------------------------------------------------------------- */
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

/* -------------------------------------------------------------------------
 * Servicio / demux
 * ------------------------------------------------------------------------- */
typedef struct {
  int ready;
  uint16_t sid;
  uint16_t tsid;
  uint16_t pmt_pid;
  uint8_t pmt_version;
  uint16_t pcr_pid;
  int video_pid, audio_pid;
  int ecm_pid;
  uint16_t caid;
  uint32_t provid;

  uint8_t program_descriptors[1024];
  int program_descriptors_len;
  struct {
    uint8_t type;
    uint16_t pid;
    int descramble, descriptors_len;
    uint8_t descriptors[1024];
  } es[MAX_ES];
  int nes;
} service_t;

static service_t svc;
static char m3u8_body[512];
static pthread_mutex_t playlist_mutex = PTHREAD_MUTEX_INITIALIZER;

/* -------------------------------------------------------------------------
 * Opciones globales
 * ------------------------------------------------------------------------- */
typedef struct {
  int adapter, frontend;
  int delsys;
  int64_t freq_hz;     /* frecuencia de satélite; si lnb_lo_hz=0 es la FI */
  int64_t lnb_lo_hz;
  uint32_t bandwidth_hz, symbol_rate;
  int mod, fec, fec_hp, fec_lp, tx_mode, guard, hier, rolloff;
  char pol;

  uint16_t want_sid;   /* 0 = auto (primer servicio) */

  char nc_host[128];
  int nc_port;
  char nc_user[64];
  char nc_pass[64];
  uint8_t nc_deskey[14];

  /* salidas */
  struct { char host[128]; int port; int multicast; } udp[8];
  int nudp;
  int http_port;
  int stdout_ts;
  int stdin_ts, mux_only;
  char cam_socket[108], ca_broker[108];
  char m3u8_base[256];
} opts_t;

static opts_t opts;

static void refresh_playlist(unsigned sid)
{
  char base[256];
  if (opts.m3u8_base[0])
    snprintf(base, sizeof(base), "%s", opts.m3u8_base);
  else
    snprintf(base, sizeof(base), "http://127.0.0.1:%d", opts.http_port);
  pthread_mutex_lock(&playlist_mutex);
  snprintf(m3u8_body, sizeof(m3u8_body),
           "#EXTM3U\n#EXTINF:-1 tvg-id=\"%u\",Canal %u\n%s/stream.ts\n", sid, sid, base);
  pthread_mutex_unlock(&playlist_mutex);
}


/* -------------------------------------------------------------------------
 * Sintonización DVB
 * ------------------------------------------------------------------------- */
static void dtv(struct dtv_property *p, int *n, uint32_t cmd, uint32_t data)
{
  p[*n].cmd = cmd; p[*n].u.data = data; (*n)++;
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
  /* Polarización LNB por voltaje ANTES de sintonizar: alimenta el LNB y
     selecciona la polarización. Si se hace DESPUÉS, el tuner no engancha. */
  if (o->delsys == SYS_DVBS || o->delsys == SYS_DVBS2) {
    fe_sec_voltage_t v = (o->pol == 'V' || o->pol == 'L' || o->pol == 'v' || o->pol == 'l')
                         ? SEC_VOLTAGE_13 : SEC_VOLTAGE_18;
    ioctl(fd, FE_SET_VOLTAGE, v);
    usleep(300000);   /* dejar estabilizar el LNB */
  }

  dtv(p, &n, DTV_TUNE, 0);

  struct dtv_properties cmd = { .num = (unsigned)n, .props = p };
  if (ioctl(fd, FE_SET_PROPERTY, &cmd) < 0) {
    fprintf(stderr, "FE_SET_PROPERTY: %s\n", strerror(errno));
    return -1;
  }
  return 0;
}

static int wait_lock(int fd, int timeout_s)
{
  fe_status_t status;
  time_t deadline = time(NULL) + timeout_s;
  while (time(NULL) < deadline) {
    if (ioctl(fd, FE_READ_STATUS, &status) == 0 && (status & FE_HAS_LOCK)) return 0;
    usleep(200000);
  }
  return -1;
}

/* -------------------------------------------------------------------------
 * Reensamblado de secciones
 * ------------------------------------------------------------------------- */
typedef struct { uint16_t pid; uint8_t buf[4096]; int len; } secbuf_t;
static secbuf_t secbufs[4];
static int nsecbufs = 0;

static secbuf_t *secbuf_get(uint16_t pid)
{
  for (int i = 0; i < nsecbufs; i++)
    if (secbufs[i].pid == pid) return &secbufs[i];
  if (nsecbufs >= 4) return NULL;
  secbuf_t *sb = &secbufs[nsecbufs++];
  memset(sb, 0, sizeof(*sb));
  sb->pid = pid;
  return sb;
}

/* Se procesa una sección completa (s = buffer, slen = section_length). */
static void handle_section(uint16_t pid, const uint8_t *s, int slen);

static void process_sections(secbuf_t *sb)
{
  while (sb->len >= 3) {
    int slen = ((sb->buf[1] & 0x0F) << 8) | sb->buf[2];
    int total = 3 + slen;
    if (slen > 1021 || total <= 3) { sb->len = 0; return; }
    if (sb->len < total) break;
    if (!(sb->buf[1] & 0x80) || crc32_mpeg(sb->buf, total) == 0)
      handle_section(sb->pid, sb->buf, slen);
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
      p += take; plen -= take;
    }
    if (plen > 0 && sb->len + plen < (int)sizeof(sb->buf)) {
      memcpy(sb->buf + sb->len, p, plen);
      sb->len += plen;
      process_sections(sb);
    }
  } else if (sb->len + len < (int)sizeof(sb->buf)) {
    memcpy(sb->buf + sb->len, data, len);
    sb->len += len;
    process_sections(sb);
  }
}

/* -------------------------------------------------------------------------
 * Output: ring buffer (HTTP) + UDP
 * ------------------------------------------------------------------------- */
static uint8_t ring[RING_PKTS * TS_PKT];
static uint64_t ring_seq = 0;          /* próximo seq a escribir */
static pthread_mutex_t ring_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ring_cond = PTHREAD_COND_INITIALIZER;

static int udp_socks[8];
static struct sockaddr_storage udp_addr[8];
static socklen_t udp_addrlen[8];
static int nudp_global;

static void output_packet(const uint8_t *pkt)
{
  if (opts.stdout_ts) {
    size_t left = TS_PKT;
    const uint8_t *p = pkt;
    while (left) {
      ssize_t n = write(STDOUT_FILENO, p, left);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) exit(EXIT_FAILURE);
      p += n;
      left -= (size_t)n;
    }
  }
  pthread_mutex_lock(&ring_mutex);
  memcpy(ring + (size_t)(ring_seq % RING_PKTS) * TS_PKT, pkt, TS_PKT);
  ring_seq++;
  pthread_cond_broadcast(&ring_cond);
  pthread_mutex_unlock(&ring_mutex);

  for (int i = 0; i < nudp_global; i++)
    sendto(udp_socks[i], pkt, TS_PKT, 0, (struct sockaddr *)&udp_addr[i], udp_addrlen[i]);
}

/* Batch up to seven TS packets without waiting for a full batch. */
static int ring_read(uint64_t *client_seq, uint8_t *out)
{
  pthread_mutex_lock(&ring_mutex);
  while (*client_seq >= ring_seq)
    pthread_cond_wait(&ring_cond, &ring_mutex);
  if (ring_seq > RING_PKTS && *client_seq < ring_seq - RING_PKTS) {
    LOGV("HTTP: slow client, ring overrun\n");
    *client_seq = ring_seq - RING_PKTS;
  }
  int count = 0;
  while (*client_seq < ring_seq && count < 7) {
    memcpy(out + count * TS_PKT,
           ring + (size_t)(*client_seq % RING_PKTS) * TS_PKT, TS_PKT);
    (*client_seq)++;
    count++;
  }
  pthread_mutex_unlock(&ring_mutex);
  return count;
}

/* TCP may accept fewer bytes than requested, even on a blocking socket. */
static int http_write_all(int fd, const void *data, size_t len)
{
  const uint8_t *p = data;
  while (len) {
    ssize_t n = send(fd, p, len, MSG_NOSIGNAL);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return -1;
    p += n;
    len -= (size_t)n;
  }
  return 0;
}

/* -------------------------------------------------------------------------
 * Generación de PAT/PMT (remux)
 * ------------------------------------------------------------------------- */
static int build_pat(uint8_t *sec)
{
  int i = 0;
  sec[i++] = TID_PAT;
  sec[i++] = 0xB0; sec[i++] = 0;        /* section_length (se rellena) */
  sec[i++] = (uint8_t)(svc.tsid >> 8);
  sec[i++] = (uint8_t)(svc.tsid & 0xff);
  sec[i++] = 0xC1;                       /* current_next=1, version 0 */
  sec[i++] = 0; sec[i++] = 0;            /* section_number, last_section_number */
  sec[i++] = (uint8_t)(svc.sid >> 8);
  sec[i++] = (uint8_t)(svc.sid & 0xff);
  sec[i++] = (uint8_t)(0xE0 | (svc.pmt_pid >> 8));
  sec[i++] = (uint8_t)(svc.pmt_pid & 0xff);
  int slen = i - 3 + 4;                  /* cuerpo + CRC */
  sec[1] = (uint8_t)(0xB0 | (slen >> 8));
  sec[2] = (uint8_t)(slen & 0xff);
  uint32_t crc = crc32_mpeg(sec, i);
  sec[i++] = (uint8_t)(crc >> 24);
  sec[i++] = (uint8_t)(crc >> 16);
  sec[i++] = (uint8_t)(crc >> 8);
  sec[i++] = (uint8_t)(crc);
  return i;
}

static int build_pmt(uint8_t *sec)
{
  int i = 0;
  sec[i++] = TID_PMT;
  sec[i++] = 0xB0; sec[i++] = 0;
  sec[i++] = (uint8_t)(svc.sid >> 8);
  sec[i++] = (uint8_t)(svc.sid & 0xff);
  sec[i++] = (uint8_t)(0xC1 | ((svc.pmt_version & 0x1F) << 1));
  sec[i++] = 0; sec[i++] = 0;
  sec[i++] = (uint8_t)(0xE0 | (svc.pcr_pid >> 8));
  sec[i++] = (uint8_t)(svc.pcr_pid & 0xff);
  int pil = svc.program_descriptors_len;
  sec[i++] = (uint8_t)(0xf0 | (pil >> 8)); sec[i++] = (uint8_t)pil;
  memcpy(sec + i, svc.program_descriptors, pil);
  i += pil;
  for (int e = 0; e < svc.nes; e++) {
    int dl = svc.es[e].descriptors_len;
    if (i + 5 + dl + 4 > 1024) return 0;
    sec[i++] = svc.es[e].type;
    sec[i++] = (uint8_t)(0xE0 | (svc.es[e].pid >> 8));
    sec[i++] = (uint8_t)(svc.es[e].pid & 0xff);
    sec[i++] = (uint8_t)(0xf0 | (dl >> 8)); sec[i++] = (uint8_t)dl;
    memcpy(sec + i, svc.es[e].descriptors, dl);
    i += dl;
  }
  int slen = i - 3 + 4;
  sec[1] = (uint8_t)(0xB0 | (slen >> 8));
  sec[2] = (uint8_t)(slen & 0xff);
  uint32_t crc = crc32_mpeg(sec, i);
  sec[i++] = (uint8_t)(crc >> 24);
  sec[i++] = (uint8_t)(crc >> 16);
  sec[i++] = (uint8_t)(crc >> 8);
  sec[i++] = (uint8_t)(crc);
  return i;
}

/* A PSI section can span multiple TS packets. Only the first has a pointer. */
static void output_psi(uint16_t pid, uint8_t *cc, const uint8_t *sec, int len)
{
  int first = 1;
  while (len > 0) {
    uint8_t out[TS_PKT];
    memset(out, 0xff, sizeof(out));
    out[0] = 0x47;
    out[1] = (uint8_t)((first ? 0x40 : 0) | (pid >> 8));
    out[2] = (uint8_t)pid;
    out[3] = (uint8_t)(0x10 | (*cc & 15));
    *cc = (*cc + 1) & 15;
    int offset = 4;
    if (first) out[offset++] = 0;
    int size = len < TS_PKT - offset ? len : TS_PKT - offset;
    memcpy(out + offset, sec, size);
    output_packet(out);
    sec += size;
    len -= size;
    first = 0;
  }
}

static uint64_t psi_due_ms;
static uint64_t monotonic_ms(void)
{
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

static void emit_psi(void)
{
  static uint8_t pat_cc, pmt_cc;
  if (!svc.ready || !svc.pmt_pid) return;
  uint64_t now = monotonic_ms();
  if (now < psi_due_ms) return;
  uint8_t pat[64], pmt[1024];
  int plen = build_pat(pat), mlen = build_pmt(pmt);
  if (mlen <= 0) return;
  output_psi(PID_PAT, &pat_cc, pat, plen);
  output_psi(svc.pmt_pid, &pmt_cc, pmt, mlen);
  psi_due_ms = now + 100;
}

/* -------------------------------------------------------------------------
 * Descrambling asíncrono: cola de ECM + hilo CA + buffer de paquetes scrambled
 * ------------------------------------------------------------------------- */
static newcamd_t *nc = NULL;
static csa_t *csae = NULL;

/* Estado de claves (protegido por csa_mutex) */
static pthread_mutex_t csa_mutex = PTHREAD_MUTEX_INITIALIZER;
static int have_cw_even = 0, have_cw_odd = 0;

/* Cola de ECM (hilo principal -> hilo CA) */
#define ECMQ_MAX 64
typedef struct { uint8_t sec[1024]; int len; uint16_t sid, caid; uint32_t provid; } ecm_req_t;
static ecm_req_t ecm_q[ECMQ_MAX];
static int ecm_q_head = 0, ecm_q_tail = 0, ecm_q_count = 0;
static pthread_mutex_t ecm_q_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t ecm_q_cond = PTHREAD_COND_INITIALIZER;

/* Buffer de paquetes scrambled sin CW (solo se toca desde el hilo principal) */
#define SCRAM_MAX 65536 /* 12 MiB per active service; bounded, not a latency target */
static uint8_t scram_buf[SCRAM_MAX * TS_PKT];
static int scram_head = 0, scram_count = 0;
static uint64_t input_tei, input_cc_errors, service_drops, csa_batches, csa_packets;
static uint64_t ecm_queue_drops;
static uint8_t cc_seen[8192], last_cc[8192];

static volatile int ca_running = 1;

static int pid_forward(uint16_t pid)
{
  for (int i = 0; i < svc.nes; i++)
    if (svc.es[i].pid == pid) return 1;
  return pid == PID_PAT || pid == svc.pmt_pid ||
         (svc.pcr_pid != 0x1fff && pid == svc.pcr_pid);
}

/* Encolar un ECM (no bloquea) */
static void enqueue_ecm(uint16_t sid, uint16_t caid, uint32_t provid,
                        const uint8_t *sec, int len)
{
  if (len <= 0 || len > (int)sizeof(ecm_q[0].sec)) return;
  pthread_mutex_lock(&ecm_q_mutex);
  /* Do not let identical queued retransmissions age later ECM requests. */
  for (int i = 0; i < ecm_q_count; i++) {
    ecm_req_t *pending = &ecm_q[(ecm_q_head + i) % ECMQ_MAX];
    if (pending->sid == sid && pending->caid == caid && pending->provid == provid &&
        pending->len == len && !memcmp(pending->sec, sec, len)) {
      pthread_mutex_unlock(&ecm_q_mutex);
      return;
    }
  }
  if (ecm_q_count < ECMQ_MAX) {
    ecm_req_t *r = &ecm_q[ecm_q_tail];
    memcpy(r->sec, sec, len);
    r->len = len; r->sid = sid; r->caid = caid; r->provid = provid;
    ecm_q_tail = (ecm_q_tail + 1) % ECMQ_MAX;
    ecm_q_count++;
    pthread_cond_signal(&ecm_q_cond);
  } else {
    ecm_queue_drops++;
  }
  pthread_mutex_unlock(&ecm_q_mutex);
}

/* Keep the entire selected service in order, including clear audio/PCR.
 * A missing key at the head must not let later packets overtake it. */
static void flush_scrambled(void)
{
  uint8_t batch[64 * TS_PKT];
  while (scram_count) {
    int count = 0, encrypted = 0;
    pthread_mutex_lock(&csa_mutex);
    while (count < scram_count && count < 64) {
      const uint8_t *pkt = scram_buf + ((scram_head + count) % SCRAM_MAX) * TS_PKT;
      if (pkt[3] & 0x80) {
        int available = (pkt[3] & 0x40) ? have_cw_odd : have_cw_even;
        if (!available) break;
        encrypted++;
      }
      memcpy(batch + count * TS_PKT, pkt, TS_PKT);
      count++;
    }
    if (encrypted) {
      csa_descramble(csae, batch, count);
      csa_batches++;
      csa_packets += encrypted;
    }
    pthread_mutex_unlock(&csa_mutex);
    if (!count) break;
    scram_head = (scram_head + count) % SCRAM_MAX;
    scram_count -= count;
    for (int i = 0; i < count; i++) output_packet(batch + i * TS_PKT);
  }
}

static void buffer_service_packet(const uint8_t *pkt)
{
  if (scram_count >= 64) flush_scrambled();
  if (scram_count == SCRAM_MAX) {
    /* Bounded startup/failure buffering. Never silently keep stale payload. */
    service_drops += scram_count;
    LOGV("TS: service queue full waiting for CW; dropped %d packets\n", scram_count);
    scram_head = scram_count = 0;
  }
  int tail = (scram_head + scram_count) % SCRAM_MAX;
  memcpy(scram_buf + tail * TS_PKT, pkt, TS_PKT);
  scram_count++;
}

/* Input diagnostics, not a claim that CC errors can be repaired. Ignore
 * adaptation-only packets for CC increments and honor discontinuity flags. */
static void observe_transport(const uint8_t *pkt, unsigned pid)
{
  int afc = (pkt[3] >> 4) & 3;
  if (pkt[1] & 0x80) {
    input_tei++;
    cc_seen[pid] = 0;
    return;
  }
  if ((afc & 2) && pkt[4] >= 1 && pkt[4] <= 183 && (pkt[5] & 0x80))
    cc_seen[pid] = 0;
  if (!(afc & 1) || pid == 0x1fff) return;
  unsigned cc = pkt[3] & 15;
  if (cc_seen[pid] && cc != last_cc[pid] && cc != ((last_cc[pid] + 1) & 15))
    input_cc_errors++;
  last_cc[pid] = cc;
  cc_seen[pid] = 1;
}

static void log_transport_stats(void)
{
  static uint64_t due;
  uint64_t now = monotonic_ms();
  if (now < due) return;
  pthread_mutex_lock(&ecm_q_mutex);
  int queued = ecm_q_count;
  uint64_t dropped = ecm_queue_drops;
  pthread_mutex_unlock(&ecm_q_mutex);
  LOGV("TS stats: tei=%llu cc_errors=%llu service_drops=%llu pending=%d "
       "ecm_pending=%d ecm_drops=%llu csa_batches=%llu csa_packets=%llu\n",
       (unsigned long long)input_tei, (unsigned long long)input_cc_errors,
       (unsigned long long)service_drops, scram_count, queued,
       (unsigned long long)dropped, (unsigned long long)csa_batches,
       (unsigned long long)csa_packets);
  due = now + 5000;
}

static newcamd_t *connect_cam(char *error, size_t size)
{
  if (opts.cam_socket[0]) return newcamd_connect_local(opts.cam_socket, error, size);
  return newcamd_connect(opts.nc_host, opts.nc_port, opts.nc_deskey,
                         opts.nc_user, opts.nc_pass, error, size);
}

/* Hilo CA: envía ECMs y actualiza las CW (el bloqueo de red ocurre aquí) */
static void *ca_thread(void *arg)
{
  newcamd_t *cam = arg;
  unsigned retry_delay = 1;
  while (ca_running) {
    ecm_req_t r;
    pthread_mutex_lock(&ecm_q_mutex);
    while (ecm_q_count == 0 && ca_running)
      pthread_cond_wait(&ecm_q_cond, &ecm_q_mutex);
    if (!ca_running) { pthread_mutex_unlock(&ecm_q_mutex); break; }
    r = ecm_q[ecm_q_head];
    ecm_q_head = (ecm_q_head + 1) % ECMQ_MAX;
    ecm_q_count--;
    pthread_mutex_unlock(&ecm_q_mutex);

    if (!cam) {
      char error[256] = {0};
      cam = connect_cam(error, sizeof(error));
      if (!cam) {
        fprintf(stderr, "newcamd: %s; retry in %u s\n", error, retry_delay);
        sleep(retry_delay);
        if (retry_delay < 30) retry_delay = retry_delay > 15 ? 30 : retry_delay * 2;
        continue;
      }
      retry_delay = 1;
      fprintf(stderr, "newcamd: %s connected\n",
              opts.cam_socket[0] ? "local broker" : "upstream (login + card data)");
    }
    uint8_t even[8], odd[8];
    uint64_t requested = monotonic_ms();
    int result = newcamd_send_ecm(cam, r.sid, r.caid, r.provid,
                                 r.sec, r.len, even, odd, 5000);
    const uint8_t empty_cw[8] = {0};
    unsigned cw_mask = result == 0 ?
      ((memcmp(even, empty_cw, 8) ? 1 : 0) | (memcmp(odd, empty_cw, 8) ? 2 : 0)) : 0;
    LOGV("ECM: sid=%u caid=%04X time_ms=%llu result=%d cw_mask=%u at_ms=%llu pid=%ld\n",
         r.sid, r.caid, (unsigned long long)(monotonic_ms() - requested), result,
         cw_mask, (unsigned long long)monotonic_ms(), (long)getpid());
    if (result == 0) {
      pthread_mutex_lock(&csa_mutex);
      /* A zero half means unavailable; do not replace a valid CW with it. */
      const uint8_t zero[8] = {0};
      if (memcmp(even, zero, 8)) {
        csa_set_key_even(csae, even);
        have_cw_even = 1;
      }
      if (memcmp(odd, zero, 8)) {
        csa_set_key_odd(csae, odd);
        have_cw_odd = 1;
      }
      pthread_mutex_unlock(&csa_mutex);
      LOGV("CW actualizada (par/impar)\n");
    } else {
      LOGV("ECM sin CW (resultado %d)\n", result);
      if (result < 0) {
        /* Deadline/NOK invalidates this service only; never reuse an old CW
         * indefinitely. A framed broker deadline does not close its socket. */
        if (result == -1) { newcamd_close(cam); cam = NULL; }
        pthread_mutex_lock(&csa_mutex);
        have_cw_even = have_cw_odd = 0;
        pthread_mutex_unlock(&csa_mutex);
        pthread_mutex_lock(&ecm_q_mutex);
        ecm_q_count = ecm_q_head = ecm_q_tail = 0;
        pthread_mutex_unlock(&ecm_q_mutex);
      }
    }
  }
  return NULL;
}

static int is_video(uint8_t t)
{
  return t == 0x01 || t == 0x02 || t == 0x10 || t == 0x1B || t == 0x24 || t == 0x42 || t == 0xEA;
}
static int is_audio(uint8_t t)
{
  return t == 0x03 || t == 0x04 || t == 0x0F || t == 0x11 || t == 0x1C ||
         t == 0x81 || t == 0x82 || t == 0x87;
}

/* Preserve non-CA descriptors; reject malformed tables without partial updates. */
static int copy_descriptors(uint8_t *out, const uint8_t *in, int len,
                            service_t *service)
{
  int used = 0;
  for (int j = 0; j < len; ) {
    if (j + 2 > len) return -1;
    int size = 2 + in[j+1];
    if (j + size > len) return -1;
    if (in[j] == DESC_CA) {
      if (size < 6) return -1;
      if (service->ecm_pid < 0) {
        service->caid = ((uint16_t)in[j+2] << 8) | in[j+3];
        service->ecm_pid = ((in[j+4] & 0x1f) << 8) | in[j+5];
      }
    } else {
      memcpy(out + used, in + j, size);
      used += size;
    }
    j += size;
  }
  return used;
}

static void parse_pmt(const uint8_t *b, int len)
{
  if (len < 9 || len > 1017) return;
  uint16_t sid = ((uint16_t)b[0] << 8) | b[1];
  if ((opts.want_sid && sid != opts.want_sid) || !(b[2] & 1)) return;
  service_t next = {0};
  next.sid = sid;
  next.pmt_pid = svc.pmt_pid;
  next.tsid = svc.tsid;
  next.video_pid = next.audio_pid = next.ecm_pid = -1;
  next.pmt_version = (b[2] >> 1) & 31;
  next.pcr_pid = ((b[5] & 0x1f) << 8) | b[6];
  int pil = ((b[7] & 15) << 8) | b[8];
  if (9 + pil > len) return;
  next.program_descriptors_len = copy_descriptors(next.program_descriptors, b + 9, pil, &next);
  if (next.program_descriptors_len < 0) return;
  for (int off = 9 + pil; off < len; ) {
    if (off + 5 > len || next.nes >= MAX_ES) return;
    int dl = ((b[off+3] & 15) << 8) | b[off+4];
    if (off + 5 + dl > len) return;
    int e = next.nes++;
    next.es[e].type = b[off];
    next.es[e].pid = ((b[off+1] & 31) << 8) | b[off+2];
    next.es[e].descramble = 1;
    next.es[e].descriptors_len = copy_descriptors(next.es[e].descriptors, b + off + 5, dl, &next);
    if (next.es[e].descriptors_len < 0) return;
    if (is_video(b[off]) && next.video_pid < 0) next.video_pid = next.es[e].pid;
    if (is_audio(b[off]) && next.audio_pid < 0) next.audio_pid = next.es[e].pid;
    off += 5 + dl;
  }
  next.ready = 1;
  if (memcmp(&svc, &next, sizeof(next))) {
    svc = next;
    refresh_playlist(svc.sid);
    psi_due_ms = 0;
    LOGV("PMT: sid=%d pcr=%d video=%d audio=%d ecm=%d caid=%04X nes=%d pid=%ld\n",
         svc.sid, svc.pcr_pid, svc.video_pid, svc.audio_pid, svc.ecm_pid, svc.caid, svc.nes, (long)getpid());
  }
}

static void parse_pat(const uint8_t *b, int len)
{
  if (len < 8) return;
  svc.tsid = ((uint16_t)b[0] << 8) | b[1];
  for (int i = 5; i + 4 <= len; i += 4) {
    uint16_t prog = ((uint16_t)b[i] << 8) | b[i+1];
    uint16_t pid = ((uint16_t)(b[i+2] & 0x1F) << 8) | b[i+3];
    if (prog == 0) continue;
    if (opts.want_sid && prog != opts.want_sid) continue;
    svc.pmt_pid = pid;
    svc.sid = prog;
    break;
  }
}

static void handle_section(uint16_t pid, const uint8_t *s, int slen)
{
  uint8_t tid = s[0];
  const uint8_t *body = s + 3;
  int blen = slen - 4;

  if (pid == PID_PAT && tid == TID_PAT) {
    parse_pat(body, blen);
  } else if (pid == svc.pmt_pid && tid == TID_PMT) {
    parse_pmt(body, blen);
  } else if (svc.ecm_pid > 0 && pid == (uint16_t)svc.ecm_pid &&
             (tid == 0x80 || tid == 0x81)) {
    /* ECM uses private short sections: preserve the entire section. */
    enqueue_ecm(svc.sid, svc.caid, svc.provid, s, slen + 3);
  }
}

/* -------------------------------------------------------------------------
 * Servidor HTTP mínimo (TS + M3U8)
 * ------------------------------------------------------------------------- */
static void http_send(int fd, const char *status, const char *ctype, const char *body, int bodylen)
{
  char hdr[512];
  int n = snprintf(hdr, sizeof(hdr),
    "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
    "Connection: close\r\nAccess-Control-Allow-Origin: *\r\n\r\n",
    status, ctype, bodylen);
  if (http_write_all(fd, hdr, (size_t)n) == 0 && body && bodylen)
    http_write_all(fd, body, (size_t)bodylen);
}

static void *http_client(void *arg)
{
  int fd = (int)(intptr_t)arg;
  char req[512];
  int r = recv(fd, req, sizeof(req) - 1, 0);
  if (r <= 0) { close(fd); return NULL; }
  req[r] = 0;

  char method[16], path[256];
  if (sscanf(req, "%15s %255s", method, path) != 2) { close(fd); return NULL; }

  if (strcmp(path, "/playlist.m3u8") == 0) {
    char playlist[sizeof(m3u8_body)];
    pthread_mutex_lock(&playlist_mutex);
    memcpy(playlist, m3u8_body, sizeof(playlist));
    pthread_mutex_unlock(&playlist_mutex);
    http_send(fd, "200 OK", "application/vnd.apple.mpegurl", playlist, (int)strlen(playlist));
  } else if (strcmp(path, "/stream.ts") == 0) {
    char hdr[256];
    int n = snprintf(hdr, sizeof(hdr),
      "HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\n"
      "Connection: close\r\nAccess-Control-Allow-Origin: *\r\n\r\n");
    if (http_write_all(fd, hdr, (size_t)n) < 0) { close(fd); return NULL; }
    /* Join live: replaying the old ring creates a burst of stale PCR/PTS. */
    pthread_mutex_lock(&ring_mutex);
    uint64_t seq = ring_seq;
    pthread_mutex_unlock(&ring_mutex);
    uint8_t pkt[7 * TS_PKT];
    while (1) {
      int count = ring_read(&seq, pkt);
      if (http_write_all(fd, pkt, (size_t)count * TS_PKT) < 0) break;
    }
  } else {
    const char *idx =
      "<html><body><h2>tbsstream</h2><p><a href='/playlist.m3u8'>M3U8</a> · "
      "<a href='/stream.ts'>TS</a></p></body></html>";
    http_send(fd, "200 OK", "text/html; charset=utf-8", idx, (int)strlen(idx));
  }
  close(fd);
  return NULL;
}

static void *http_server(void *arg)
{
  int port = *(int *)arg;
  int ls = socket(AF_INET, SOCK_STREAM, 0);
  int one = 1;
  setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = INADDR_ANY,
                           .sin_port = htons((uint16_t)port) };
  if (bind(ls, (struct sockaddr *)&a, sizeof(a)) < 0 || listen(ls, 8) < 0) {
    fprintf(stderr, "HTTP: %s\n", strerror(errno));
    close(ls);
    exit(EXIT_FAILURE);
  }
  fprintf(stderr, "HTTP TS en http://0.0.0.0:%d/stream.ts\n", port);
  while (1) {
    int c = accept(ls, NULL, NULL);
    if (c < 0) continue;
    struct timeval tv = { .tv_sec = 5 };
    setsockopt(c, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    pthread_t t;
    pthread_create(&t, NULL, http_client, (void *)(intptr_t)c);
    pthread_detach(t);
  }
  return NULL;
}

/* -------------------------------------------------------------------------
 * Bucle principal
 * ------------------------------------------------------------------------- */
static void process_packet(uint8_t *pkt)
{
  if (pkt[0] != 0x47) return;
  emit_psi();
  int pusi = (pkt[1] & 0x40) != 0;
  uint16_t pid = ((uint16_t)(pkt[1] & 0x1F) << 8) | pkt[2];
  observe_transport(pkt, pid);
  if (pkt[1] & 0x80) return; /* corrupt input must not enter PSI/CSA */
  int afc = (pkt[3] >> 4) & 0x03;

  /* Demux de secciones PSI (PAT/PMT/ECM) */
  if (pid == PID_PAT || (svc.pmt_pid && pid == svc.pmt_pid) ||
      (svc.ecm_pid > 0 && pid == (uint16_t)svc.ecm_pid)) {
    int off = 4;
    if (afc == 0x02) return;
    if (afc == 0x03) off += 1 + pkt[4];
    if (off <= 184)
      feed_section(pid, pkt + off, TS_PKT - off, pusi);
    return;
  }

  if (svc.ready && pid_forward(pid)) buffer_service_packet(pkt);
}

/* DVR reads need not end at a TS boundary. Keep fragments across reads. */
static uint8_t ts_pending[3 * TS_PKT];
static int ts_pending_len = 0, ts_synced = 0;

static void process_buffer(const uint8_t *buf, int len)
{
  for (int i = 0; i < len; i++) {
    ts_pending[ts_pending_len++] = buf[i];
    if (ts_synced && ts_pending_len >= TS_PKT && ts_pending[0] != 0x47)
      ts_synced = 0;
    if (!ts_synced && ts_pending_len >= 2 * TS_PKT + 1) {
      if (ts_pending[0] == 0x47 && ts_pending[TS_PKT] == 0x47 &&
          ts_pending[2 * TS_PKT] == 0x47) {
        ts_synced = 1;
      } else {
        memmove(ts_pending, ts_pending + 1, --ts_pending_len);
      }
    }
    while (ts_synced && ts_pending_len >= TS_PKT) {
      process_packet(ts_pending);
      ts_pending_len -= TS_PKT;
      memmove(ts_pending, ts_pending + TS_PKT, ts_pending_len);
    }
  }
  flush_scrambled();
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
  return FEC_AUTO;
}

int main(int argc, char **argv)
{
  signal(SIGPIPE, SIG_IGN);
  memset(&opts, 0, sizeof(opts));
  opts.adapter = 0; opts.frontend = 0; opts.delsys = SYS_DVBT;
  opts.nc_port = 15050; opts.http_port = 9981;
  opts.pol = 'H';
  opts.lnb_lo_hz = 9750000000LL;   /* universal Ku, banda baja (9750 MHz) */
  strcpy(opts.nc_host, "127.0.0.1");
  strcpy(opts.nc_user, "user");

  char *type = NULL, *fec = NULL, *fec_hp = NULL, *fec_lp = NULL, *pols = NULL;
  double freq_mhz = 0;
  int timeout = 15;

  enum { OPT_FREQ=1000, OPT_FREQ_MHZ, OPT_BW, OPT_SR, OPT_FEC, OPT_FEC_HP, OPT_FEC_LP,
         OPT_POL, OPT_NEWCAMD, OPT_DESKEY, OPT_USER, OPT_PASS, OPT_UDP, OPT_HTTP,
         OPT_BASE, OPT_LNBLO, OPT_STDOUT, OPT_STDIN, OPT_MUX_ONLY, OPT_CAM_SOCKET, OPT_CA_BROKER };

  static const struct option longopts[] = {
    {"adapter", required_argument, 0, 'a'},
    {"frontend", required_argument, 0, 'F'},
    {"type", required_argument, 0, 't'},
    {"freq", required_argument, 0, OPT_FREQ},
    {"freq-mhz", required_argument, 0, OPT_FREQ_MHZ},
    {"bandwidth", required_argument, 0, OPT_BW},
    {"symbol-rate", required_argument, 0, OPT_SR},
    {"fec", required_argument, 0, OPT_FEC},
    {"fec-hp", required_argument, 0, OPT_FEC_HP},
    {"fec-lp", required_argument, 0, OPT_FEC_LP},
    {"pol", required_argument, 0, OPT_POL},
    {"sid", required_argument, 0, 's'},
    {"newcamd", required_argument, 0, OPT_NEWCAMD},
    {"deskey", required_argument, 0, OPT_DESKEY},
    {"user", required_argument, 0, OPT_USER},
    {"pass", required_argument, 0, OPT_PASS},
    {"udp", required_argument, 0, OPT_UDP},
    {"http-port", required_argument, 0, OPT_HTTP},
    {"stdout", no_argument, 0, OPT_STDOUT},
    {"stdin", no_argument, 0, OPT_STDIN},
    {"mux-only", no_argument, 0, OPT_MUX_ONLY},
    {"cam-socket", required_argument, 0, OPT_CAM_SOCKET},
    {"ca-broker", required_argument, 0, OPT_CA_BROKER},
    {"m3u8-base", required_argument, 0, OPT_BASE},
    {"lnb-lo", required_argument, 0, OPT_LNBLO},
    {"timeout", required_argument, 0, 'T'},
    {"verbose", no_argument, 0, 'v'},
    {"help", no_argument, 0, 'h'},
    {0,0,0,0}
  };

  int c;
  while ((c = getopt_long(argc, argv, "a:F:t:s:T:vh", longopts, NULL)) != -1) {
    switch (c) {
      case 'a': opts.adapter = atoi(optarg); break;
      case 'F': opts.frontend = atoi(optarg); break;
      case 't': type = optarg; break;
      case 's': opts.want_sid = (uint16_t)atoi(optarg); break;
      case OPT_FREQ: opts.freq_hz = (int64_t)strtoll(optarg, NULL, 10); break;
      case OPT_FREQ_MHZ: freq_mhz = atof(optarg); break;
      case OPT_BW: opts.bandwidth_hz = (uint32_t)strtoul(optarg, NULL, 10); break;
      case OPT_SR: opts.symbol_rate = (uint32_t)strtoul(optarg, NULL, 10); break;
      case OPT_FEC: fec = optarg; break;
      case OPT_FEC_HP: fec_hp = optarg; break;
      case OPT_FEC_LP: fec_lp = optarg; break;
      case OPT_POL: pols = optarg; break;
      case OPT_NEWCAMD: {
        char *colon = strchr(optarg, ':');
        if (colon) { *colon = 0; strncpy(opts.nc_host, optarg, sizeof(opts.nc_host)-1); opts.nc_port = atoi(colon+1); }
        else strncpy(opts.nc_host, optarg, sizeof(opts.nc_host)-1);
        break;
      }
      case OPT_DESKEY: {
        const char *s = optarg; int i = 0;
        while (*s && i < 14) {
          while (*s && !isxdigit((unsigned char)*s)) s++;
          int u = *s ? (isdigit((unsigned char)*s) ? *s-'0' : tolower((unsigned char)*s)-'a'+10) : 0; if (*s) s++;
          while (*s && !isxdigit((unsigned char)*s)) s++;
          int l = *s ? (isdigit((unsigned char)*s) ? *s-'0' : tolower((unsigned char)*s)-'a'+10) : 0; if (*s) s++;
          opts.nc_deskey[i++] = (uint8_t)((u << 4) | l);
        }
        break;
      }
      case OPT_USER: strncpy(opts.nc_user, optarg, sizeof(opts.nc_user)-1); break;
      case OPT_PASS: strncpy(opts.nc_pass, optarg, sizeof(opts.nc_pass)-1); break;
      case OPT_UDP: {
        if (opts.nudp < 8) {
          char *colon = strchr(optarg, ':');
          if (colon) {
            *colon = 0;
            strncpy(opts.udp[opts.nudp].host, optarg, sizeof(opts.udp[opts.nudp].host)-1);
            opts.udp[opts.nudp].port = atoi(colon+1);
            opts.udp[opts.nudp].multicast = 0;
            opts.nudp++;
          }
        }
        break;
      }
      case OPT_STDOUT: opts.stdout_ts = 1; break;
      case OPT_STDIN: opts.stdin_ts = 1; break;
      case OPT_MUX_ONLY: opts.mux_only = 1; break;
      case OPT_CAM_SOCKET:
      case OPT_CA_BROKER:
        if (strlen(optarg) >= sizeof(opts.cam_socket)) {
          fprintf(stderr, "local socket path too long\n"); return 1;
        }
        strcpy(c == OPT_CAM_SOCKET ? opts.cam_socket : opts.ca_broker, optarg);
        break;
      case OPT_HTTP: opts.http_port = atoi(optarg); break;
      case OPT_BASE: strncpy(opts.m3u8_base, optarg, sizeof(opts.m3u8_base)-1); break;
      case OPT_LNBLO: opts.lnb_lo_hz = (int64_t)(atof(optarg) * 1000000.0); break;
      case 'T': timeout = atoi(optarg); break;
      case 'v': verbose = 1; break;
      case 'h':
        fprintf(stderr, "Panel roles: --mux-only --stdout (DVB capture), "
                "--stdin --sid N --stdout --cam-socket PATH (service), "
                "--ca-broker PATH --newcamd HOST:PORT (shared CA)\n");
        fprintf(stderr, "Uso: %s -t dvbs --freq-mhz 11836 --symbol-rate 27500000 "
                "--newcamd 192.168.1.2:15050 --deskey <hex14bytes> --udp 239.1.1.1:1234\n", argv[0]);
        return 0;
      default: return 1;
    }
  }

  if (opts.ca_broker[0])
    return newcamd_broker(opts.ca_broker, opts.nc_host, opts.nc_port,
                         opts.nc_deskey, opts.nc_user, opts.nc_pass);
  if (opts.stdin_ts && opts.mux_only) {
    fprintf(stderr, "--stdin and --mux-only are mutually exclusive\n"); return 1;
  }

  if (freq_mhz > 0) opts.freq_hz = (int64_t)(freq_mhz * 1000000.0);

  if (!type) type = "dvbt";
  if (!strcmp(type, "dvbt")) opts.delsys = SYS_DVBT;
  else if (!strcmp(type, "dvbt2")) opts.delsys = SYS_DVBT2;
  else if (!strcmp(type, "dvbc")) opts.delsys = SYS_DVBC_ANNEX_A;
  else if (!strcmp(type, "dvbs")) opts.delsys = SYS_DVBS;
  else if (!strcmp(type, "dvbs2")) opts.delsys = SYS_DVBS2;
  else { fprintf(stderr, "tipo desconocido\n"); return 1; }

  if (!opts.stdin_ts && !opts.freq_hz) { fprintf(stderr, "falta --freq/--freq-mhz\n"); return 1; }
  if (!opts.stdin_ts && (opts.delsys == SYS_DVBC_ANNEX_A || opts.delsys == SYS_DVBS || opts.delsys == SYS_DVBS2) && !opts.symbol_rate) {
    fprintf(stderr, "falta --symbol-rate\n"); return 1;
  }
  if (pols) { opts.pol = *pols; }
  opts.fec    = fec    ? parse_fec(fec)    : FEC_AUTO;
  opts.fec_hp = fec_hp ? parse_fec(fec_hp) : FEC_AUTO;
  opts.fec_lp = fec_lp ? parse_fec(fec_lp) : FEC_AUTO;

  /* sockets UDP */
  nudp_global = opts.nudp;
  for (int i = 0; i < opts.nudp; i++) {
    udp_socks[i] = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in *a = (struct sockaddr_in *)&udp_addr[i];
    memset(a, 0, sizeof(*a));
    a->sin_family = AF_INET;
    a->sin_port = htons((uint16_t)opts.udp[i].port);
    inet_pton(AF_INET, opts.udp[i].host, &a->sin_addr);
    udp_addrlen[i] = sizeof(*a);
    fprintf(stderr, "UDP -> %s:%d\n", opts.udp[i].host, opts.udp[i].port);
  }

  int dvr = STDIN_FILENO;
  if (!opts.stdin_ts) {
    /* abrir frontend/dvr */
    char fe_path[128], dvr_path[128];
    snprintf(fe_path, sizeof(fe_path), "/dev/dvb/adapter%d/frontend%d", opts.adapter, opts.frontend);
    snprintf(dvr_path, sizeof(dvr_path), "/dev/dvb/adapter%d/dvr%d", opts.adapter, opts.frontend);
    int fe = open(fe_path, O_RDWR);
    if (fe < 0) { fprintf(stderr, "open %s: %s\n", fe_path, strerror(errno)); return 1; }
    dvr = open(dvr_path, O_RDONLY | O_NONBLOCK);
    if (dvr < 0) { fprintf(stderr, "open %s: %s\n", dvr_path, strerror(errno)); return 1; }

    if (frontend_tune(fe, &opts) < 0) return 1;
    if (wait_lock(fe, timeout) < 0) { fprintf(stderr, "sin lock\n"); return 1; }
    LOGV("lock OK\n");

    /* El driver TBSECP3 solo entrega TS al dvr si el demux tiene un filtro
       "full TS" (todos los PIDs -> DMX_OUT_TS_TAP). */
    char dmx_path[128];
    snprintf(dmx_path, sizeof(dmx_path), "/dev/dvb/adapter%d/demux%d", opts.adapter, opts.frontend);
    int dmx = open(dmx_path, O_RDWR | O_NONBLOCK);
    if (dmx < 0) { fprintf(stderr, "open demux: %s\n", strerror(errno)); return 1; }
    {
      /* Larger kernel buffering absorbs short userspace scheduling stalls.
       * Some vendor drivers do not implement this ioctl: keep running/log it. */
      if (ioctl(dmx, DMX_SET_BUFFER_SIZE, 4 * 1024 * 1024) < 0)
        LOGV("DMX_SET_BUFFER_SIZE unsupported/failed: %s\n", strerror(errno));
      struct dmx_pes_filter_params pes;
      memset(&pes, 0, sizeof(pes));
      pes.pid = 0x2000;
      pes.input = DMX_IN_FRONTEND;
      pes.output = DMX_OUT_TS_TAP;
      pes.pes_type = DMX_PES_OTHER;
      pes.flags = 0;
      if (ioctl(dmx, DMX_SET_PES_FILTER, &pes) < 0 || ioctl(dmx, DMX_START) < 0) {
        fprintf(stderr, "full mux filter: %s\n", strerror(errno)); return 1;
      }
    }

  }

  /* Capture has no descrambler or CA connection. */
  if (!opts.mux_only) {
    /* newcamd + CSA */
    csae = csa_create();
    if (!csae) { fprintf(stderr, "CSA allocation failed\n"); return 1; }
    char err[256] = {0};
    nc = connect_cam(err, sizeof(err));
    if (!nc) fprintf(stderr, "newcamd: %s\n", err);

    /* hilo CA asíncrono: envía ECMs y actualiza las CW sin bloquear el principal */
    pthread_t ca_tid;
    pthread_create(&ca_tid, NULL, ca_thread, nc);

    refresh_playlist(opts.want_sid);

    /* servidor HTTP */
    pthread_t ht;
    if (opts.http_port > 0)
      pthread_create(&ht, NULL, http_server, &opts.http_port);

  }

  /* bucle */
  uint8_t buf[TS_PKT * 256];
  struct pollfd pfd = { .fd = dvr, .events = POLLIN };
  while (1) {
    int r = poll(&pfd, 1, 100);
    if (r < 0 && errno == EINTR) continue;
    if (r < 0) return 1;
    if (r == 0) {
      if (opts.mux_only) continue;
      flush_scrambled();
      log_transport_stats();
      continue;
    }
    ssize_t n = read(dvr, buf, sizeof(buf));
    if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
    if (n <= 0) return n == 0 ? 0 : 1;
    if (opts.mux_only) {
      size_t sent = 0;
      while (sent < (size_t)n) {
        ssize_t out = write(STDOUT_FILENO, buf + sent, (size_t)n - sent);
        if (out < 0 && errno == EINTR) continue;
        if (out <= 0) return 1;
        sent += (size_t)out;
      }
      continue;
    }
    process_buffer(buf, (int)n);
    log_transport_stats();

  }

  return 0;
}
