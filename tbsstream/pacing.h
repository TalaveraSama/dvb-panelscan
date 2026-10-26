/* Bounded monotonic output clock. Does not modify PCR/PTS/DTS. GPLv3. */
#ifndef TBS_PACING_H
#define TBS_PACING_H
#include <stdint.h>
#include <string.h>
#define PCR_WRAP ((UINT64_C(1) << 33) * 300)
typedef struct {
  uint64_t due, step, index, pcr, pcr_index, pcr_due;
  uint64_t arrival, arrival_index, resets, late;
  int have_pcr;
} ts_clock_t;

static int ts_pcr(const uint8_t *p, uint64_t *value, int *discontinuity)
{
  *discontinuity = 0;
  if (!(p[3] & 0x20) || p[4] < 1 || p[4] > 183) return 0;
  *discontinuity = !!(p[5] & 0x80);
  if (!(p[5] & 0x10) || p[4] < 7) return 0;
  uint64_t base = ((uint64_t)p[6] << 25) | ((uint64_t)p[7] << 17) |
                  ((uint64_t)p[8] << 9) | ((uint64_t)p[9] << 1) | (p[10] >> 7);
  unsigned ext = ((p[10] & 1) << 8) | p[11];
  if (ext >= 300) return 0;
  *value = base * 300 + ext;
  return 1;
}

/* Time in microseconds. PCR drives the clock; arrival spacing is the fallback.
 * Rebase after a stall instead of catching up through an unlimited burst.
 * At most seven packets are coalesced by the caller (one millisecond window). */
static uint64_t ts_schedule(ts_clock_t *c, const uint8_t *pkt, int pcr_pid,
                            uint64_t arrival, uint64_t now)
{
  uint64_t pcr = 0;
  int disc = 0;
  int pid = ((pkt[1] & 31) << 8) | pkt[2];
  int has_pcr = pid == pcr_pid && ts_pcr(pkt, &pcr, &disc);
  if (!c->due) { c->due = now + 100000; c->step = 75; }
  else c->due += c->step;
  c->index++;
  if (!c->arrival) { c->arrival = arrival; c->arrival_index = c->index; }
  else if (!c->have_pcr && arrival >= c->arrival + 5000) {
    uint64_t step = (arrival - c->arrival) / (c->index - c->arrival_index);
    if (step >= 1 && step <= 20000) c->step = step;
    c->arrival = arrival; c->arrival_index = c->index;
  }
  if (disc) { c->have_pcr = 0; c->due = now + 100000; c->resets++; }
  if (has_pcr) {
    uint64_t delta = (pcr + PCR_WRAP - c->pcr) % PCR_WRAP;
    if (c->have_pcr && delta && delta <= 13500000 && c->index > c->pcr_index) {
      uint64_t step = delta / 27 / (c->index - c->pcr_index);
      if (step >= 1 && step <= 20000) {
        c->step = step;
        uint64_t target = c->pcr_due + delta / 27;
        if (target > c->due) c->due = target;
      }
    } else if (c->have_pcr && delta > 13500000) {
      c->due = now + 100000; c->resets++;
    }
    c->have_pcr = 1; c->pcr = pcr; c->pcr_index = c->index; c->pcr_due = c->due;
  }
  if (now > c->due && now - c->due > 20000) {
    c->due = now + 20000; c->have_pcr = 0; c->late++;
  }
  /* An implausible/frozen clock cannot retain seconds of output forever. */
  if (c->due > now + 500000) {
    c->due = now + 100000; c->have_pcr = 0; c->resets++;
  }
  return c->due;
}
#endif
