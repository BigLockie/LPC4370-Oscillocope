/*
 * app_usb_cdc.c - refactored from LPCOpen's usbd_rom_cdc_vcom/cdc_main.c.
 *
 * Original copyright NXP Semiconductors, 2013. Distributed under the NXP
 * "permission to use, copy, modify, and distribute" terms (see header in
 * cdc_vcom.c). All we did:
 *   - renamed `int main(void)` -> `void app_usb_cdc_init(void)`
 *   - removed the infinite read-and-echo loop at the tail of main()
 *   - removed the Board_Init() call (we do clocks + GPIO in main.c before
 *     calling this); kept USB_init_pin_clk() since that's what actually
 *     muxes the USB pins and turns on the USB peripheral clock
 *
 * Helpers retained verbatim (the cdc_vcom.c source uses them):
 *   - USB_IRQHandler()
 *   - find_IntfDesc()
 *   - EP0_patch()
 *   - g_pUsbApi (global needed by the ROM driver shim macros)
 */

#include "board.h"
#include <string.h>
#include "app_usbd_cfg.h"
#include "cdc_vcom.h"

/* ----- private state ----- */

static USBD_HANDLE_T g_hUsb;

/* EP0 NAK-race workaround state */
static uint32_t           g_ep0RxBusy = 0;
static USB_EP_HANDLER_T   g_Ep0BaseHdlr;

const USBD_API_T *g_pUsbApi;

/* ----- private functions ----- */

static ErrorCode_t EP0_patch(USBD_HANDLE_T hUsb, void *data, uint32_t event)
{
    switch (event) {
    case USB_EVT_OUT_NAK:
        if (g_ep0RxBusy) return LPC_OK;
        g_ep0RxBusy = 1;
        break;
    case USB_EVT_SETUP:
    case USB_EVT_OUT:
        g_ep0RxBusy = 0;
        break;
    }
    return g_Ep0BaseHdlr(hUsb, data, event);
}

/* ----- public (called from elsewhere in the firmware) ----- */

/* The USB ROM driver triggers this on every USB event. The CDC class
 * handlers (registered in vcom_init via USBD_API) dispatch from here. */
void USB_IRQHandler(void)
{
    USBD_API->hw->ISR(g_hUsb);
}

/* Find the interface descriptor of the given class in a descriptor blob.
 * Used by cdc_vcom.c to locate the CDC data interface. */
USB_INTERFACE_DESCRIPTOR *find_IntfDesc(const uint8_t *pDesc, uint32_t intfClass)
{
    USB_COMMON_DESCRIPTOR    *pD;
    USB_INTERFACE_DESCRIPTOR *pIntfDesc = 0;
    uint32_t                  next_desc_adr;

    pD            = (USB_COMMON_DESCRIPTOR *)pDesc;
    next_desc_adr = (uint32_t)pDesc;

    while (pD->bLength) {
        if (pD->bDescriptorType == USB_INTERFACE_DESCRIPTOR_TYPE) {
            pIntfDesc = (USB_INTERFACE_DESCRIPTOR *)pD;
            if (pIntfDesc->bInterfaceClass == intfClass) {
                break;
            }
        }
        pIntfDesc     = 0;
        next_desc_adr = (uint32_t)pD + pD->bLength;
        pD            = (USB_COMMON_DESCRIPTOR *)next_desc_adr;
    }
    return pIntfDesc;
}

/* One-time CDC + USB stack bring-up. Call once at boot, AFTER clock_init().
 * After this returns the device is enumerating and the host will see a
 * virtual COM port. */
void app_usb_cdc_init(void)
{
    USBD_API_INIT_PARAM_T usb_param;
    USB_CORE_DESCS_T      desc;
    ErrorCode_t           ret;
    USB_CORE_CTRL_T      *pCtrl;

    /* Mux USB pins, enable USB peripheral clock. */
    USB_init_pin_clk();

    /* Hook into the USB ROM driver. */
    g_pUsbApi = (const USBD_API_T *)LPC_ROM_API->usbdApiBase;

    memset(&usb_param, 0, sizeof(USBD_API_INIT_PARAM_T));
    usb_param.usb_reg_base = LPC_USB_BASE;
    usb_param.max_num_ep   = 4;
    usb_param.mem_base     = USB_STACK_MEM_BASE;
    usb_param.mem_size     = USB_STACK_MEM_SIZE;

    desc.device_desc    = (uint8_t *)USB_DeviceDescriptor;
    desc.string_desc    = (uint8_t *)USB_StringDescriptor;
#ifdef USE_USB0
    desc.high_speed_desc = USB_HsConfigDescriptor;
    desc.full_speed_desc = USB_FsConfigDescriptor;
    desc.device_qualifier = (uint8_t *)USB_DeviceQualifier;
#else
    desc.high_speed_desc = USB_FsConfigDescriptor;
    desc.full_speed_desc = USB_FsConfigDescriptor;
    desc.device_qualifier = 0;
#endif

    ret = USBD_API->hw->Init(&g_hUsb, &desc, &usb_param);
    if (ret != LPC_OK) {
        return;   /* hardware init failed; caller can detect via !vcom_connected() */
    }

    /* WORKAROUND for artf45032: install EP0 NAK shim. */
    pCtrl            = (USB_CORE_CTRL_T *)g_hUsb;
    g_Ep0BaseHdlr    = pCtrl->ep_event_hdlr[0];
    pCtrl->ep_event_hdlr[0] = EP0_patch;

    /* Register the CDC class handlers. */
    ret = vcom_init(g_hUsb, &desc, &usb_param);
    if (ret != LPC_OK) {
        return;
    }

    /* Connect to the bus and turn on the USB IRQ. */
    NVIC_EnableIRQ(LPC_USB_IRQ);
    USBD_API->hw->Connect(g_hUsb, 1);
}
