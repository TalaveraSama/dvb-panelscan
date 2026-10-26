#ifndef DVB_PANEL_LNB_H
#define DVB_PANEL_LNB_H
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/dvb/frontend.h>

/* Shared by scanner and raw-mux capture. Frequencies are in Hz. */
typedef struct {
  int64_t lo_hz, if_hz;
  fe_sec_tone_mode_t tone;
  fe_sec_voltage_t voltage;
} dvb_lnb_plan_t;

static inline dvb_lnb_plan_t dvb_lnb_plan(int64_t rf, int64_t manual_lo, char pol, int universal)
{
  dvb_lnb_plan_t p;
  p.lo_hz = universal ? (rf >= 11700000000LL ? 10600000000LL : 9750000000LL) : manual_lo;
  p.if_hz = p.lo_hz > 0 && rf > p.lo_hz ? rf - p.lo_hz : rf;
  p.tone = universal && rf >= 11700000000LL ? SEC_TONE_ON : SEC_TONE_OFF;
  p.voltage = pol == 'V' || pol == 'v' || pol == 'L' || pol == 'l' ? SEC_VOLTAGE_13 : SEC_VOLTAGE_18;
  return p;
}

static inline int dvb_lnb_apply(int fd, const dvb_lnb_plan_t *p)
{
  /* Stop old band selection before changing voltage; no DiSEqC command. */
  if (ioctl(fd, FE_SET_TONE, SEC_TONE_OFF) < 0) { perror("FE_SET_TONE OFF"); return -1; }
  if (ioctl(fd, FE_SET_VOLTAGE, p->voltage) < 0) { perror("FE_SET_VOLTAGE"); return -1; }
  usleep(300000);
  if (ioctl(fd, FE_SET_TONE, p->tone) < 0) { perror("FE_SET_TONE banda"); return -1; }
  usleep(20000);
  return 0;
}
#endif
