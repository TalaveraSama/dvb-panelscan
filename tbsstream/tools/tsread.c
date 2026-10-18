#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <linux/dvb/frontend.h>
#include <linux/dvb/dmx.h>

static int set_voltage_tune(int a)
{
  char path[64];
  snprintf(path, sizeof(path), "/dev/dvb/adapter%d/frontend0", a);
  int fd = open(path, O_RDWR);
  if (fd < 0) { perror(path); return -1; }
  ioctl(fd, FE_SET_VOLTAGE, SEC_VOLTAGE_13);
  usleep(300000);
  struct dtv_property p[16];
  int n = 0;
  p[n].cmd = DTV_CLEAR; p[n].u.data = 0; n++;
  p[n].cmd = DTV_DELIVERY_SYSTEM; p[n].u.data = SYS_DVBS2; n++;
  p[n].cmd = DTV_FREQUENCY; p[n].u.data = 1220000; n++;
  p[n].cmd = DTV_INVERSION; p[n].u.data = INVERSION_AUTO; n++;
  p[n].cmd = DTV_MODULATION; p[n].u.data = QPSK; n++;
  p[n].cmd = DTV_SYMBOL_RATE; p[n].u.data = 27900000; n++;
  p[n].cmd = DTV_INNER_FEC; p[n].u.data = FEC_AUTO; n++;
  p[n].cmd = DTV_ROLLOFF; p[n].u.data = ROLLOFF_35; n++;
  p[n].cmd = DTV_PILOT; p[n].u.data = PILOT_AUTO; n++;
  p[n].cmd = DTV_TUNE; p[n].u.data = 0; n++;
  struct dtv_properties c = { .num = (unsigned)n, .props = p };
  if (ioctl(fd, FE_SET_PROPERTY, &c) < 0) { perror("FE_SET_PROPERTY"); close(fd); return -1; }
  fe_status_t st = 0;
  for (int i = 0; i < 25; i++) { usleep(200000); ioctl(fd, FE_READ_STATUS, &st); if (st & FE_HAS_LOCK) break; }
  printf("lock=%d (status=0x%x)\n", !!(st & FE_HAS_LOCK), st);
  return fd;  /* mantener fd abierto */
}

int main(int argc, char **argv)
{
  int a = argc > 1 ? atoi(argv[1]) : 0;
  int fe = set_voltage_tune(a);
  if (fe < 0) return 1;

  /* Configurar el demux para volcar el TS completo al dvr (necesario en TBS) */
  char dpath[64];
  snprintf(dpath, sizeof(dpath), "/dev/dvb/adapter%d/demux0", a);
  int demux = open(dpath, O_RDWR | O_NONBLOCK);
  if (demux >= 0) {
    struct dmx_pes_filter_params pes;
    memset(&pes, 0, sizeof(pes));
    pes.pid = 0x2000;               /* todos los PIDs */
    pes.input = DMX_IN_FRONTEND;
    pes.output = DMX_OUT_TS_TAP;    /* volcar al dvr */
    pes.pes_type = DMX_PES_OTHER;
    pes.flags = 0;
    if (ioctl(demux, DMX_SET_PES_FILTER, &pes) < 0) perror("DMX_SET_PES_FILTER");
    if (ioctl(demux, DMX_START) < 0) perror("DMX_START");
    printf("demux configurado (full TS)\n");
  } else perror(dpath);

  char path[64];
  snprintf(path, sizeof(path), "/dev/dvb/adapter%d/dvr0", a);
  int dvr = open(path, O_RDONLY | O_NONBLOCK);
  if (dvr < 0) { perror(path); return 1; }

  uint8_t buf[188 * 64];
  long total = 0, sync = 0;
  time_t deadline = time(NULL) + 5;
  while (time(NULL) < deadline) {
    struct pollfd pf = { .fd = dvr, .events = POLLIN };
    if (poll(&pf, 1, 500) <= 0) continue;
    ssize_t n = read(dvr, buf, sizeof(buf));
    if (n <= 0) continue;
    total += n;
    for (ssize_t i = 0; i < n; i++) if (buf[i] == 0x47) sync++;
  }
  printf("bytes=%ld sync0x47=%ld  (%.0f kbps)\n", total, sync, total * 8.0 / 5000.0);
  return 0;
}
