/*
 * clock.h - Clock Generation Unit setup.
 *
 * Configures the LPC4370 clock tree:
 *   12 MHz crystal  -> PLL1  -> 204 MHz core (max for the M4)
 *   12 MHz crystal  -> PLL1/3 (IDIVA) -> 68 MHz BASE_ADCHS_CLK  (clock_init default)
 *   PLL0USB 480 MHz / 6 (IDIVB) -> 80 MHz BASE_ADCHS_CLK       (clock_set_adchs_80mhz)
 *
 * Call clock_init() once at the very top of main(). Call clock_set_adchs_80mhz()
 * AFTER usb_cdc_init() (it reuses the 480 MHz USB PLL, which only exists once
 * the USB stack has brought it up) to bump the ADC sample clock to 80 MHz.
 */

#ifndef _CLOCK_H_
#define _CLOCK_H_

#include <stdint.h>

#define SYSTEM_CORE_HZ   204000000u   /* M4 main clock (PLL1) */
#define ADCHS_CLOCK_HZ    80000000u   /* ADCHS base clock target (PLL0USB / 6) */

void clock_init(void);

/* Re-point BASE_ADCHS to 80 MHz = PLL0USB(480) / 6 via integer divider B.
 * Requires PLL0USB to be running (set up by the USB stack), so call this
 * after usb_cdc_init(). No fractional PLL / no PLL0AUDIO encoding needed. */
void clock_set_adchs_80mhz(void);

#endif /* _CLOCK_H_ */
