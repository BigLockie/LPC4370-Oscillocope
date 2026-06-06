/*
 * pin_map.h
 *
 * Named pin defines for the FastOscilloscope LPC4370 board. All values
 * come from the Luke's pin-assignment table (Pin Changes, rev. June 2026).
 *
 * The LPC4370 multiplexes I/O via the System Control Unit (SCU). Each
 * physical pin (e.g. P1_18) selects a function (FUNC0..FUNC7) that maps
 * it to a peripheral. To use a pin as a GPIO we also need to know which
 * GPIO port/bit it lands on after the SCU mapping; the LPC43xx user
 * manual Table 158 lists those.
 *
 * Format used below for GPIO outputs:
 *   <NAME>_PORT, <NAME>_BIT, <NAME>_SCU_PORT, <NAME>_SCU_PIN, <NAME>_SCU_FUNC
 *
 * The SCU_FUNC is whichever FUNC# routes the physical pin to a normal
 * GPIO pad (most LPC43xx pins use FUNC0 or FUNC4 for GPIO).
 */

#ifndef _PIN_MAP_H_
#define _PIN_MAP_H_

/* ----- Channel 1 AFE: gain switch (one-hot) ------------------------- */
/* CH1_SEL1 -> 0.256x   P6_2   -> GPIO3[1]   (FUNC0) */
#define CH1_SEL1_PORT      3
#define CH1_SEL1_BIT       1
#define CH1_SEL1_SCU_PORT  6
#define CH1_SEL1_SCU_PIN   2
#define CH1_SEL1_SCU_FUNC  0

/* CH1_SEL2 -> 0.833x   P1_17  -> GPIO0[12]  (FUNC0) */
#define CH1_SEL2_PORT      0
#define CH1_SEL2_BIT       12
#define CH1_SEL2_SCU_PORT  1
#define CH1_SEL2_SCU_PIN   17
#define CH1_SEL2_SCU_FUNC  0

/* CH1_SEL3 -> 2.564x   P1_16  -> GPIO0[3]   (FUNC0) */
#define CH1_SEL3_PORT      0
#define CH1_SEL3_BIT       3
#define CH1_SEL3_SCU_PORT  1
#define CH1_SEL3_SCU_PIN   16
#define CH1_SEL3_SCU_FUNC  0

/* CH1_SEL4 -> 10x      P1_18  -> GPIO0[13]  (FUNC0) */
#define CH1_SEL4_PORT      0
#define CH1_SEL4_BIT       13
#define CH1_SEL4_SCU_PORT  1
#define CH1_SEL4_SCU_PIN   18
#define CH1_SEL4_SCU_FUNC  0

/* CH1_SELSSR (DC=high, AC=low)   P1_20 -> GPIO0[15] (FUNC0) */
#define CH1_SELSSR_PORT      0
#define CH1_SELSSR_BIT       15
#define CH1_SELSSR_SCU_PORT  1
#define CH1_SELSSR_SCU_PIN   20
#define CH1_SELSSR_SCU_FUNC  0

/* ----- Channel 2 AFE: gain switch (one-hot) ------------------------- */
/* CH2_SEL1 -> 0.256x   P1_0   -> GPIO0[4]   (FUNC0) */
#define CH2_SEL1_PORT      0
#define CH2_SEL1_BIT       4
#define CH2_SEL1_SCU_PORT  1
#define CH2_SEL1_SCU_PIN   0
#define CH2_SEL1_SCU_FUNC  0

/* CH2_SEL2 -> 0.833x   P0_0   -> GPIO0[0]   (FUNC0) */
#define CH2_SEL2_PORT      0
#define CH2_SEL2_BIT       0
#define CH2_SEL2_SCU_PORT  0
#define CH2_SEL2_SCU_PIN   0
#define CH2_SEL2_SCU_FUNC  0

/* CH2_SEL3 -> 2.564x   P0_1   -> GPIO0[1]   (FUNC0) */
#define CH2_SEL3_PORT      0
#define CH2_SEL3_BIT       1
#define CH2_SEL3_SCU_PORT  0
#define CH2_SEL3_SCU_PIN   1
#define CH2_SEL3_SCU_FUNC  0

/* CH2_SEL4 -> 10x      P1_12  -> GPIO1[5]   (FUNC0) */
#define CH2_SEL4_PORT      1
#define CH2_SEL4_BIT       5
#define CH2_SEL4_SCU_PORT  1
#define CH2_SEL4_SCU_PIN   12
#define CH2_SEL4_SCU_FUNC  0

/* CH2_SELSSR (DC=high, AC=low)   P1_15 -> GPIO0[2]  (FUNC0) */
#define CH2_SELSSR_PORT      0
#define CH2_SELSSR_BIT       2
#define CH2_SELSSR_SCU_PORT  1
#define CH2_SELSSR_SCU_PIN   15
#define CH2_SELSSR_SCU_FUNC  0

/* ----- ADCHS analog inputs ----------------------------------------- */
/* ADCHS_0 = Channel 2 input (board net "Channel2") */
/* ADCHS_1 = Channel 1 input (board net "Channel1") */
#define ADCHS_CHANNEL_FOR_CH1   1   /* Ch1 PCB net  -> ADCHS input 1 */
#define ADCHS_CHANNEL_FOR_CH2   0   /* Ch2 PCB net  -> ADCHS input 0 */

/* ----- Misc -------------------------------------------------------- */
/* Neopixel data line                P1_14 -> GPIO1[7]  (FUNC0) */
#define NEOPIXEL_PORT      1
#define NEOPIXEL_BIT       7
#define NEOPIXEL_SCU_PORT  1
#define NEOPIXEL_SCU_PIN   14
#define NEOPIXEL_SCU_FUNC  0

#endif /* _PIN_MAP_H_ */
