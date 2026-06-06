/*
 * clock.c — LPC4370 clock tree bring-up.
 *
 * Targets:
 *   BASE_M4_CLK    = 204 MHz   (max Cortex-M4 frequency)
 *   BASE_ADCHS_CLK =  68 MHz   (PLL1 / 3, via integer divider A)
 *
 * Why 68 MHz on ADCHS instead of the 80 MHz the part can do:
 *   80 MHz from a 12 MHz xtal needs PLL0AUDIO with non-integer M/N/P, and
 *   PLL0AUDIO is fiddly to configure correctly. The integer-divider path
 *   from PLL1 is bulletproof, uses only documented LPCOpen API calls, and
 *   still gives 33 MSa/s per channel — 165× the project's 200 kHz/channel
 *   customer requirement. If we ever need the extra headroom we can swap
 *   in PLL0AUDIO later without touching anything outside this file.
 *
 * The sample-rate divider applied in adchs.c on top of this base clock
 * is what implements the user's selected timebase.
 */

#include "clock.h"
#include "chip.h"

void clock_init(void)
{
    /* (1) Bring up the 12 MHz crystal and run M4 from PLL1 @ 204 MHz.
     *
     * Chip_SetupXtalClocking() is the LPCOpen helper that:
     *   - enables the external 12 MHz crystal oscillator
     *   - powers up PLL1 with M=17, N=1 → 12 × 17 = 204 MHz
     *   - selects PLL1 as BASE_M4_CLK
     *   - sets flash wait states for 204 MHz operation
     * After it returns the core is running at the full 204 MHz.        */
    Chip_SetupXtalClocking();

    /* (2) Configure IDIVA = PLL1 / 3 = 68 MHz, route it to BASE_ADCHS_CLK.
     *
     * The Chip_Clock_* helpers map directly to the CGU register fields
     * documented in LPC43xx UM10503 §14, so there's no guesswork.        */
    Chip_Clock_SetDivider(CLK_IDIV_A, CLKIN_MAINPLL, 3);
    Chip_Clock_SetBaseClock(CLK_BASE_ADCHS, CLKIN_IDIVA,
                            true,    /* enable autoblocking      */
                            false);  /* don't power down ADCHS   */

    /* (3) Refresh CMSIS's view of the core clock so SysTick math is right. */
    SystemCoreClockUpdate();
}

void clock_set_adchs_80mhz(void)
{
    /* PLL0USB runs at 480 MHz for USB (brought up by the USB stack). 480 / 6 =
     * 80 MHz exactly, with a plain integer divider, no PLL0AUDIO and none of
     * its pseudo-random MDEC/NDEC/PDEC encoding. Route it through divider B
     * (IDIVA only divides /1../4; IDIVB does /1../16, so it can do /6) and point
     * BASE_ADCHS at it. Tapping the USB PLL's output here does not disturb USB. */
    Chip_Clock_SetDivider(CLK_IDIV_B, CLKIN_USBPLL, 6);          /* 480 / 6 = 80 MHz */
    Chip_Clock_SetBaseClock(CLK_BASE_ADCHS, CLKIN_IDIVB,
                            true,    /* enable autoblocking    */
                            false);  /* don't power down ADCHS */
}
