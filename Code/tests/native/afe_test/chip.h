/*
 * chip.h (HOST-TEST SHIM)
 *
 * Stand-in for LPCOpen's chip.h, used only when compiling Firmware/.../afe.c
 * on a host laptop for unit-testing. Provides the minimal subset of LPCOpen's
 * GPIO + SCU surface that afe.c actually touches.
 *
 * The real chip.h is many hundreds of lines pulling in CMSIS + every
 * peripheral header. We just need:
 *   - SCU_MODE_INACT, SCU_MODE_INBUFF_EN  defines used in GPIO_OUT_MODE
 *   - LPC_GPIO_T type, LPC_GPIO_PORT extern
 *   - Chip_SCU_PinMuxSet, Chip_GPIO_SetPinDIROutput, Chip_GPIO_SetPinState
 *
 * The stubs themselves live in lpcopen_stub.c and record every call into a
 * global log that Python reads via ctypes.
 */
#ifndef _CHIP_H_HOST_TEST_
#define _CHIP_H_HOST_TEST_

#include <stdint.h>
#include <stdbool.h>

#define SCU_MODE_INACT       (0u << 0)
#define SCU_MODE_INBUFF_EN   (1u << 4)

typedef struct { int dummy; } LPC_GPIO_T;
extern LPC_GPIO_T *LPC_GPIO_PORT;

void Chip_SCU_PinMuxSet       (uint8_t  port, uint8_t pin, uint16_t mode);
void Chip_GPIO_SetPinDIROutput(LPC_GPIO_T *p, uint8_t port, uint8_t bit);
void Chip_GPIO_SetPinState    (LPC_GPIO_T *p, uint8_t port, uint8_t bit, bool state);

#endif
