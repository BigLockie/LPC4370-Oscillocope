/* lpc4370_ram.ld
 * RAM-execution linker script for the LPC4370 (Cortex-M4F).
 *
 * Links the whole image into local SRAM bank 0 (0x10000000, 128 KB) so it can
 * be downloaded over the LPC4370 USB0 ROM-DFU bootloader and executed
 * (volatile) for hardware bring-up -- no SPIFI programming, no SWD probe.
 *
 * Memory map taken verbatim from NXP LinkServer's preinstalled_devices.json
 * (device "LPC4370").  Uses the LPCXpresso/LPCOpen "global section table"
 * convention required by cr_startup_lpc43xx.c
 * (__data_section_table / __bss_section_table / _vStackTop).
 */

MEMORY {
  RamLoc128    (rwx) : ORIGIN = 0x10000000, LENGTH = 0x20000  /* 128 KB local SRAM */
  RamLoc72     (rwx) : ORIGIN = 0x10080000, LENGTH = 0x12000  /*  72 KB local SRAM */
  RamAHB32     (rwx) : ORIGIN = 0x20000000, LENGTH = 0x8000   /*  32 KB AHB SRAM   */
  RamAHB16     (rwx) : ORIGIN = 0x20008000, LENGTH = 0x4000   /*  16 KB AHB SRAM   */
  RamAHB_ETB16 (rwx) : ORIGIN = 0x2000C000, LENGTH = 0x4000   /*  16 KB AHB SRAM   */
}

ENTRY(ResetISR)

/* Initial stack pointer = top of the 128 KB bank (vector[0]). */
_vStackTop = ORIGIN(RamLoc128) + LENGTH(RamLoc128);

SECTIONS {
    .text : ALIGN(4) {
        FILL(0xff)
        __vectors_start__ = ABSOLUTE(.);
        KEEP(*(.isr_vector))      /* must be first: SP + reset vector at 0x10000000 */
        *(.after_vectors*)
        *(.text*)
        *(.rodata .rodata.* .constdata .constdata.*)
        *(.glue_7) *(.glue_7t)
        . = ALIGN(4);
    } > RamLoc128

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > RamLoc128
    .ARM.exidx : ALIGN(4) {
        __exidx_start = .;
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        __exidx_end = .;
    } > RamLoc128

    .init_array : ALIGN(4) {
        __init_array_start = .;
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        __init_array_end = .;
    } > RamLoc128
    .fini_array : ALIGN(4) {
        __fini_array_start = .;
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array))
        __fini_array_end = .;
    } > RamLoc128

    /* Global Section Table -- ResetISR() walks this to init .data/.bss. */
    .section_table : ALIGN(4) {
        __section_table_start = .;
        __data_section_table = .;
        LONG(LOADADDR(.data));
        LONG(ADDR(.data));
        LONG(SIZEOF(.data));
        __data_section_table_end = .;
        __bss_section_table = .;
        LONG(ADDR(.bss));
        LONG(SIZEOF(.bss));
        __bss_section_table_end = .;
        __section_table_end = .;
    } > RamLoc128

    /* RAM image: load address == exec address, so the .data copy is a no-op. */
    .data : ALIGN(4) {
        FILL(0xff)
        _data = .;
        *(vtable)
        *(.ramfunc*)
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RamLoc128

    .bss (NOLOAD) : ALIGN(4) {
        _bss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(8);
        _ebss = .;
        PROVIDE(end = .);     /* newlib _sbrk heap base (nosys.specs) */
        PROVIDE(_end = .);
    } > RamLoc128

    /* Boot ROM doesn't check this for a RAM/DFU image; resolve the weak ref. */
    PROVIDE(__valid_user_code_checksum = 0);
}
