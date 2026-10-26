#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <linux/dvb/frontend.h>
#include <sys/ioctl.h>
static unsigned long calls[4];
static int values[4], count, fail;
static int test_ioctl(int fd, unsigned long request, ...) {
  assert(fd==7); va_list args; va_start(args,request);
  calls[count]=request; values[count++]=va_arg(args,int); va_end(args);
  if (count==fail) { errno=EIO;return -1; } return 0;
}
static int test_usleep(unsigned int delay) { (void)delay;return 0; }
#define ioctl test_ioctl
#define usleep test_usleep
#include "../dvb_lnb.h"
#undef ioctl
#undef usleep
int main(void) {
  dvb_lnb_plan_t p=dvb_lnb_plan(11174000000LL,10750000000LL,'H',1);
  assert(p.lo_hz==9750000000LL && p.if_hz==1424000000LL && p.tone==SEC_TONE_OFF && p.voltage==SEC_VOLTAGE_18);
  p=dvb_lnb_plan(12230000000LL,9750000000LL,'V',1);
  assert(p.lo_hz==10600000000LL && p.if_hz==1630000000LL && p.tone==SEC_TONE_ON && p.voltage==SEC_VOLTAGE_13);
  assert(dvb_lnb_apply(7,&p)==0 && count==3);
  assert(calls[0]==FE_SET_TONE && values[0]==SEC_TONE_OFF);
  assert(calls[1]==FE_SET_VOLTAGE && values[1]==SEC_VOLTAGE_13);
  assert(calls[2]==FE_SET_TONE && values[2]==SEC_TONE_ON);
  count=0;fail=2; assert(dvb_lnb_apply(7,&p)==-1 && count==2);
  p=dvb_lnb_plan(11699000000LL,0,'V',1);assert(p.lo_hz==9750000000LL && p.tone==SEC_TONE_OFF);
  p=dvb_lnb_plan(11700000000LL,0,'H',1);assert(p.lo_hz==10600000000LL && p.tone==SEC_TONE_ON && p.voltage==SEC_VOLTAGE_18);
  p=dvb_lnb_plan(12230000000LL,10750000000LL,'V',0);assert(p.if_hz==1480000000LL && p.tone==SEC_TONE_OFF);
  puts("LNB: universal bands, V/H, fixed LO, ioctl ordering and failure passed");return 0;
}
