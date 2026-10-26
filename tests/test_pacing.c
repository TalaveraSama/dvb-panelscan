#include "../tbsstream/pacing.h"
#include <assert.h>
#include <stdio.h>
static void packet(uint8_t *p, uint64_t pcr, int disc)
{
  memset(p, 0xff, 188);
  p[0] = 0x47; p[1] = 1; p[2] = 0; p[3] = 0x30;
  p[4] = 7; p[5] = 0x10 | (disc ? 0x80 : 0);
  uint64_t base = pcr / 300; unsigned ext = pcr % 300;
  p[6] = base >> 25; p[7] = base >> 17; p[8] = base >> 9;
  p[9] = base >> 1; p[10] = ((base & 1) << 7) | 0x7e | (ext >> 8); p[11] = ext;
}
int main(void)
{
  uint8_t p[188]; uint64_t value; int disc;
  packet(p, PCR_WRAP - 20, 0);
  assert(ts_pcr(p, &value, &disc) && value == PCR_WRAP - 20 && !disc);
  p[4] = 6; assert(!ts_pcr(p, &value, &disc));
  p[4] = 184; assert(!ts_pcr(p, &value, &disc));
  packet(p, 0, 1); assert(ts_pcr(p, &value, &disc) && disc);
  p[10] |= 1; p[11] = 255; assert(!ts_pcr(p, &value, &disc));

  ts_clock_t c = {0};
  packet(p, PCR_WRAP - 540000, 0);
  uint64_t due = ts_schedule(&c, p, 0x100, 1000000, 1500000);
  assert(due == 1600000);
  p[5] = 0; /* 99 selected packets before the next PCR */
  for (int i = 0; i < 99; i++) ts_schedule(&c, p, 0x100, 1000000 + i*400, 1500000);
  packet(p, 540000, 0); /* 40 ms across PCR wrap */
  due = ts_schedule(&c, p, 0x100, 1040000, 1500000);
  assert(c.step == 400 && due == 1640000 && !c.resets);
  uint64_t anchor = c.pcr_index;
  ts_schedule(&c, p, 0x100, 1040000, 1500000);
  assert(c.pcr_index == anchor); /* repeated PCR doesn't replace reference */
  assert(ts_schedule(&c, p, 0x100, 1040000, 2000000) == 2020000 && c.late == 1);
  packet(p, 200000, 1);
  assert(ts_schedule(&c, p, 0x100, 1040000, 2100000) == 2200000 && c.resets == 1);
  packet(p, 27000000, 0); /* >500 ms jump: rebase, don't sleep seconds */
  assert(ts_schedule(&c, p, 0x100, 1040000, 2200000) == 2300000 && c.resets == 2);

  memset(&c, 0, sizeof(c));
  p[5] = 0;
  ts_schedule(&c, p, 0x100, 1000000, 1500000);
  for (int i = 1; i <= 100; i++) ts_schedule(&c, p, 0x100, 1000000 + i*500, 1500000);
  assert(!c.have_pcr && c.step == 500); /* arrival-clock fallback */
  packet(p, 1000000, 0);
  ts_schedule(&c, p, 0x100, 1050000, 1500000);
  assert(c.have_pcr);
  p[5] = 0;
  ts_schedule(&c, p, 0x100, 1650000, 1500000);
  assert(!c.have_pcr); /* missing PCR returns to arrival clock */
  /* Even a corrupted/implausible clock cannot schedule seconds ahead. */
  c.step = 5000000;
  assert(ts_schedule(&c, p, 0x100, 1050000, 1500000) == 1600000 && c.resets == 1);
  puts("PCR parsing, wrap, pacing, discontinuity, late recovery and arrival fallback passed");
  return 0;
}
