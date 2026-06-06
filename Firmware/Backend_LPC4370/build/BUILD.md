# Building and flashing the firmware

This folder has everything needed to build the LPC4370 firmware from the command
line and flash it over USB, except two things you install once (an Arm compiler
and the NXP chip library). No MCUXpresso IDE required.

## What is in this folder

| File | Role |
|---|---|
| `build.ps1` | The build script. Self locating, run it from anywhere. |
| `lpc4370_ram.ld` | Linker script. Puts the image in local SRAM so it runs from a USB DFU download (volatile, no flash programming). |
| `milestone1_blink.c` | Optional NeoPixel blink image for toolchain bring up (only built into the firmware when you define `MILESTONE1_BLINK`). |
| `vendor/hsadc_18xx_43xx.c` | The HSADC driver. LPCOpen 3.02 ships without it, so this copy comes from LPCOpen 2.12. |
| `vendor/cr_startup_lpc43xx.c` | Reset handler and the section table walk the linker script expects. |
| `vendor/sysinit.c` | `SystemInit` plus `OscRateIn` for the `NO_BOARD_LIB` build. |
| `out/` | Build outputs (`scope.elf` / `.bin` / `.hex` / `.map` / `build.log`). Git ignores this. |

## Install once

1. **Arm GNU bare metal toolchain** (`arm-none-eabi-gcc`). Get the "arm-none-eabi"
   build from the Arm Developer site, or reuse the one the MCUXpresso installer
   drops under `%USERPROFILE%\.mcuxpressotools`. The build auto detects that
   location, or set it yourself:
   ```powershell
   $env:ARM_TOOLCHAIN_BIN = "C:\path\to\arm-none-eabi\bin"
   ```
2. **NXP LPCOpen 3.02** (for the `lpc_chip_43xx` chip library). Download LPCOpen
   3.02 for the LPC4370, unzip it, and either drop it at
   `%USERPROFILE%\lpcopen_3_02` (auto detected) or set:
   ```powershell
   $env:LPCOPEN_DIR = "C:\path\to\lpcopen_3_02"
   ```
3. **NXP LPCScrypt** (only needed to flash a board, not to build). It provides
   `image_manager.exe`, `dfu-util.exe`, and the WinUSB driver that lets the PC
   talk to the board in DFU mode. Typical path:
   `C:\nxp\LPCScrypt_2.1.4_101\bin`.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File Firmware\Backend_LPC4370\build\build.ps1
```

The result is `Firmware\Backend_LPC4370\build\out\scope.bin` (plus `.hex` and
`.elf`). A good build ends with `BUILD OK`.

## Flash (USB C, no probe)

The board is flashless and runs a volatile RAM image, so this is how you load new
firmware. Using the LPCScrypt tools (adjust the path to your install):

1. Put the board in DFU mode: hold BOOT (P2_8) and tap RESET. It shows up as
   VID `0x1FC9` PID `0x000C`.
2. Wrap the image with NXP's boot header, then download:
   ```powershell
   $bin = "C:\nxp\LPCScrypt_2.1.4_101\bin"
   $out = "Firmware\Backend_LPC4370\build\out"
   & "$bin\image_manager.exe" -i "$out\scope.bin" -o "$out\scope.bin.hdr" --bin
   & "$bin\dfu-util.exe" -d 0x1fc9:c -c 0 -i 0 -t 2048 -R -D "$out\scope.bin.hdr"
   ```
   `dfu-util` prints "Invalid DFU suffix signature" at the end. That is a harmless
   warning. "Download done" above it is the real result.
3. After the `-R` reset the board re-enumerates as "LPC USB VCom Port" on a COM
   port (COM4 is typical). That is the port you pass to the host GUI.

Every unplug, reset, or power cycle wipes the RAM image, so re-flash after each.

## Editing the AFE (gain and coupling work)

The control driver is `../src/afe.c` (`afe_set_gain`, `afe_set_coupling_dc`). The
command that reaches it is dispatched in `../src/main.c`. To add the AC/DC
coupling command end to end, follow the steps in the top level
[`README.md`](../../../README.md) under "Continuing development".
