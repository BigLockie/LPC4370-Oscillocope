/*
 * board.h - minimal shim so the vendored LPCOpen CDC sources (cdc_vcom.c,
 *           cdc_desc.c, app_usb_cdc.c) compile without pulling in the
 *           bambino / keil_mcb_18xx board library.
 *
 * The CDC example expects `#include "board.h"` to land it in a world
 * with chip.h, the LPC USBD ROM headers, and a few DEBUG* macros. We
 * provide just that.
 *
 * Anything that the bambino board.h provided but the CDC code doesn't
 * actually need (LED helpers, SDRAM init, ethernet pin mux) is omitted.
 */

#ifndef _BOARD_H_
#define _BOARD_H_

#include "chip.h"

/* DEBUG* macros - the CDC example calls DEBUGSTR()/DEBUGOUT() to print
 * status. Route to nowhere; we don't have a debug UART in this firmware
 * (USB-CDC IS our serial). If you later add a SWO/UART debug channel,
 * point these at it. */
#define DEBUGSTR(s)         ((void)(s))
#define DEBUGOUT(...)       ((void)0)

/* Some LPCOpen headers reference Board_Init() - we provide a no-op stub
 * (clocks and GPIO are already set up by clock_init() / afe_init() in
 * main.c before the CDC stack is touched). */
static inline void Board_Init(void) { }

#endif /* _BOARD_H_ */
