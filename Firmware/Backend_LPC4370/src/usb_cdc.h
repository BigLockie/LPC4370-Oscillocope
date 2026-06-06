/*
 * usb_cdc.h - USB CDC ACM (virtual COM port) interface.
 *
 * Wraps LPCOpen / nxpUSBlib's CDC class so the rest of the firmware
 * sees a clean read/write API. After usb_cdc_init() the device
 * enumerates as "FastOscilloscope" / generic CDC ACM on the host.
 */

#ifndef _USB_CDC_H_
#define _USB_CDC_H_

#include <stdbool.h>
#include <stdint.h>

/* Bring USB stack + CDC class up. Blocks until the host has enumerated
 * the device (or returns false on timeout). */
bool usb_cdc_init(void);

/* Pump the USB stack - call from the main loop on every iteration.
 * This drives RX, TX, and class control transfers. */
void usb_cdc_task(void);

/* Returns true once the host has opened the COM port. */
bool usb_cdc_is_connected(void);

/* Pull up to max_len bytes from the RX ring. Returns count actually read. */
uint16_t usb_cdc_read(uint8_t *buf, uint16_t max_len);

/* Push len bytes to the TX ring. Returns count actually queued. May
 * return less than len if the ring is full; caller should retry. */
uint16_t usb_cdc_write(const uint8_t *buf, uint16_t len);

#endif /* _USB_CDC_H_ */
