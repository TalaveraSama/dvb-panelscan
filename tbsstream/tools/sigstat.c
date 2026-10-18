#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/dvb/frontend.h>

static long long read_stat(int fd, uint32_t cmd)
{
  struct dtv_property p;
  struct dtv_properties c = { .num = 1, .props = &p };
  p.cmd = cmd; p.u.st.len = 1; p.u.st.stat[0].svalue = 0;
  if (ioctl(fd, FE_GET_PROPERTY, &c) < 0) return 0;
  return (long long)p.u.st.stat[0].svalue;
}

int main(int argc, char **argv)
{
  int a = argc > 1 ? atoi(argv[1]) : 0;
  char path[64];
  snprintf(path, sizeof(path), "/dev/dvb/adapter%d/frontend0", a);
  int fd = open(path, O_RDWR);
  if (fd < 0) { perror(path); return 1; }

  ioctl(fd, FE_SET_VOLTAGE, SEC_VOLTAGE_13);   /* 13V = V */
  usleep(700000);

  /* FI 1220 MHz (kHz para driver TBS), DVB-S2, SR 27.9 Msym/s */
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
  if (ioctl(fd, FE_SET_PROPERTY, &c) < 0) { perror("FE_SET_PROPERTY"); return 1; }

  fe_status_t st = 0;
  for (int i = 0; i < 15; i++) { usleep(200000); ioctl(fd, FE_READ_STATUS, &st); }
  printf("status=0x%x SIGNAL=%d CARRIER=%d VITERBI=%d SYNC=%d LOCK=%d\n",
         st, !!(st & FE_HAS_SIGNAL), !!(st & FE_HAS_CARRIER),
         !!(st & FE_HAS_VITERBI), !!(st & FE_HAS_SYNC), !!(st & FE_HAS_LOCK));
  printf("signal=%lld cnr=%lld errblocks=%lld\n",
         read_stat(fd, DTV_STAT_SIGNAL_STRENGTH),
         read_stat(fd, DTV_STAT_CNR),
         read_stat(fd, DTV_STAT_ERROR_BLOCK_COUNT));
  return 0;
}
