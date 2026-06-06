/*
 * main.c - FastOscilloscope LPC4370 firmware entry.
 *
 * Superloop architecture (no RTOS):
 *
 *   while (1) {
 *       usb_cdc_task();                   // pump USB stack
 *       drain_rx_and_dispatch();          // parse incoming command bytes
 *       if (acquiring) maybe_send_block(); // ship completed ADC bursts
 *   }
 *
 * Two interrupt sources:
 *   - USB IRQ: filled by nxpUSBlib's usbd_rom driver (handled inside vcom_*)
 *   - DMA IRQ: ADCHS burst-complete; sets the ready flag in adchs.c
 *
 * Scaling and trigger arithmetic stays on the host (host_bridge.py /
 * Display block) - the firmware just bursts raw 12-bit samples. This
 * matches the Block 2 design doc's pipeline ("Backend converts ADC
 * counts to voltage").
 */

#include "adchs.h"
#include "afe.h"
#include "clock.h"
#include "proto.h"
#include "usb_cdc.h"

#include "chip.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* ---- runtime state ---- */

static bool s_acquiring     = false;
static bool s_single_pending = false;

/* Trigger state - cached across the 3 individual SET_TRIGGER_* commands
 * so we always have a coherent (source, level, mode) triple to push down
 * to adchs_set_trigger().                                              */
static uint8_t s_trig_source = 1;
static int32_t s_trig_level  = 0;
static uint8_t s_trig_mode   = 0;  /* 0=rising, 1=falling, 2=auto */

static uint8_t  s_pkt_scratch[5 + 2 * ADCHS_BLOCK_SAMPLES];
static uint16_t s_sample_scratch[ADCHS_BLOCK_SAMPLES];

/* ---- helpers ---- */

static uint32_t be_u32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  |  (uint32_t)p[3];
}
static int32_t  be_i32(const uint8_t *p) { return (int32_t)be_u32(p); }

/* ---- command dispatch ---- */

static void handle_command(const proto_cmd_t *cmd)
{
    switch (cmd->id) {

    case PROTO_CMD_SET_TIMEBASE: {
        /* uint32 ns/div from the host. We want one ADCHS_BLOCK_SAMPLES-sample
         * block to cover (10 divs × ns_per_div) of time, so:
         *   sample_rate   = SAMPLES / (10 × ns_per_div × 1e-9)
         *                 = SAMPLES × 1e8 / ns_per_div   [Hz]
         *   ADCHS divider = ADCHS_BASE_CLOCK_HZ / sample_rate
         *                 = ADCHS_BASE_CLOCK_HZ × ns_per_div / (SAMPLES × 1e8)
         * (SAMPLES × 1e8 is derived from ADCHS_BLOCK_SAMPLES so depth + this
         *  math can never drift apart.) Clamped to [1, 65535]. The host applies
         *  the identical formula for its time axis, so it stays accurate even
         *  when the divider clamps at fast timebases. */
        uint32_t ns_per_div = be_u32(cmd->payload);
        if (ns_per_div == 0) break;
        uint64_t denom = (uint64_t)ADCHS_BLOCK_SAMPLES * 100000000ULL;
        uint64_t div = ((uint64_t)ADCHS_BASE_CLOCK_HZ * ns_per_div) / denom;
        if (div < 1)     div = 1;
        if (div > 65535) div = 65535;
        adchs_set_sample_rate_divider((uint16_t)div);
        break;
    }

    case PROTO_CMD_SET_VDIV: {
        /* Payload is uV per division. We turn that requested vertical scale
         * into one of the four AFE gain cells: a small V/div means the user is
         * looking at a tiny signal, so we want the biggest gain to fill the
         * ADC range, and a large V/div wants the smallest gain so a big signal
         * does not clip. The thresholds below are the crossover points between
         * the 10x, 2.564x, 0.833x, and 0.256x cells.
         *
         * Note this drives BOTH channels to the same gain. There is no per
         * channel gain command yet; if you need independent gain, see the
         * variable gain notes in afe.c and the README. */
        uint32_t uv_per_div = be_u32(cmd->payload);
        afe_gain_t g;
        if      (uv_per_div <  100000) g = AFE_GAIN_10_0;     /* below 0.1 V/div */
        else if (uv_per_div <  500000) g = AFE_GAIN_2_564;
        else if (uv_per_div < 2000000) g = AFE_GAIN_0_833;
        else                           g = AFE_GAIN_0_256;
        afe_set_gain(1, g);
        afe_set_gain(2, g);
        break;
    }

    /* When the AC/DC coupling switch gets wired up, its command handler goes
     * right here next to the other AFE control, for example:
     *   case PROTO_CMD_SET_COUPLING:
     *       afe_set_coupling_dc(cmd->payload[0], cmd->payload[1]);
     *       break;
     * The driver afe_set_coupling_dc() already exists; this case plus the
     * matching proto.h ID and host_bridge.py entry are all that is missing. */

    case PROTO_CMD_SET_VOFFSET:
        /* int32 µV - analog offset is fixed in this AFE; no-op for now. */
        (void)be_i32(cmd->payload);
        break;

    case PROTO_CMD_SET_TRIGGER_LEVEL:
        s_trig_level = be_i32(cmd->payload);
        adchs_set_trigger(s_trig_source, s_trig_level, s_trig_mode);
        break;

    case PROTO_CMD_SET_TRIGGER_MODE:
        /* payload[0]: 0=rising, 1=falling, 2=auto (matches ADCHS_TRIG_*) */
        s_trig_mode = cmd->payload[0];
        adchs_set_trigger(s_trig_source, s_trig_level, s_trig_mode);
        break;

    case PROTO_CMD_SET_TRIGGER_SOURCE:
#ifdef DMA_DEBUG_SWEEP
        /* DIAGNOSTIC: hijack this command to re-point the HSADC input (0-5) so
         * the host can sweep all inputs and find which one carries the signal. */
        {
            extern void adchs_debug_set_input(uint8_t);
            adchs_debug_set_input(cmd->payload[0]);
        }
        break;
#else
        /* payload[0]: 1=Ch1, 2=Ch2 */
        s_trig_source = (cmd->payload[0] == 2) ? 2 : 1;
        adchs_set_trigger(s_trig_source, s_trig_level, s_trig_mode);
        break;
#endif

    case PROTO_CMD_SET_CHANNEL: {
        /* payload[0] = channel (1 or 2), payload[1] = enabled (0/1) */
        uint8_t ch = cmd->payload[0];
        bool    on = cmd->payload[1] != 0;
        adchs_set_channel_enabled(ch, on);
        break;
    }

    case PROTO_CMD_RUN:
        if (!s_acquiring) {
            s_acquiring = true;
            adchs_start_capture();
        }
        break;

    case PROTO_CMD_STOP:
        if (s_acquiring) {
            adchs_stop_capture();
            s_acquiring = false;
        }
        break;

    case PROTO_CMD_SINGLE:
        s_single_pending = true;
        if (!s_acquiring) {
            s_acquiring = true;
            adchs_start_capture();
        }
        break;
    }
}

static void drain_rx_and_dispatch(void)
{
    uint8_t buf[64];
    uint16_t n = usb_cdc_read(buf, sizeof(buf));
    proto_cmd_t cmd;
    for (uint16_t i = 0; i < n; i++) {
        if (proto_parse_byte(buf[i], &cmd)) {
            handle_command(&cmd);
        }
    }
}

/* Throttle outgoing ADC blocks to ~30/s. The polled capture can fill blocks
 * far faster than USB-CDC (~1 MB/s) can carry them; streaming unthrottled
 * floods the link and corrupts packets. 30 blocks/s is plenty for a display
 * refresh and stays well inside the CDC budget. Uses the DWT cycle counter
 * (enabled in main()). */
static uint32_t s_last_block_cyc = 0;

static void maybe_send_block(void)
{
#ifdef DMA_DEBUG
    /* Diagnostic build: stream the GPDMA/HSADC register snapshot CONTINUOUSLY
     * from boot - independent of s_acquiring and adchs_start_capture(). This
     * lets us tell whether the USB/streaming path works on its own. The capture
     * is (optionally) kicked once at boot via DMA_DEBUG_BOOT_CAPTURE in main(). */
    if ((DWT->CYCCNT - s_last_block_cyc) < (SystemCoreClock / 30u)) return;
    s_last_block_cyc = DWT->CYCCNT;
    {
        extern void adchs_get_debug(uint16_t *, int);
        uint16_t dbg[27];
        adchs_get_debug(dbg, 27);
        uint16_t db = proto_build_data_packet(s_pkt_scratch, sizeof(s_pkt_scratch),
                                              PROTO_DATA_TYPE_CH1, dbg, 27);
        uint16_t sent = (db > 0) ? usb_cdc_write(s_pkt_scratch, db) : 0;
#ifdef DMA_DEBUG_DELAYED_CAPTURE
        /* Bisect: kick the capture ONCE, but only AFTER the host has actually
         * received ~20 frames (writes succeed only when a host is draining the
         * endpoint). This guarantees a listener is connected and watching when
         * capture fires, so it sees the pre/post transition. If the stream dies
         * right after, adchs_start_capture() is what wedges USB. */
        {
            static uint32_t s_dbg_sent = 0;
            if (sent > 0 && ++s_dbg_sent == 20) {
                adchs_start_capture();
            }
        }
#else
        (void) sent;
#endif
    }
    return;
#else
    if (!s_acquiring) return;

    if ((DWT->CYCCNT - s_last_block_cyc) < (SystemCoreClock / 30u)) return;
    s_last_block_cyc = DWT->CYCCNT;

    if (adchs_block_ready(1)) {
        uint16_t count = adchs_get_block(1, s_sample_scratch);
        uint16_t bytes = proto_build_data_packet(
            s_pkt_scratch, sizeof(s_pkt_scratch),
            PROTO_DATA_TYPE_CH1, s_sample_scratch, count);
        if (bytes > 0) {
            usb_cdc_write(s_pkt_scratch, bytes);
        }
    }

    if (adchs_block_ready(2)) {
        uint16_t count = adchs_get_block(2, s_sample_scratch);
        uint16_t bytes = proto_build_data_packet(
            s_pkt_scratch, sizeof(s_pkt_scratch),
            PROTO_DATA_TYPE_CH2, s_sample_scratch, count);
        if (bytes > 0) {
            usb_cdc_write(s_pkt_scratch, bytes);
        }

        if (s_single_pending) {
            s_single_pending = false;
            adchs_stop_capture();
            s_acquiring = false;
        }
    }
#endif /* DMA_DEBUG */
}

/* ---- DMA IRQ glue ---- */
extern void adchs_dma_complete_isr(uint8_t dma_channel);

void DMA_IRQHandler(void)
{
    /* GPDMA terminal-count + error handling. */
    uint8_t tc = Chip_GPDMA_IntGetStatus(LPC_GPDMA, GPDMA_STAT_INTTC, 0);
    if (tc) { adchs_dma_complete_isr(0); Chip_GPDMA_ClearIntPending(LPC_GPDMA, GPDMA_STATCLR_INTTC, 0); }

    tc = Chip_GPDMA_IntGetStatus(LPC_GPDMA, GPDMA_STAT_INTTC, 1);
    if (tc) { adchs_dma_complete_isr(1); Chip_GPDMA_ClearIntPending(LPC_GPDMA, GPDMA_STATCLR_INTTC, 1); }

    /* Swallow errors - the bad block is dropped, capture continues. */
    for (uint8_t ch = 0; ch < 2; ch++) {
        if (Chip_GPDMA_IntGetStatus(LPC_GPDMA, GPDMA_STAT_INTERR, ch)) {
            Chip_GPDMA_ClearIntPending(LPC_GPDMA, GPDMA_STATCLR_INTERR, ch);
        }
    }
}

/* ---- entry ---- */

#ifdef MILESTONE1_BLINK
/* TEMPORARY bring-up hook (Milestone 1): visible WS2812 blink that never
 * returns. Defined in milestone1_blink.c, compiled in only for the blink
 * build. Remove once USB bring-up (Milestone 2+) is underway. */
extern void milestone1_blink_forever(void);
#endif

int main(void)
{
    SystemCoreClockUpdate();
    clock_init();

#ifdef MILESTONE1_BLINK
    milestone1_blink_forever();   /* proves toolchain+flash+clock; loops forever */
#endif

    /* GPIO clock + AFE pin setup. */
    Chip_GPIO_Init(LPC_GPIO_PORT);
#ifndef MILESTONE2_USB_ONLY
    afe_init();

    /* ADCHS + DMA bring-up (idle until first RUN). */
    adchs_init();
#endif

    /* USB CDC bring-up; blocks until host enumerates the COM port. */
    usb_cdc_init();

    /* USB is up, so PLL0USB (480 MHz) now exists - bump the ADC sample clock
     * from the 68 MHz default to 80 MHz (480/6). Harmless if the ADC is unused. */
#ifndef ADCHS_CLOCK_68MHZ   /* define to stay at the 68 MHz clock_init default */
    clock_set_adchs_80mhz();
#endif

    /* Enable the DWT cycle counter - used by maybe_send_block() to throttle. */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;

#ifdef DMA_DEBUG_BOOT_CAPTURE
    /* Bisect hook: kick the HSADC capture once here, so the DMA_DEBUG stream
     * shows live FIFO/DSCR/DMA progress without depending on a host RUN. If the
     * USB dies right after this, the fault is inside adchs_start_capture(). */
    adchs_start_capture();
#endif

    /* Superloop. */
    while (1) {
        usb_cdc_task();
        drain_rx_and_dispatch();
        maybe_send_block();
    }

    /* Unreachable. */
    return 0;
}
