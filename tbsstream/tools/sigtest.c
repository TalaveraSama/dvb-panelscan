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
  usleep(700000);                              /* dejar estabilizar el LNB */
  printf("adapter%d signal=%lld cnr=%lld\n", a,
         read_stat(fd, DTV_STAT_SIGNAL_STRENGTH), read_stat(fd, DTV_STAT_CNR));
  return 0;
}
