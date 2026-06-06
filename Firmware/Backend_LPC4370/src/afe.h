/*
 * afe.h — Analog Front End control.
 *
 * Drives the per-channel gain MUX and AC/DC coupling switch via GPIOs
 * defined in pin_map.h. The AFE itself is a pure analog board — this
 * module only flips the digital control lines that select the path.
 */

#ifndef _AFE_H_
#define _AFE_H_

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    AFE_GAIN_0_256 = 0,   /* SEL1 high -> 0.256x */
    AFE_GAIN_0_833,       /* SEL2 high -> 0.833x */
    AFE_GAIN_2_564,       /* SEL3 high -> 2.564x */
    AFE_GAIN_10_0,        /* SEL4 high -> 10x */
    AFE_GAIN_COUNT
} afe_gain_t;

/* Numeric multiplier for converting raw ADC counts → input volts. */
extern const float AFE_GAIN_MULT[AFE_GAIN_COUNT];

/*
 * One-time SCU + GPIO direction setup. Sets the default path:
 *   gain  = 0.256× (smallest, safest at startup)
 *   couple = DC
 * Call once at boot, after GPIO clock is enabled.
 */
void afe_init(void);

/* Channel must be 1 or 2; both functions silently no-op for invalid input. */
void afe_set_gain(uint8_t channel, afe_gain_t gain);
/* dc_mode true selects DC coupling, false selects AC. No host command drives
 * this yet; see afe.c and the README for wiring it up. */
void afe_set_coupling_dc(uint8_t channel, bool dc_mode);

#endif /* _AFE_H_ */
