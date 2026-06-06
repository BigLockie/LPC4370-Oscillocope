/*
 * adchs.h — High-Speed ADC capture, with trigger + per-channel gating.
 *
 * The LPC4370 ADCHS is a 12-bit, up-to-80 MSa/s converter with 6 inputs.
 * We use channels ADCHS_CHANNEL_FOR_CH1 and ADCHS_CHANNEL_FOR_CH2 (see
 * pin_map.h) for the board's Channel 1 and Channel 2 inputs respectively.
 *
 * Acquisition model:
 *   1. Host sends RUN → adchs_start_capture() arms a DMA burst per active channel.
 *   2. When DMA completes, the GPDMA IRQ in main.c flags adchs_block_ready().
 *   3. main.c drains the buffer via adchs_get_block() and ships it over USB.
 *   4. adchs_get_block() rearms the DMA channel automatically.
 *
 * Burst-then-transmit is the only viable mode because USB-CDC (~1 MB/s)
 * cannot keep up with 33 MSa/s × 2 ch × 2 bytes = 132 MB/s continuous.
 * Bursts give the user a snapshot at full sample rate, then we spend
 * longer transmitting it.
 */

#ifndef _ADCHS_H_
#define _ADCHS_H_

#include <stdbool.h>
#include <stdint.h>

/* Samples per channel per burst. 4095 is the GPDMA single-transfer ceiling
 * (12-bit transfer-count field). Deeper capture = higher sample rate for a
 * given on-screen window (rate = SAMPLES / window), so this is what lets us
 * sample fast at slower timebases instead of undersampling. 4095 × 4 B = 16380 B
 * fits the 16 KB RamAHB16 DMA buffer at 0x20008000. */
#define ADCHS_BLOCK_SAMPLES   4095   /* per channel, per burst */

/* Base clock feeding the ADCHS sample-rate divider  MUST match the clock the
 * ADCHS actually runs at (clock.c), or the host's time/frequency axis is wrong.
 * The timebase divider math (main.c SET_TIMEBASE) divides this value, so if it
 * disagrees with the real clock the displayed window is scaled by their ratio.
 *   - default build (ADCHS_CLOCK_68MHZ): clock_init() leaves ADCHS at 68 MHz
 *     (PLL1=204 / IDIVA÷3); clock_set_adchs_80mhz() is NOT called.
 *   - 80 MHz path: clock_set_adchs_80mhz() (PLL0USB÷6 via IDIVB) — currently
 *     broken (yields ~2 MHz), so we run at 68 MHz. */
#ifdef ADCHS_CLOCK_68MHZ
#define ADCHS_BASE_CLOCK_HZ   68000000u
#else
#define ADCHS_BASE_CLOCK_HZ   80000000u
#endif

/* Trigger modes — must match host_bridge.TRIGGER_MODE_CODES. */
#define ADCHS_TRIG_RISING     0
#define ADCHS_TRIG_FALLING    1
#define ADCHS_TRIG_AUTO       2

/* --- core lifecycle --- */
void adchs_init(void);
void adchs_start_capture(void);
void adchs_stop_capture(void);

/* Enable / disable a channel's DMA arming. Channel must be 1 or 2.
 * Disabled channels are simply not armed in adchs_start_capture(). */
void adchs_set_channel_enabled(uint8_t channel, bool enabled);

/* Set the sample-rate divider applied to ADCHS_BASE_CLOCK_HZ. Valid
 * range is [1, 65535]. The resulting per-channel sample rate is
 * ADCHS_BASE_CLOCK_HZ / divider. Must be called before start_capture
 * to take effect on the next burst. */
void adchs_set_sample_rate_divider(uint16_t divider);

/* Configure the hardware trigger comparator.
 *   source_channel : 1 or 2  (which ADCHS input gates the burst)
 *   level_uv       : trigger level in microvolts (signed, ±V_FULL_SCALE range)
 *   mode_code      : ADCHS_TRIG_RISING / _FALLING / _AUTO
 * In AUTO mode the trigger is bypassed and bursts free-run. */
void adchs_set_trigger(uint8_t source_channel,
                       int32_t level_uv,
                       uint8_t mode_code);

/* --- per-block readout --- */
bool     adchs_block_ready(uint8_t channel);
uint16_t adchs_get_block(uint8_t channel, uint16_t *out_buf);

/* Called from the GPDMA ISR (DMA_IRQHandler in main.c). */
void adchs_dma_complete_isr(uint8_t dma_channel);

#endif /* _ADCHS_H_ */
