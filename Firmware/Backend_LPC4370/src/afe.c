/*
 * afe.c - AFE GPIO control.
 *
 * The gain switch is a one-hot selector: exactly one of SEL1..SEL4 is
 * driven high to select 0.256× / 0.833× / 2.564× / 10× respectively.
 * SELSSR controls coupling (high = DC, low = AC).
 *
 * Uses LPCOpen Chip APIs (Chip_GPIO_*, Chip_SCU_PinMux). We compile
 * with the `lpc_chip_43xx` library that ships with LPCOpen.
 */

#include "afe.h"
#include "../inc/pin_map.h"

#include "chip.h"

const float AFE_GAIN_MULT[AFE_GAIN_COUNT] = {
    [AFE_GAIN_0_256] = 0.256f,
    [AFE_GAIN_0_833] = 0.833f,
    [AFE_GAIN_2_564] = 2.564f,
    [AFE_GAIN_10_0]  = 10.0f,
};

/* SCU function for "normal GPIO output, no pull, no glitch filter" */
#define GPIO_OUT_MODE  (SCU_MODE_INACT | SCU_MODE_INBUFF_EN)

typedef struct {
    uint8_t scu_port, scu_pin, scu_func;
    uint8_t gpio_port, gpio_bit;
} pin_t;

/* Order must match afe_gain_t enum. */
static const pin_t s_ch1_sel[AFE_GAIN_COUNT] = {
    { CH1_SEL1_SCU_PORT, CH1_SEL1_SCU_PIN, CH1_SEL1_SCU_FUNC, CH1_SEL1_PORT, CH1_SEL1_BIT },
    { CH1_SEL2_SCU_PORT, CH1_SEL2_SCU_PIN, CH1_SEL2_SCU_FUNC, CH1_SEL2_PORT, CH1_SEL2_BIT },
    { CH1_SEL3_SCU_PORT, CH1_SEL3_SCU_PIN, CH1_SEL3_SCU_FUNC, CH1_SEL3_PORT, CH1_SEL3_BIT },
    { CH1_SEL4_SCU_PORT, CH1_SEL4_SCU_PIN, CH1_SEL4_SCU_FUNC, CH1_SEL4_PORT, CH1_SEL4_BIT },
};

static const pin_t s_ch2_sel[AFE_GAIN_COUNT] = {
    { CH2_SEL1_SCU_PORT, CH2_SEL1_SCU_PIN, CH2_SEL1_SCU_FUNC, CH2_SEL1_PORT, CH2_SEL1_BIT },
    { CH2_SEL2_SCU_PORT, CH2_SEL2_SCU_PIN, CH2_SEL2_SCU_FUNC, CH2_SEL2_PORT, CH2_SEL2_BIT },
    { CH2_SEL3_SCU_PORT, CH2_SEL3_SCU_PIN, CH2_SEL3_SCU_FUNC, CH2_SEL3_PORT, CH2_SEL3_BIT },
    { CH2_SEL4_SCU_PORT, CH2_SEL4_SCU_PIN, CH2_SEL4_SCU_FUNC, CH2_SEL4_PORT, CH2_SEL4_BIT },
};

static const pin_t s_ch1_couple = {
    CH1_SELSSR_SCU_PORT, CH1_SELSSR_SCU_PIN, CH1_SELSSR_SCU_FUNC,
    CH1_SELSSR_PORT,     CH1_SELSSR_BIT,
};
static const pin_t s_ch2_couple = {
    CH2_SELSSR_SCU_PORT, CH2_SELSSR_SCU_PIN, CH2_SELSSR_SCU_FUNC,
    CH2_SELSSR_PORT,     CH2_SELSSR_BIT,
};

static void cfg_output(const pin_t *p, bool initial_high)
{
    Chip_SCU_PinMuxSet(p->scu_port, p->scu_pin,
                       (uint16_t)(p->scu_func | GPIO_OUT_MODE));
    Chip_GPIO_SetPinDIROutput(LPC_GPIO_PORT, p->gpio_port, p->gpio_bit);
    Chip_GPIO_SetPinState(LPC_GPIO_PORT, p->gpio_port, p->gpio_bit, initial_high);
}

static void drive_pin(const pin_t *p, bool high)
{
    Chip_GPIO_SetPinState(LPC_GPIO_PORT, p->gpio_port, p->gpio_bit, high);
}

void afe_init(void)
{
    /* Both channels: declare gain selectors and coupling pins as outputs. */
    for (int i = 0; i < AFE_GAIN_COUNT; i++) {
        cfg_output(&s_ch1_sel[i], false);
        cfg_output(&s_ch2_sel[i], false);
    }
    cfg_output(&s_ch1_couple, true);   /* default DC */
    cfg_output(&s_ch2_couple, true);

    /* Drive the safest-default gain on both channels. */
    afe_set_gain(1, AFE_GAIN_0_256);
    afe_set_gain(2, AFE_GAIN_0_256);
}

void afe_set_gain(uint8_t channel, afe_gain_t gain)
{
    if (channel != 1 && channel != 2) return;
    if (gain >= AFE_GAIN_COUNT)        return;

    const pin_t *table = (channel == 1) ? s_ch1_sel : s_ch2_sel;

    /* Break-before-make: drive ALL gain selectors low first, then assert
     * the new one. Prevents two MUX paths from conducting at the same
     * instant during a gain change, which on some make-before-break MUXes
     * can cause a transient that briefly forwards the wrong scale to the
     * ADC (or, worst case, fights the next stage). */
    for (int i = 0; i < AFE_GAIN_COUNT; i++) {
        drive_pin(&table[i], false);
    }
    drive_pin(&table[(int)gain], true);
}

void afe_set_coupling_dc(uint8_t channel, bool dc_mode)
{
    if (channel == 1) drive_pin(&s_ch1_couple, dc_mode);
    if (channel == 2) drive_pin(&s_ch2_couple, dc_mode);
}
