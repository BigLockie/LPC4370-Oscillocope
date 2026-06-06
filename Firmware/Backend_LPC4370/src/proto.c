/*
 * proto.c - packet parser/builder. See proto.h for the protocol.
 *
 * Receive parser is a small state machine driven one byte at a time
 * (so it can be called straight out of a USB CDC RX interrupt or from
 * the main superloop):
 *
 *     IDLE -> wait for 0xAA
 *     CMD  -> next byte is the command ID, look up payload length
 *     PAY  -> collect payload_len bytes
 *     SUM  -> next byte is the checksum; verify and emit
 *
 * Unknown command IDs are silently dropped (state returns to IDLE).
 * A bad checksum drops the packet and returns to IDLE.
 */

#include "proto.h"

#include <string.h>

/* ---- helpers ---- */

uint8_t proto_xor(const uint8_t *data, uint16_t len)
{
    uint8_t c = 0;
    for (uint16_t i = 0; i < len; i++) {
        c ^= data[i];
    }
    return c;
}

/* Payload length lookup for each command ID. -1 = unknown. */
static int8_t payload_len_for(proto_cmd_id_t id)
{
    switch (id) {
        case PROTO_CMD_SET_TIMEBASE:       return 4;
        case PROTO_CMD_SET_VDIV:           return 4;
        case PROTO_CMD_SET_VOFFSET:        return 4;
        case PROTO_CMD_SET_TRIGGER_LEVEL:  return 4;
        case PROTO_CMD_SET_TRIGGER_MODE:   return 1;
        case PROTO_CMD_SET_TRIGGER_SOURCE: return 1;
        case PROTO_CMD_SET_CHANNEL:        return 2;
        case PROTO_CMD_RUN:                return 0;
        case PROTO_CMD_STOP:               return 0;
        case PROTO_CMD_SINGLE:             return 0;
        default:                           return -1;
    }
}

/* ---- parser state ---- */

typedef enum { S_IDLE, S_CMD, S_PAY, S_SUM } parser_state_t;

static parser_state_t s_state         = S_IDLE;
static proto_cmd_t    s_partial       = {0};
static uint8_t        s_payload_pos   = 0;
static uint8_t        s_running_xor   = 0;   /* XOR of bytes seen so far in current packet */

bool proto_parse_byte(uint8_t b, proto_cmd_t *out_cmd)
{
    switch (s_state) {

    case S_IDLE:
        if (b == PROTO_START_BYTE) {
            s_running_xor   = b;
            s_payload_pos   = 0;
            s_state         = S_CMD;
        }
        return false;

    case S_CMD: {
        int8_t plen = payload_len_for((proto_cmd_id_t)b);
        if (plen < 0) {
            /* Unknown command - abort */
            s_state = S_IDLE;
            return false;
        }
        s_partial.id          = (proto_cmd_id_t)b;
        s_partial.payload_len = (uint8_t)plen;
        s_running_xor        ^= b;
        s_state               = (plen == 0) ? S_SUM : S_PAY;
        return false;
    }

    case S_PAY:
        s_partial.payload[s_payload_pos++] = b;
        s_running_xor ^= b;
        if (s_payload_pos >= s_partial.payload_len) {
            s_state = S_SUM;
        }
        return false;

    case S_SUM:
        if (b == s_running_xor) {
            memcpy(out_cmd, &s_partial, sizeof(proto_cmd_t));
            s_state = S_IDLE;
            return true;
        }
        /* Checksum bad - drop the packet */
        s_state = S_IDLE;
        return false;
    }

    /* Should be unreachable */
    s_state = S_IDLE;
    return false;
}

/* ---- builder ---- */

uint16_t proto_build_data_packet(uint8_t       *out_buf,
                                 uint16_t       out_buf_len,
                                 uint8_t        data_type,
                                 const uint16_t *samples,
                                 uint16_t       sample_count)
{
    /* Required size: 1 start + 1 type + 2 count + 2N samples + 1 checksum */
    uint32_t needed = 5u + (uint32_t)sample_count * 2u;
    if (needed > out_buf_len) {
        return 0;
    }

    uint16_t pos = 0;
    out_buf[pos++] = PROTO_START_BYTE;
    out_buf[pos++] = data_type;
    out_buf[pos++] = (uint8_t)((sample_count >> 8) & 0xFF);
    out_buf[pos++] = (uint8_t)( sample_count       & 0xFF);

    for (uint16_t i = 0; i < sample_count; i++) {
        uint16_t s = samples[i] & 0x0FFF;          /* mask to 12 bits */
        out_buf[pos++] = (uint8_t)((s >> 8) & 0xFF);
        out_buf[pos++] = (uint8_t)( s       & 0xFF);
    }

    out_buf[pos] = proto_xor(out_buf, pos);
    return pos + 1;
}
