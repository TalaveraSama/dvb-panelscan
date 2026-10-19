/* Real encrypted upstream + private worker sockets; no live CA server needed. */
#include "../tbsstream/newcamd.c"
#include <assert.h>
#include <signal.h>
#include <sys/wait.h>

static void request(newcamd_t *worker, uint16_t sid, uint8_t marker)
{
  uint8_t bytes[13] = {sid >> 8, sid, 0x18, 2, 0,0,0,0, 0,3, 0x80,0,marker};
  assert(send_full(worker->fd, bytes, sizeof(bytes)) == 0);
}

static void answer(newcamd_t *server, uint16_t seq, uint8_t marker)
{
  uint8_t cw[19] = {0x80,0,16};
  memset(cw + 3, marker, 16);
  server->seq = seq - 1;
  assert(nc_send_msg(server, cw, sizeof(cw), 0,0,0, NULL) == 0);
}

static uint16_t read_ecm(newcamd_t *server, uint16_t sid)
{
  uint8_t bytes[1024];
  assert(nc_read_msg(server, bytes, sizeof(bytes), 1500) >= 15);
  assert(((bytes[4] << 8) | bytes[5]) == sid);
  return (bytes[2] << 8) | bytes[3];
}

int main(void)
{
  signal(SIGPIPE, SIG_IGN);
  char directory[] = "/tmp/dvb-broker-test-XXXXXX";
  assert(mkdtemp(directory));
  struct sockaddr_un addr = {.sun_family = AF_UNIX};
  snprintf(addr.sun_path, sizeof(addr.sun_path), "%s/socket", directory);
  int listener = socket(AF_UNIX, SOCK_STREAM, 0), pair[2];
  assert(listener >= 0 && bind(listener, (struct sockaddr *)&addr, sizeof(addr)) == 0);
  assert(listen(listener, 8) == 0);
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
  uint8_t key[14] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14}, spread[16];
  des_key_spread(key, spread);
  newcamd_t *upstream = calloc(1, sizeof(*upstream));
  upstream->fd = pair[0]; des_set_keys(upstream, spread);
  pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    close(pair[1]);
    _exit(broker_loop(listener, upstream, "unused", 0, key, "", ""));
  }
  close(pair[0]); close(listener); free(upstream);
  newcamd_t server = {.fd = pair[1]}; des_set_keys(&server, spread);
  char error[128] = {0};
  newcamd_t *a = newcamd_connect_local(addr.sun_path, error, sizeof(error));
  newcamd_t *b = newcamd_connect_local(addr.sun_path, error, sizeof(error));
  assert(a && b);
  request(a, 672, 1); request(b, 643, 2);
  /* Request 2 must arrive before replying to request 1. */
  uint16_t seq_a = read_ecm(&server, 672), seq_b = read_ecm(&server, 643);
  assert(seq_a != seq_b);
  answer(&server, seq_b, 0x22); answer(&server, seq_a, 0x11);
  uint8_t reply[17];
  assert(recv_full(b->fd, reply, sizeof(reply), 1000) == 0 && !reply[0] && reply[1] == 0x22);
  assert(recv_full(a->fd, reply, sizeof(reply), 1000) == 0 && !reply[0] && reply[1] == 0x11);
  /* Exact retransmission uses a short-lived cache scoped to this worker. */
  request(a, 672, 1);
  assert(recv_full(a->fd, reply, sizeof(reply), 500) == 0 && reply[1] == 0x11);
  struct pollfd fd = {.fd = server.fd, .events = POLLIN};
  assert(poll(&fd, 1, 50) == 0);
  /* One unanswered ECM must not block another service. */
  request(a, 672, 3); request(b, 643, 4);
  seq_a = read_ecm(&server, 672); seq_b = read_ecm(&server, 643);
  answer(&server, seq_b, 0x44);
  assert(recv_full(b->fd, reply, sizeof(reply), 500) == 0 && reply[1] == 0x44);
  assert(recv_full(a->fd, reply, sizeof(reply), 6000) == 0 && (int8_t)reply[0] == -3);
  /* Late CW is ignored, without reconnecting the shared upstream. */
  answer(&server, seq_a, 0x55);
  request(a, 672, 5);
  seq_a = read_ecm(&server, 672); answer(&server, seq_a, 0x66);
  assert(recv_full(a->fd, reply, sizeof(reply), 1000) == 0 && reply[1] == 0x66);
  /* A NOK response affects only the matching service. */
  request(a, 672, 6); seq_a = read_ecm(&server, 672);
  uint8_t nok[] = {0x80, 0, 0}; server.seq = seq_a - 1;
  assert(nc_send_msg(&server, nok, 3, 0,0,0, NULL) == 0);
  assert(recv_full(a->fd, reply, sizeof(reply), 1000) == 0 && (int8_t)reply[0] == -2);
  newcamd_close(a); newcamd_close(b); close(server.fd);
  kill(child, SIGTERM); int status; assert(waitpid(child, &status, 0) == child);
  unlink(addr.sun_path); rmdir(directory);
  puts("Shared Newcamd: out-of-order CW, cache, isolated deadline, late reply and NOK passed");
  return 0;
}
