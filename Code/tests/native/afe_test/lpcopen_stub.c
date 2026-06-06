/*
 * lpcopen_stub.c - recording stand-ins for the few LPCOpen Chip_* calls
 * that afe.c uses. Every call appends a record to a global log that the
 * Python unit test reads via ctypes to assert the call sequence.
 *
 * Compile this together with Firmware/Backend_LPC4370/src/afe.c using the
 * sibling chip.h shim and an include path pointing at this folder.
 */

#include "chip.h"
#include <stddef.h>

/* Public log layout - mirror this in test_afe_unit.py's ctypes Structure. */
typedef struct {
    int op;       /* 0 = SCU mux, 1 = GPIO dir-out, 2 = GPIO set-state */
    int port;     /* SCU port for op=0, GPIO port for op=1/2          */
    int pin;     /* SCU pin  for op=0, GPIO bit  for op=1/2          */
    int value;    /* SCU mode for op=0, 0/1 state for op=2, unused op=1 */
} call_record_t;

#define LOG_CAP 1024

call_record_t afe_test_log[LOG_CAP];
int           afe_test_log_len = 0;

void afe_test_log_clear(void) { afe_test_log_len = 0; }

static LPC_GPIO_T g_lpc_gpio_port_storage;
LPC_GPIO_T *LPC_GPIO_PORT = &g_lpc_gpio_port_storage;

static void push(int op, int port, int pin, int value)
{
    if (afe_test_log_len < LOG_CAP) {
        afe_test_log[afe_test_log_len].op    = op;
        afe_test_log[afe_test_log_len].port  = port;
        afe_test_log[afe_test_log_len].pin   = pin;
        afe_test_log[afe_test_log_len].value = value;
        afe_test_log_len++;
    }
}

void Chip_SCU_PinMuxSet(uint8_t port, uint8_t pin, uint16_t mode)
{
    push(0, port, pin, (int)mode);
}

void Chip_GPIO_SetPinDIROutput(LPC_GPIO_T *p, uint8_t port, uint8_t bit)
{
    (void)p;
    push(1, port, bit, 0);
}

void Chip_GPIO_SetPinState(LPC_GPIO_T *p, uint8_t port, uint8_t bit, bool state)
{
    (void)p;
    push(2, port, bit, state ? 1 : 0);
}
