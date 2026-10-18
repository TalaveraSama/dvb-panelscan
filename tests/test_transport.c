#define main stream_program_main
#include "../tbsstream/main.c"
#undef main
#include <assert.h>

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

int main(void)
{
  test_fragmented_reads();
  test_http_writes();
  test_stdout_ts();
  puts("TS fragmentation, PCR forwarding, ring batching and TCP tests passed");
  return 0;
}
