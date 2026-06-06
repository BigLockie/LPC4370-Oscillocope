/*
 * milestone1_blink.c - TEMPORARY bring-up test (Milestone 1).
 *
 * Drives the WS2812 "NeoPixel" on P1_14 / GPIO1[7] so the board shows a
 * visible green <-> off blink at ~1 Hz. This proves toolchain + flash + clock
 * + GPIO are all alive before we depend on the USB stack.
 *
 * NOT part of the product firmware. main() calls milestone1_blink_forever()
 * (which never returns) only when the build defines MILESTONE1_BLINK.
 *
 * Why not a plain GPIO toggle (as the README suggests)? The P1_14 part is an
 * addressable WS2812-family LED, not a simple LED. It only lights when fed a
 * valid ~800 kHz one-wire data frame, so we bit-bang the protocol here.
 *
 * Bit timing uses the Cortex-M4 DWT cycle counter, scaled from SystemCoreClock
 * (204 MHz after clock_init), so the WS2812 high/low widths are correct.
 */
#include "chip.h"
#include "pin_map.h"

/* WS2812(B) bit timing in nanoseconds (datasheet; ~+/-150 ns tolerant). */
#define WS_T0H_NS   400u
#define WS_T0L_NS   850u
#define WS_T1H_NS   800u
#define WS_T1L_NS   450u
#define WS_RESET_US 300u    /* >50 us low latches the frame */

/* Same SCU mode the firmware's afe.c uses for its GPIO outputs. */
#define NEOPIXEL_OUT_MODE  (SCU_MODE_INACT | SCU_MODE_INBUFF_EN)

static uint32_t cyc_t0h, cyc_t0l, cyc_t1h, cyc_t1l;

static inline void dwt_enable(void)
{
	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static inline void wait_cycles(uint32_t n)
{
	uint32_t start = DWT->CYCCNT;
	while ((DWT->CYCCNT - start) < n) { /* spin */ }
}

/* Drive the data line high/low with a single byte-register store (fast). */
#define WS_HIGH()  (LPC_GPIO_PORT->B[NEOPIXEL_PORT][NEOPIXEL_BIT] = 1)
#define WS_LOW()   (LPC_GPIO_PORT->B[NEOPIXEL_PORT][NEOPIXEL_BIT] = 0)

/* Shift out one byte, MSB first. Interrupts must be disabled by the caller. */
static void ws_send_byte(uint8_t b)
{
	for (uint8_t i = 0; i < 8; i++) {
		if (b & 0x80) {
			WS_HIGH(); wait_cycles(cyc_t1h);
			WS_LOW();  wait_cycles(cyc_t1l);
		} else {
			WS_HIGH(); wait_cycles(cyc_t0h);
			WS_LOW();  wait_cycles(cyc_t0l);
		}
		b <<= 1;
	}
}

/* Send one pixel (WS2812 wire order is G, R, B), then latch. */
static void ws_send_grb(uint8_t g, uint8_t r, uint8_t b)
{
	__disable_irq();
	ws_send_byte(g);
	ws_send_byte(r);
	ws_send_byte(b);
	__enable_irq();
	WS_LOW();
	wait_cycles((SystemCoreClock / 1000000u) * WS_RESET_US);
}

void milestone1_blink_forever(void)
{
	uint32_t hz = SystemCoreClock ? SystemCoreClock : 204000000u;

	/* Precompute bit-time cycle counts from the live core clock. */
	cyc_t0h = (uint32_t) (((uint64_t) hz * WS_T0H_NS) / 1000000000u);
	cyc_t0l = (uint32_t) (((uint64_t) hz * WS_T0L_NS) / 1000000000u);
	cyc_t1h = (uint32_t) (((uint64_t) hz * WS_T1H_NS) / 1000000000u);
	cyc_t1l = (uint32_t) (((uint64_t) hz * WS_T1L_NS) / 1000000000u);

	dwt_enable();

	/* P1_14 -> FUNC0 (GPIO1[7]); idle low; set as output. */
	Chip_SCU_PinMuxSet(NEOPIXEL_SCU_PORT, NEOPIXEL_SCU_PIN,
	                   (uint16_t) (NEOPIXEL_SCU_FUNC | NEOPIXEL_OUT_MODE));
	WS_LOW();
	Chip_GPIO_SetPinDIROutput(LPC_GPIO_PORT, NEOPIXEL_PORT, NEOPIXEL_BIT);

	for (;;) {
		ws_send_grb(0x20, 0x00, 0x00);   /* dim green on */
		wait_cycles(hz / 2);             /* ~0.5 s       */
		ws_send_grb(0x00, 0x00, 0x00);   /* off          */
		wait_cycles(hz / 2);             /* ~0.5 s       */
	}
}
