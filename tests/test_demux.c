/* Exercise the actual static demux functions without a DVB device. */
#define main stream_program_main
#include "../tbsstream/main.c"
#undef main
#include <assert.h>
int main(void) {
  uint8_t pmt[] = {0,1,0xc1,0,0,0xe1,0,0xf0,6, 9,4,1,0,0xe1,0x23,
                   0x1b,0xe1,0,0xf0,0};
  memset(&svc, 0, sizeof(svc));
  parse_pmt(pmt, sizeof(pmt));
  assert(svc.ecm_pid == 0x123 && svc.caid == 0x100);
  svc.ready = 0;
  uint8_t es_pmt[] = {0,1,0xc1,0,0,0xe1,0,0xf0,0,
                      0x1b,0xe1,0,0xf0,6,9,4,5,0,0xe1,0x24};
  parse_pmt(es_pmt, sizeof(es_pmt));
  assert(svc.ecm_pid == 0x124 && svc.caid == 0x500);
  /* ECM private sections have no MPEG long-section CRC. */
  secbuf_t sb = {0};
  sb.pid = svc.ecm_pid;
  uint8_t ecm[] = {0x80,0x70,5,1,2,3,4,5};
  memcpy(sb.buf, ecm, sizeof(ecm)); sb.len = sizeof(ecm);
  process_sections(&sb);
  assert(ecm_q_count == 1);
  assert(ecm_q[0].len == sizeof(ecm));
  assert(!memcmp(ecm_q[0].sec, ecm, sizeof(ecm)));
  /* Invalid CRC on a long section must not be queued. */
  ecm[1] |= 0x80;
  memcpy(sb.buf, ecm, sizeof(ecm)); sb.len = sizeof(ecm);
  process_sections(&sb);
  assert(ecm_q_count == 1);
  puts("demux regression tests passed");
  return 0;
}
