#define main stream_program_main
#include "../tbsstream/main.c"
#undef main
#include <assert.h>
#include <dvbcsa/dvbcsa.h>

static void test_fragmented_reads(void)
{
  enum { COUNT = 40 };
  uint8_t input[COUNT * TS_PKT + 11];
  memset(&svc, 0, sizeof(svc));
  svc.ready = 1;
  svc.nes = 1;
  svc.es[0].pid = 0x100;
  svc.pcr_pid = 0x101;
  memset(input, 0x11, 11); /* initial garbage to exercise synchronization */
  for (int i = 0; i < COUNT; i++) {
    uint8_t *p = input + 11 + i * TS_PKT;
    memset(p, i, TS_PKT);
    p[0] = 0x47; p[1] = 1; p[2] = 0; p[3] = 0x10 | (i & 15);
  }
  /* Includes one-byte reads and reads shorter than three packets. */
  const int chunks[] = {1, 187, 2, 19, 376, 500, 3};
  for (unsigned c = 0, pos = 0; pos < sizeof(input); c++) {
    unsigned n = chunks[c % (sizeof(chunks)/sizeof(chunks[0]))];
    if (n > sizeof(input) - pos) n = sizeof(input) - pos;
    process_buffer(input + pos, (int)n);
    pos += n;
  }
  assert(ring_seq == COUNT);
  assert(ts_pending_len == 0);
  for (int i = 0; i < COUNT; i++)
    assert(!memcmp(ring + i * TS_PKT, input + 11 + i * TS_PKT, TS_PKT));

  /* Preserve a PCR-only PID even when it is not an audio/video ES. */
  uint8_t pcr[TS_PKT] = {0x47, 1, 1, 0x20, 183, 0x10};
  process_buffer(pcr, sizeof(pcr));
  assert(ring_seq == COUNT + 1);
  assert(!memcmp(ring + COUNT * TS_PKT, pcr, TS_PKT));

  /* Recover from garbage inserted between otherwise complete packets. */
  uint8_t garbage[] = {0x11,0x22,0x33};
  process_buffer(garbage, sizeof(garbage));
  process_buffer(input + 11, 3 * TS_PKT);
  assert(ring_seq == COUNT + 4);

  uint64_t seq = 0;
  uint8_t out[7 * TS_PKT];
  assert(ring_read(&seq, out) == 7 && seq == 7);
  assert(!memcmp(out, input + 11, sizeof(out)));
}

#define WRITE_SIZE (512 * 1024)
static uint8_t expected[WRITE_SIZE];
static void *reader(void *arg)
{
  int fd = *(int *)arg;
  uint8_t buf[317];
  size_t pos = 0;
  while (pos < WRITE_SIZE) {
    ssize_t n = recv(fd, buf, sizeof(buf), 0);
    assert(n > 0);
    assert(pos + (size_t)n <= WRITE_SIZE);
    assert(!memcmp(buf, expected + pos, (size_t)n));
    pos += (size_t)n;
  }
  return NULL;
}

static void test_http_writes(void)
{
  int pair[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
  int small = 1024;
  setsockopt(pair[0], SOL_SOCKET, SO_SNDBUF, &small, sizeof(small));
  for (size_t i = 0; i < sizeof(expected); i++) expected[i] = (uint8_t)i;
  pthread_t thread;
  assert(pthread_create(&thread, NULL, reader, &pair[1]) == 0);
  assert(http_write_all(pair[0], expected, sizeof(expected)) == 0);
  pthread_join(thread, NULL);
  close(pair[1]);
  assert(http_write_all(pair[0], expected, 1) == -1);
  close(pair[0]);
}

static void test_stdout_ts(void)
{
  int pair[2];
  assert(pipe(pair) == 0);
  int saved = dup(STDOUT_FILENO);
  assert(saved >= 0 && dup2(pair[1], STDOUT_FILENO) >= 0);
  close(pair[1]);
  uint8_t packet[TS_PKT] = {0x47, 1, 0, 0x10}, received[TS_PKT];
  opts.stdout_ts = 1;
  output_packet(packet);
  opts.stdout_ts = 0;
  assert(dup2(saved, STDOUT_FILENO) >= 0);
  close(saved);
  assert(read(pair[0], received, sizeof(received)) == TS_PKT);
  assert(!memcmp(packet, received, sizeof(packet)));
  close(pair[0]);
}

static void test_service_order_and_csa_batch(void)
{
  uint8_t cw[8] = {1,2,3,6,4,5,6,15};
  uint8_t plain[3 * TS_PKT], encrypted[3 * TS_PKT];
  for (int i = 0; i < 3; i++) {
    memset(plain + i * TS_PKT, 0x55 + i, TS_PKT);
    uint8_t *p = plain + i * TS_PKT;
    p[0] = 0x47; p[1] = 1; p[2] = (uint8_t)i; p[3] = 0x10;
  }
  memcpy(encrypted, plain, sizeof(plain));
  struct dvbcsa_key_s *key = dvbcsa_key_alloc();
  dvbcsa_key_set(cw, key);
  dvbcsa_encrypt(key, encrypted + 4, 184);
  encrypted[3] |= 0x80;
  dvbcsa_encrypt(key, encrypted + 2 * TS_PKT + 4, 184);
  encrypted[2 * TS_PKT + 3] |= 0xc0;
  csae = csa_create();
  ring_seq = 0;
  have_cw_even = have_cw_odd = 0;
  scram_head = scram_count = 0;
  for (int i = 0; i < 3; i++) buffer_service_packet(encrypted + i * TS_PKT);
  flush_scrambled();
  assert(ring_seq == 0 && scram_count == 3);
  install_cw(0, cw);
  flush_scrambled();
  assert(ring_seq == 2 && scram_count == 1);
  assert(!memcmp(ring, plain, 2 * TS_PKT));
  install_cw(1, cw);
  flush_scrambled();
  assert(ring_seq == 3 && scram_count == 0);
  assert(!memcmp(ring, plain, sizeof(plain)));

  ring_seq = 0;
  output_parity = -1;
  memset(cw_used, 0, sizeof(cw_used));
  uint64_t before = csa_batches;
  for (int i = 0; i < 64; i++) buffer_service_packet(encrypted);
  flush_scrambled();
  assert(ring_seq == 64 && csa_batches == before + 1);
  for (int i = 0; i < 64; i++) assert(!memcmp(ring + i * TS_PKT, plain, TS_PKT));

  have_cw_even = have_cw_odd = 0;
  ring_seq = 0;
  service_drops = 0;
  for (int i = 0; i < SCRAM_MAX + 1; i++) buffer_service_packet(encrypted);
  assert(service_drops == SCRAM_MAX && scram_count == 1 && ring_seq == 0);
  scram_head = scram_count = 0;
  dvbcsa_key_free(key);
  csa_free(csae); csae = NULL;
}

static void test_late_rotating_cw(void)
{
  uint8_t even1[8] = {1,2,3,6,4,5,6,15};
  uint8_t odd1[8] = {2,3,4,9,5,6,7,18};
  uint8_t even2[8] = {3,4,5,12,6,7,8,21};
  uint8_t plain[4 * TS_PKT], input[4 * TS_PKT];
  struct dvbcsa_key_s *key = dvbcsa_key_alloc();
  csae = csa_create();
  memset(&svc, 0, sizeof(svc));
  select_ca(0);
  ring_seq = 0;
  for (int i = 0; i < 4; i++) {
    uint8_t *p = plain + i * TS_PKT;
    memset(p, 0x51 + i, TS_PKT);
    p[0] = 0x47; p[1] = 1; p[2] = 0; p[3] = 0x10 | i;
  }
  memcpy(input, plain, sizeof(input));
  const uint8_t *keys[] = {even1, odd1, even2};
  for (int i = 0; i < 3; i++) {
    dvbcsa_key_set(keys[i], key);
    dvbcsa_encrypt(key, input + i * TS_PKT + 4, 184);
    input[i * TS_PKT + 3] |= i == 1 ? 0xc0 : 0x80;
  }
  install_cw(0, even1); install_cw(1, odd1);
  buffer_service_packet(input); flush_scrambled();
  buffer_service_packet(input + TS_PKT); flush_scrambled();
  assert(ring_seq == 2);
  buffer_service_packet(input + 2 * TS_PKT);
  buffer_service_packet(input + 3 * TS_PKT); /* clear packet cannot overtake */
  flush_scrambled();
  assert(ring_seq == 2 && scram_count == 2);
  install_cw(0, even1); /* a repeated old reply does not make the CW fresh */
  flush_scrambled();
  assert(ring_seq == 2);
  install_cw(0, even2);
  flush_scrambled();
  assert(ring_seq == 4 && scram_count == 0);
  assert(!memcmp(ring, plain, sizeof(plain)));
  dvbcsa_key_free(key); csa_free(csae); csae = NULL;
}

static void test_buffer_keeps_its_key(void)
{
  uint8_t key1[8] = {1,2,3,6,4,5,6,15};
  uint8_t key2[8] = {3,4,5,12,6,7,8,21};
  uint8_t plain[TS_PKT], packet[TS_PKT];
  memset(plain, 0x55, sizeof(plain));
  plain[0] = 0x47; plain[1] = 1; plain[2] = 0; plain[3] = 0x10;
  memcpy(packet, plain, sizeof(packet));
  struct dvbcsa_key_s *key = dvbcsa_key_alloc();
  dvbcsa_key_set(key1, key); dvbcsa_encrypt(key, packet + 4, 184); packet[3] |= 0x80;
  csae = csa_create(); memset(&svc, 0, sizeof(svc)); select_ca(0); ring_seq = 0;
  install_cw(0, key1);
  buffer_service_packet(packet);
  install_cw(0, key2); /* response arrives before the queued packet is flushed */
  dvbcsa_key_set(key2, key); memcpy(packet, plain, TS_PKT);
  dvbcsa_encrypt(key, packet + 4, 184); packet[3] |= 0x80;
  buffer_service_packet(packet); flush_scrambled();
  assert(!memcmp(ring, plain, TS_PKT));
  assert(!memcmp(cw_value[0], key2, 8));
  assert(ring_seq == 2 && !memcmp(ring + TS_PKT, plain, TS_PKT));
  dvbcsa_key_free(key); csa_free(csae); csae = NULL;
}

static void test_ca_failure_and_late_response(void)
{
  uint8_t key[8] = {1,2,3,6,4,5,6,15}, zero[8] = {0};
  csae = csa_create();
  memset(&svc, 0, sizeof(svc));
  svc.ca_count = 2;
  svc.ca[0].caid = 0x100; svc.ca[0].pid = 0x123;
  svc.ca[1].caid = 0x500; svc.ca[1].pid = 0x124;
  select_ca(0);
  ecm_req_t request = {.generation = ca_generation};
  assert(accept_ecm_result(&request, -2, zero, zero));
  assert(!ca_rotate); /* one failure is not enough */
  assert(accept_ecm_result(&request, -2, zero, zero));
  assert(ca_rotate);
  uint8_t packet[TS_PKT] = {0x47,0x1f,0xff,0x10};
  process_packet(packet);
  assert(ca_selected == 1 && ca_generation != request.generation);
  assert(!accept_ecm_result(&request, 0, key, key));
  assert(!have_cw_even && !have_cw_odd);
  request.generation = ca_generation;
  assert(accept_ecm_result(&request, 0, key, zero));
  assert(have_cw_even && !have_cw_odd && !ca_rotate);
  /* No usable ECM on this candidate: try the next, not a permanent black screen. */
  ca_last_progress = monotonic_ms() - 20001;
  process_packet(packet);
  assert(ca_selected == 0);
  request.generation = ca_generation;
  accept_ecm_result(&request, -1, zero, zero);
  accept_ecm_result(&request, -1, zero, zero);
  assert(!ca_rotate); /* transport errors do not select an unrelated CA */
  csa_free(csae); csae = NULL;
  memset(&svc, 0, sizeof(svc));
}

static void test_working_ca_grace_and_lock(void)
{
  uint8_t key[8] = {1,2,3,6,4,5,6,15}, zero[8] = {0};
  csae = csa_create(); memset(&svc, 0, sizeof(svc));
  svc.ca_count = 2; svc.ca[0].caid = 0xb00; svc.ca[0].pid = 5169;
  svc.ca[1].caid = 0x1867; svc.ca[1].pid = 6507;
  select_ca(0);
  ecm_req_t request = {.generation = ca_generation};
  accept_ecm_result(&request, 0, key, key);
  for (int i = 0; i < 4; i++) accept_ecm_result(&request, -2, zero, zero);
  assert(!ca_rotate && !have_cw_even && !have_cw_odd); /* wait safely, no old keys */
  ca_last_progress = monotonic_ms() - 15001;
  accept_ecm_result(&request, -2, zero, zero);
  assert(ca_rotate);
  opts.preferred_caid = 0xb00; opts.preferred_ecm_pid = 5169;
  select_ca(0); request.generation = ca_generation;
  for (int i = 0; i < 4; i++) accept_ecm_result(&request, -2, zero, zero);
  ca_last_progress = monotonic_ms() - 30001;
  uint8_t packet[TS_PKT] = {0x47,0x1f,0xff,0x10};
  process_packet(packet);
  assert(ca_selected == 0 && !ca_rotate);
  assert(preferred_ca_index(&svc) == 0);
  opts.preferred_ecm_pid = 100;
  assert(preferred_ca_index(&svc) == -1);
  select_ca(-1);
  process_packet(packet); /* absent locked pair must not index ca[-1] or fall back */
  assert(ca_selected == -1);
  opts.preferred_caid = opts.preferred_ecm_pid = 0;
  memset(&svc, 0, sizeof(svc)); select_ca(0);
  csa_free(csae); csae = NULL;
}

static void test_ecm_repeat_suppression(void)
{
  memset(&svc, 0, sizeof(svc)); select_ca(0);
  uint8_t even[] = {0x80,0x70,5,1,2,3,4,5};
  uint8_t odd[] = {0x81,0x70,5,6,7,8,9,10};
  uint64_t before = ecm_repeats;
  enqueue_ecm(29, 0xb00, 0, even, sizeof(even));
  assert(ecm_q_count == 1);
  ecm_q_count = 0; ecm_q_head = ecm_q_tail; /* simulate an already completed request */
  enqueue_ecm(29, 0xb00, 0, even, sizeof(even));
  assert(!ecm_q_count && ecm_repeats == before + 1);
  enqueue_ecm(29, 0xb00, 0, odd, sizeof(odd));
  assert(ecm_q_count == 1); /* other table ID is not suppressed */
  even[7]++;
  enqueue_ecm(29, 0xb00, 0, even, sizeof(even));
  assert(ecm_q_count == 2); /* changed bytes go through immediately */
  ecm_q_count = 0; ecm_q_head = ecm_q_tail;
  recent_ecm_at[0] = monotonic_ms() - 1001;
  enqueue_ecm(29, 0xb00, 0, even, sizeof(even));
  assert(ecm_q_count == 1); /* bounded retry, not an eternal cache */
  select_ca(0);
  enqueue_ecm(29, 0xb00, 0, even, sizeof(even));
  assert(ecm_q_count == 1); /* changing context cannot suppress a fresh request */
  ecm_q_count = ecm_q_head = ecm_q_tail = 0;
}

static void psi_packet(uint8_t *packet, unsigned pid, int cc, int start,
                       const uint8_t *payload, unsigned length)
{
  assert(length > 0 && length < 183);
  memset(packet, 0xff, TS_PKT);
  packet[0] = 0x47; packet[1] = (pid >> 8) | (start ? 0x40 : 0);
  packet[2] = pid; packet[3] = 0x30 | cc;
  packet[4] = 183 - length; packet[5] = 0;
  memcpy(packet + TS_PKT - length, payload, length);
}

static void test_psi_continuity_and_pointer(void)
{
  uint8_t ecm[] = {0x80,0x70,5,1,2,3,4,5};
  uint8_t start[] = {0,0x80,0x70,5,1,2};
  uint8_t packet[TS_PKT];
  memset(&svc, 0, sizeof(svc));
  svc.ca_count = 1; svc.ca[0].caid = 0x100; svc.ca[0].pid = 0x123;
  nsecbufs = 0; select_ca(0);
  psi_packet(packet, 0x123, 0, 1, start, sizeof(start));
  process_packet(packet);
  secbuf_t *assembly = secbuf_get(0x123);
  assert(assembly->len == 5 && !ecm_q_count);
  process_packet(packet); /* duplicate must not concatenate the same bytes */
  assert(assembly->len == 5);
  psi_packet(packet, 0x123, 2, 0, ecm + 5, 3); /* CC 1 lost */
  process_packet(packet);
  assert(!assembly->len && assembly->wait_pusi && !ecm_q_count);
  psi_packet(packet, 0x123, 3, 1, start, sizeof(start)); process_packet(packet);
  psi_packet(packet, 0x123, 4, 0, ecm + 5, 3); process_packet(packet);
  assert(ecm_q_count == 1 && ecm_q[0].len == sizeof(ecm));
  assert(!memcmp(ecm_q[0].sec, ecm, sizeof(ecm)));
  ecm_q_count = ecm_q_head = ecm_q_tail = 0;
  psi_packet(packet, 0x123, 5, 1, start, sizeof(start)); process_packet(packet);
  uint8_t next[] = {0,0x81,0x70,5,9,8,7,6,5};
  psi_packet(packet, 0x123, 6, 1, next, sizeof(next)); process_packet(packet);
  assert(ecm_q_count == 1 && ecm_q[0].sec[0] == 0x81); /* pointer 0 abandons old partial */
  uint8_t bad_pointer[] = {10,0x80};
  feed_section(0x123, bad_pointer, sizeof(bad_pointer), 1);
  assert(!assembly->len && assembly->wait_pusi);
  ecm_q_count = ecm_q_head = ecm_q_tail = 0;
  psi_packet(packet, 0x123, 7, 1, start, sizeof(start)); process_packet(packet);
  packet[1] |= 0x80; process_packet(packet); /* TEI destroys partial PSI */
  assert(!assembly->len && assembly->wait_pusi);
  /* A PUSI payload containing only pointer=0 may start its section next packet. */
  select_ca(0);
  uint8_t pointer_only[] = {0};
  feed_section(0x123, pointer_only, sizeof(pointer_only), 1);
  feed_section(0x123, ecm, sizeof(ecm), 0);
  assert(ecm_q_count == 1 && !memcmp(ecm_q[0].sec, ecm, sizeof(ecm)));
  /* Pointer bytes finish an old section; the rest starts a distinct new section. */
  select_ca(0);
  feed_section(0x123, start, sizeof(start), 1);
  uint8_t finish_and_start[] = {3,3,4,5,0x81,0x70,5,9,8,7,6,5};
  feed_section(0x123, finish_and_start, sizeof(finish_and_start), 1);
  assert(ecm_q_count == 2);
  assert(ecm_q[0].sec[0] == 0x80 && ecm_q[1].sec[0] == 0x81);
  uint64_t before = invalid_ts;
  packet[1] &= 0x7f; packet[4] = 184; process_packet(packet);
  assert(invalid_ts == before + 1);
  memset(&svc, 0, sizeof(svc));
}

static void test_false_sync_headers(void)
{
  uint8_t fake[3 * TS_PKT], valid[3 * TS_PKT];
  memset(fake, 0, sizeof(fake));
  for (int i = 0; i < 3; i++) fake[i * TS_PKT] = 0x47; /* reserved AFC=0 */
  memset(&svc, 0, sizeof(svc));
  svc.ready = 1; svc.nes = 1; svc.es[0].pid = 0x100; svc.pcr_pid = 0x1fff;
  ts_synced = ts_pending_len = 0; ring_seq = 0; scram_count = scram_head = 0;
  process_buffer(fake, sizeof(fake));
  assert(!ts_synced && !ring_seq);
  for (int i = 0; i < 3; i++) {
    uint8_t *packet = valid + i * TS_PKT;
    memset(packet, 0x55, TS_PKT);
    packet[0] = 0x47; packet[1] = 1; packet[2] = 0; packet[3] = 0x10 | i;
  }
  process_buffer(valid, sizeof(valid));
  assert(ts_synced && ring_seq == 3 && !ts_pending_len);
  assert(!memcmp(ring, valid, sizeof(valid)));
}

static void test_input_error_counters(void)
{
  uint8_t pkt[TS_PKT] = {0x47, 1, 0, 0x10};
  memset(cc_seen, 0, sizeof(cc_seen));
  input_tei = input_cc_errors = 0;
  observe_transport(pkt, 0x100);
  pkt[3] = 0x12; observe_transport(pkt, 0x100); /* skipped payload CC */
  assert(input_cc_errors == 1);
  observe_transport(pkt, 0x100); /* same CC can be a duplicate */
  pkt[3] = 0x20; pkt[4] = 183; pkt[5] = 0;
  observe_transport(pkt, 0x100); /* adaptation only: no increment */
  assert(input_cc_errors == 1);
  pkt[3] = 0x37; pkt[4] = 1; pkt[5] = 0x80;
  observe_transport(pkt, 0x100); /* declared discontinuity resets expectation */
  pkt[3] = 0x18; observe_transport(pkt, 0x100);
  assert(input_cc_errors == 1);
  pkt[1] |= 0x80;
  uint64_t previous = ring_seq;
  process_packet(pkt);
  assert(input_tei == 1 && ring_seq == previous);
}

static void test_timed_descramble_and_expiry(void)
{
  uint8_t p[TS_PKT] = {0x47, 1, 0, 0x10};
  scram_head = scram_count = 0;
  opts.descramble_delay_ms = 500;
  uint64_t before = ring_seq;
  buffer_service_packet(p);
  flush_scrambled();
  assert(scram_count == 1 && ring_seq == before);
  scram_at[scram_head] -= 501000;
  flush_scrambled();
  assert(!scram_count && ring_seq == before + 1);
  have_cw_even = 0;
  p[3] = 0x90;
  buffer_service_packet(p);
  scram_at[scram_head] -= 501000;
  flush_scrambled();
  assert(scram_count == 1 && ring_seq == before + 1);
  /* Expired payload must not be decrypted later or remain forever. */
  uint64_t dropped = cw_timeout_drops;
  scram_at[scram_head] -= 2100000;
  flush_scrambled();
  assert(!scram_count && cw_timeout_drops == dropped + 1);
  opts.descramble_delay_ms = 0;
}

static void test_bounded_output_queue(void)
{
  uint8_t p[TS_PKT] = {0x47, 1, 0, 0x10};
  output_async = 1;
  output_count = output_head = 0;
  uint64_t before = output_drops;
  for (unsigned i = 0; i < OUTPUT_MAX + 1; i++) queue_output(p, monotonic_us());
  assert(output_count == 1 && output_drops == before + OUTPUT_MAX);
  assert(output_reset);
  output_async = 0;
  output_count = output_head = 0;
}

static void test_output_worker_order_and_expiry(void)
{
  output_async = output_running = 1;
  output_count = output_head = 0;
  opts.descramble_delay_ms = 0;
  uint8_t p[TS_PKT] = {0x47, 1, 0, 0x10};
  uint64_t before = ring_seq, dropped = output_drops;
  queue_output(p, monotonic_us() - 3000000); /* stale: never emitted */
  for (int i = 0; i < 20; i++) { p[2] = i; queue_output(p, monotonic_us()); }
  pthread_t thread;
  assert(!pthread_create(&thread, NULL, output_worker, NULL));
  struct timespec until;
  clock_gettime(CLOCK_REALTIME, &until); until.tv_sec += 3;
  pthread_mutex_lock(&ring_mutex);
  while (ring_seq < before + 20)
    assert(!pthread_cond_timedwait(&ring_cond, &ring_mutex, &until));
  for (int i = 0; i < 20; i++) assert(ring[((before+i) % RING_PKTS)*TS_PKT+2] == i);
  pthread_mutex_unlock(&ring_mutex);
  pthread_mutex_lock(&output_mutex);
  output_running = 0;
  pthread_cond_signal(&output_cond);
  pthread_mutex_unlock(&output_mutex);
  pthread_join(thread, NULL);
  assert(output_drops == dropped + 1);
  output_async = 0;
}

int main(void)
{
  test_fragmented_reads();
  test_http_writes();
  test_stdout_ts();
  test_service_order_and_csa_batch();
  test_late_rotating_cw();
  test_buffer_keeps_its_key();
  test_ca_failure_and_late_response();
  test_working_ca_grace_and_lock();
  test_ecm_repeat_suppression();
  test_psi_continuity_and_pointer();
  test_false_sync_headers();
  test_input_error_counters();
  test_timed_descramble_and_expiry();
  test_bounded_output_queue();
  test_output_worker_order_and_expiry();
  puts("TS fragmentation, PCR forwarding, ring batching and TCP tests passed");
  return 0;
}
