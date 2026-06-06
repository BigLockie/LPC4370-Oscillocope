/*
 * proto.h
 *
 * FastOscilloscope serial protocol - must stay byte-for-byte in sync
 * with `Code/host_bridge.py` on the laptop side.
 *
 *   Command packet (Host -> MCU)
 *     [0xAA] [cmd_id] [0..4 byte payload, MSB-first] [XOR checksum]
 *     Max packet size: 7 bytes.
 *
 *   Data packet (MCU -> Host)
 *     [0xAA] [data_type] [count_hi] [count_lo]
 *     [N × 16-bit MSB-first samples] [XOR checksum]
 *     data_type 0x80 = Ch1 ADC block, 0x81 = Ch2 ADC block.
 *
 * The proto module is the only place in the firmware that knows about
 * byte layouts. afe.c, adchs.c, and main.c speak in decoded structs.
 */

#ifndef _PROTO_H_
#define _PROTO_H_

#include <stdbool.h>
#include <stdint.h>

#define PROTO_START_BYTE         0xAA
#define PROTO_DATA_TYPE_CH1      0x80
#define PROTO_DATA_TYPE_CH2      0x81
#define PROTO_MAX_CMD_PKT_LEN    7
#define PROTO_MAX_PAYLOAD_LEN    4

/* Command IDs (must match host_bridge.py CMD_* constants) */
typedef enum {
    PROTO_CMD_SET_TIMEBASE       = 0x01, /* uint32 ns/div, big-endian */
    PROTO_CMD_SET_VDIV           = 0x02, /* uint32 µV/div, big-endian */
    PROTO_CMD_SET_VOFFSET        = 0x03, /*  int32 µV,     big-endian */
    PROTO_CMD_SET_TRIGGER_LEVEL  = 0x04, /*  int32 µV,     big-endian */
    PROTO_CMD_SET_TRIGGER_MODE   = 0x05, /* 1 byte: 0=rising 1=falling 2=auto */
    PROTO_CMD_SET_TRIGGER_SOURCE = 0x06, /* 1 byte: 1=Ch1 2=Ch2 */
    PROTO_CMD_SET_CHANNEL        = 0x07, /* 2 bytes: channel, enabled */
    /* 0x08 is the next free ID. The AC/DC coupling switch wants to live here,
     * for example PROTO_CMD_SET_COUPLING = 0x08 with a 2 byte payload
     * (channel, dc_mode), dispatched in main.c to afe_set_coupling_dc(). Add
     * the matching CMD_SET_COUPLING to host_bridge.py so the two stay in sync
     * (test_proto_parity.py checks that). 0x09..0x0F are free after it. */
    PROTO_CMD_RUN                = 0x10, /* no payload */
    PROTO_CMD_STOP               = 0x11, /* no payload */
    PROTO_CMD_SINGLE             = 0x12, /* no payload */
} proto_cmd_id_t;

/* Decoded command struct returned by the parser. */
typedef struct {
    proto_cmd_id_t id;
    uint8_t        payload_len;
    uint8_t        payload[PROTO_MAX_PAYLOAD_LEN];
} proto_cmd_t;

/* XOR checksum over an arbitrary byte string. */
uint8_t proto_xor(const uint8_t *data, uint16_t len);

/*
 * Feed a single received byte to the parser. Returns true once a
 * complete, checksum-valid packet has been assembled into *out_cmd.
 * The caller drains the byte stream in a loop and dispatches when
 * proto_parse_byte() returns true.
 */
bool proto_parse_byte(uint8_t b, proto_cmd_t *out_cmd);

/*
 * Build a Channel-N data packet into the caller's buffer.
 *
 *   out_buf       - buffer at least 5 + sample_count*2 bytes
 *   out_buf_len   - capacity of out_buf
 *   data_type     - PROTO_DATA_TYPE_CH1 or _CH2
 *   samples       - array of 12-bit ADC counts (zero-padded in 16 bits)
 *   sample_count  - number of samples (≤ 1024)
 *
 * Returns the number of bytes written, or 0 on overflow.
 */
uint16_t proto_build_data_packet(uint8_t       *out_buf,
                                 uint16_t       out_buf_len,
                                 uint8_t        data_type,
                                 const uint16_t *samples,
                                 uint16_t       sample_count);

#endif /* _PROTO_H_ */
