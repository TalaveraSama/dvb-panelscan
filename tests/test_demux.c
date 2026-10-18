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
  /* Keep language and codec descriptors but remove only CA descriptors. */
  uint8_t with_descriptors[] = {
    0,1,0xc3,0,0,0xe1,0,0xf0,0,
    6,0xe1,0,0xf0,16,
    9,4,0x18,2,0xe1,0x24, /* CA */
    0x0a,4,'s','p','a',0, /* ISO639 */
    0x6a,2,0,0 /* AC3 */
  };
  parse_pmt(with_descriptors, sizeof(with_descriptors));
  assert(svc.ecm_pid == 0x124 && svc.caid == 0x1802);
  assert(strstr(m3u8_body, "Canal 1") != NULL);
  assert(svc.es[0].descriptors_len == 10);
  uint8_t section[1024];
  int size = build_pmt(section);
  assert(size == 31);
  assert(crc32_mpeg(section, size) == 0);
  assert(section[17] == 0x0a && section[23] == 0x6a);
  /* Malformed replacement must not alter the last usable PMT. */
  uint8_t old_version = svc.pmt_version;
  with_descriptors[13] = 17;
  parse_pmt(with_descriptors, sizeof(with_descriptors));
  assert(svc.pmt_version == old_version && svc.es[0].descriptors_len == 10);

  /* Large sections span packets, PUSI/pointer only on the first packet. */
  uint8_t large[700], rebuilt[700], cc = 14;
  for (int i = 0; i < 700; i++) large[i] = (uint8_t)i;
  ring_seq = 0;
  output_psi(0x100, &cc, large, sizeof(large));
  assert(ring_seq == 4 && cc == 2);
  int copied = 0;
  for (int i = 0; i < 4; i++) {
    const uint8_t *pkt = ring + i * TS_PKT;
    assert(pkt[0] == 0x47 && (pkt[3] & 15) == ((14+i) & 15));
    assert(!!(pkt[1] & 0x40) == (i == 0));
    int offset = i == 0 ? 5 : 4;
    int take = sizeof(large) - copied;
    if (take > TS_PKT - offset) take = TS_PKT - offset;
    memcpy(rebuilt + copied, pkt + offset, take);
    copied += take;
  }
  assert(!memcmp(rebuilt, large, sizeof(large)));
  /* Emit initial tables before ES; no packet-count-dependent interval. */
  svc.pmt_pid = 0x100;
  psi_due_ms = 0;
  ring_seq = 0;
  emit_psi();
  assert(ring_seq == 2);
  uint64_t due = psi_due_ms;
  emit_psi();
  assert(ring_seq == 2 && psi_due_ms == due);
  puts("demux/descriptor/PSI regression tests passed");
  return 0;
}
