/*
 * adchs.c - High-Speed ADC capture via GPDMA (high-speed burst).
 *
 * The HSADC free-runs (single CH1 descriptor, looping at the descriptor-timer
 * rate) and continuously fills its FIFO. GPDMA channel 0 drains the FIFO into
 * SRAM with no CPU involvement, capturing ADCHS_BLOCK_SAMPLES *consecutive*
 * samples at the full ADC rate (up to ~68 MSa/s on the 68 MHz clock, ~80 with
 * PLL0AUDIO). That's what lets us sample fast enough for MHz-range signals
 * without the aliasing the polled readout produced.
 *
 * The routing the HSADC needs is NOT in LPCOpen and is NOT the GIMA (which only
 * routes triggers): the ADCHS *read* DMA request is GPDMA request line 8,
 * selected by writing 0x3 to its field in LPC_CREG->DMAMUX. (Reference: NXP
 * community + Embedded Artists LabTool capture_vadc.c.) GPDMA registers are
 * programmed directly because LPCOpen's Chip_GPDMA_Transfer has no HSADC entry.
 *
 * Current scope: CH1 only, unpacked (1 sample per 32-bit FIFO word), free-run
 * (no edge trigger yet - that returns next, via threshold gating or a software
 * trigger on a larger capture). PACKED_READ (2 samples/word) is needed to push
 * past ~30 MSa/s toward the 80 MSa/s goal and is the next step after this works.
 *
 * IMPORTANT TERMINOLOGY: LPCOpen calls this HSADC; NXP also says ADCHS/VADC.
 */

#include "adchs.h"
#include "../inc/pin_map.h"

#include "chip.h"

#include <string.h>

#ifndef LPC_HSADC
#define LPC_HSADC LPC_ADCHS
#endif

/* HSADC FIFO read port (FIFO_OUTPUT[0]) - the GPDMA source. */
#define HSADC_FIFO_SRC        ((uint32_t) &LPC_HSADC->FIFO_OUTPUT[0])
/* GPDMA peripheral-request line for the ADCHS read path (DMAMUX field = 0x3). */
#define HSADC_DMA_READ_LINE   8u

/* ---- module state ---- */

/* DMA destination: AHB SRAM at 0x20000000 (32 KB bank). The GPDMA can reach the
 * AHB SRAM banks but NOT the CPU-local SRAM at 0x10000000 where our firmware
 * (and a normal static buffer) lives so the DMA buffer MUST be placed here,
 * not in BSS. One 32-bit word per (unpacked) sample → 4 KB, fits in the 32 KB. */
#define HSADC_DMA_DEST     0x20000000u
#define HSADC_DMA_DEST_PTR ((volatile uint32_t *) HSADC_DMA_DEST)

static volatile bool s_block_ready_ch1 = false;
static volatile bool s_block_ready_ch2 = false;
static volatile bool s_capture_active  = false;

static bool     s_ch1_enabled   = true;
static bool     s_ch2_enabled   = true;
static uint16_t s_match_value   = 3;         /* ~17 MSa/s default (68 MHz / 4) */
static uint8_t  s_trig_source   = 1;
static int32_t  s_trig_level_uv = 0;
static uint8_t  s_trig_mode     = ADCHS_TRIG_RISING;

/* Single free-running CH1 descriptor: convert CH1's input and loop, so the
 * FIFO holds a continuous stream of CH1 samples for the DMA to drain. */
static void apply_descriptors(void)
{
    Chip_HSADC_SetupDescEntry(LPC_HSADC, 0, 0,
        HSADC_DESC_CH(ADCHS_CHANNEL_FOR_CH1) |
        HSADC_DESC_MATCH(s_match_value)      |
        HSADC_DESC_RESET_TIMER               |
        HSADC_DESC_BRANCH_FIRST);            /* loop to descriptor 0 */
    Chip_HSADC_UpdateDescTable(LPC_HSADC, 0);
}

/* Arm GPDMA channel 0 for a one-shot FIFO->buffer burst of ADCHS_BLOCK_SAMPLES
 * 32-bit transfers, peripheral(8)-to-memory, DMA as flow controller. */
static void arm_dma(void)
{
    LPC_GPDMA->CH[0].CONFIG = 0;                  /* disable channel */
    LPC_GPDMA->INTTCCLEAR   = 0x01;
    LPC_GPDMA->INTERRCLR    = 0x01;

    LPC_GPDMA->CH[0].SRCADDR  = HSADC_FIFO_SRC;
    LPC_GPDMA->CH[0].DESTADDR = HSADC_DMA_DEST;
    LPC_GPDMA->CH[0].LLI      = 0;                /* one-shot, no chain */
    LPC_GPDMA->CH[0].CONTROL  =
        (ADCHS_BLOCK_SAMPLES & 0xFFF) |           /* transfer count (32-bit words) */
        (0x2u << 12) |                            /* source burst    = 8  */
        (0x2u << 15) |                            /* dest burst      = 8  */
        (0x2u << 18) |                            /* source width    = 32-bit */
        (0x2u << 21) |                            /* dest width      = 32-bit */
        (0x1u << 24) |                            /* source AHB master 1 */
        (0x1u << 25) |                            /* dest AHB master 1 */
        (0x0u << 26) |                            /* source no-increment (FIFO) */
        (0x1u << 27) |                            /* dest increment */
        (0x1u << 31);                             /* terminal-count interrupt */
    LPC_GPDMA->CH[0].CONFIG =
        (0x1u) |                                  /* channel enable */
        (HSADC_DMA_READ_LINE << 1) |              /* src peripheral = 8 (ADCHS read) */
        (0x2u << 11) |                            /* flow: peripheral -> memory (DMA ctrl) */
        (0x1u << 14) |                            /* interrupt-error mask */
        (0x1u << 15);                             /* terminal-count int mask */
}

/* ---- init ---- */

void adchs_init(void)
{
    Chip_HSADC_Init(LPC_HSADC);                  /* clocks + reset            */
    Chip_HSADC_SetPowerSpeed(LPC_HSADC, false);  /* CRS/DGEC + offset-binary  */
    Chip_HSADC_EnablePower(LPC_HSADC);           /* analog power + band gap   */
    Chip_HSADC_SetupFIFO(LPC_HSADC, 8, false);   /* trip at 8, unpacked       */
    Chip_HSADC_ConfigureTrigger(LPC_HSADC,
        HSADC_CONFIG_TRIGGER_SW,
        HSADC_CONFIG_TRIGGER_RISEEXT,
        HSADC_CONFIG_TRIGGER_NOEXTSYNC,
        HSADC_CHANNEL_ID_EN_NONE,
        0x80);
    apply_descriptors();

    /* Route the ADCHS read DMA request onto GPDMA request line 8 (DMAMUX=0x3).
     * This is the piece LPCOpen omits and the README mis-attributed to GIMA. */
    LPC_CREG->DMAMUX &= ~(0x3u << (HSADC_DMA_READ_LINE * 2));
    LPC_CREG->DMAMUX |=  (0x3u << (HSADC_DMA_READ_LINE * 2));

    Chip_GPDMA_Init(LPC_GPDMA);
    LPC_GPDMA->CONFIG = 0x01;                     /* enable GPDMA, little-endian */
    while (!(LPC_GPDMA->CONFIG & 0x01)) { }
    NVIC_EnableIRQ(DMA_IRQn);
}

/* ---- configuration ---- */

void adchs_set_channel_enabled(uint8_t channel, bool enabled)
{
    if      (channel == 1) s_ch1_enabled = enabled;
    else if (channel == 2) s_ch2_enabled = enabled;
}

void adchs_set_sample_rate_divider(uint16_t divider)
{
    if (divider == 0) divider = 1;
    uint32_t match = (uint32_t) divider - 1u;
    if (match > 0x3FFF) match = 0x3FFF;          /* 14-bit MATCH field; DMA isn't CPU-limited */
    s_match_value = (uint16_t) match;
    apply_descriptors();
}

void adchs_set_trigger(uint8_t source_channel, int32_t level_uv, uint8_t mode_code)
{
    s_trig_source   = (source_channel == 2) ? 2 : 1;
    s_trig_level_uv = level_uv;
    s_trig_mode     = mode_code;
}

/* ---- capture lifecycle ---- */

void adchs_start_capture(void)
{
    s_block_ready_ch1 = false;
    s_capture_active  = true;

    Chip_HSADC_FlushFIFO(LPC_HSADC);
    arm_dma();
    Chip_HSADC_SetActiveDescriptor(LPC_HSADC, 0, 0);
    Chip_HSADC_SWTrigger(LPC_HSADC);             /* start the free-running chain */
}

void adchs_stop_capture(void)
{
    s_capture_active = false;
    LPC_GPDMA->CH[0].CONFIG = 0;                  /* halt the DMA channel */
}

/* ---- readout ---- */

bool adchs_block_ready(uint8_t channel)
{
    if (channel == 1) return s_block_ready_ch1;
    return false;                                 /* CH1-only in this build */
}

uint16_t adchs_get_block(uint8_t channel, uint16_t *out_buf)
{
    if (channel != 1) return 0;

    /* Copy + mask to 12 bits (unpacked: sample is in bits [11:0]) from the
     * AHB-SRAM DMA buffer. */
    for (uint16_t i = 0; i < ADCHS_BLOCK_SAMPLES; i++) {
        out_buf[i] = (uint16_t) (HSADC_DMA_DEST_PTR[i] & 0x0FFFu);
    }
    s_block_ready_ch1 = false;

    /* Re-arm for the next burst. The HSADC keeps free-running, so just flush
     * the stale/overflowed FIFO and re-start the DMA (no re-trigger needed). */
    if (s_capture_active) {
        Chip_HSADC_FlushFIFO(LPC_HSADC);
        arm_dma();
    }
    return ADCHS_BLOCK_SAMPLES;
}

void adchs_dma_complete_isr(uint8_t dma_channel)
{
    if (dma_channel == 0) s_block_ready_ch1 = true;
    if (dma_channel == 1) s_block_ready_ch2 = true;
}

#ifdef DMA_DEBUG
/* Diagnostic register snapshot, packed as 12-bit chunks (3 per 32-bit reg) so
 * it survives the protocol's 12-bit sample masking. out[] needs >= 27 entries. */
void adchs_get_debug(uint16_t *out, int n)
{
    uint32_t r[9];
    r[0] = LPC_GPDMA->CH[0].CONFIG;       /* bit0=enable, bit17=active        */
    r[1] = Chip_Clock_GetRate(CLK_ADCHS); /* live ADCHS clock (Hz) — 0/garbage => dead clock */
    r[2] = LPC_GPDMA->RAWINTTCSTAT;       /* bit0 = ch0 terminal count (done) */
    r[3] = LPC_GPDMA->RAWINTERRSTAT;      /* bit0 = ch0 error                 */
    r[4] = LPC_GPDMA->CH[0].CONTROL;      /* low 12 bits = remaining count    */
    r[5] = LPC_GPDMA->CH[0].DESTADDR;     /* advances while transferring       */
    r[6] = LPC_HSADC->FIFO_STS;           /* FIFO fill level (is it filling?) */
    r[7] = LPC_HSADC->DSCR_STS;           /* descriptor running?              */
    r[8] = LPC_HSADC->LAST_SAMPLE[ADCHS_CHANNEL_FOR_CH1]; /* converter output: DONE(bit0)+data => converting */
    for (int i = 0; i < 9 && (3 * i + 2) < n; i++) {
        out[3 * i + 0] = (uint16_t) ((r[i] >> 24) & 0xFF);
        out[3 * i + 1] = (uint16_t) ((r[i] >> 12) & 0xFFF);
        out[3 * i + 2] = (uint16_t) (r[i] & 0xFFF);
    }
}
#endif
