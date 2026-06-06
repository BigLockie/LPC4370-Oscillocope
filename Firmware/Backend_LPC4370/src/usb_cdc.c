/*
 * usb_cdc.c - thin wrapper around the vendored LPCOpen CDC ACM sources
 *             in src/lpcopen_cdc/. The implementations of vcom_init /
 *             vcom_bread / vcom_write live in cdc_vcom.c; the USB stack
 *             bring-up + IRQ live in app_usb_cdc.c. We just adapt their
 *             return types and add a coarse "connected" check.
 */

#include "usb_cdc.h"
#include "lpcopen_cdc/cdc_vcom.h"

/* Provided by app_usb_cdc.c - brings up USB hardware + ROM stack + CDC class. */
extern void app_usb_cdc_init(void);

bool usb_cdc_init(void)
{
    app_usb_cdc_init();
    return true;
}

void usb_cdc_task(void)
{
    /* USB is interrupt-driven (USB_IRQHandler in app_usb_cdc.c). The
     * LPCOpen CDC example doesn't need a per-tick service call. Leave
     * this as a placeholder so the main superloop can stay symmetric. */
}

bool usb_cdc_is_connected(void)
{
    return vcom_connected() ? true : false;
}

uint16_t usb_cdc_read(uint8_t *buf, uint16_t max_len)
{
    /* vcom_bread is non-blocking when len is small; returns bytes copied. */
    return (uint16_t)vcom_bread(buf, max_len);
}

uint16_t usb_cdc_write(const uint8_t *buf, uint16_t len)
{
    /* vcom_write takes a non-const pointer; safe cast because the function
     * only reads from the buffer (it copies into the USB endpoint). */
    return (uint16_t)vcom_write((uint8_t *)buf, len);
}
