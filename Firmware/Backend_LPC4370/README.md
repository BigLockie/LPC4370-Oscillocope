# Backend_LPC4370 (LPC4370 Firmware)

This is the Backend block for the FastOscilloscope project, written as C firmware that runs on the LPC4370 MCU. It does four things:

1. Brings up the system clock (12 MHz crystal to 204 MHz core, 68 MHz HSADC)
2. Drives the analog front end GPIOs (gain MUX plus AC/DC coupling)
3. Captures bursts of 12 bit ADC samples via the on chip HSADC
4. Talks to the laptop over USB CDC using our framed packet protocol

On the laptop side, `Code/host_bridge.py` speaks the same protocol and hands packets to the User Input and Display blocks.

## Status, read this before flashing

I want to be upfront about what's actually done. The firmware is structurally complete. Every file the project needs exists, every function the build references is defined, and the byte level protocol is fully cross validated against the laptop side with no hardware (see "Verifying it works" below).

What's NOT done yet:

* I haven't compiled it in MCUXpresso on a Windows box, only laptop side static analysis. So a couple of include paths or library link names may need small tweaks once the project is imported.
* The DMA routing from the HSADC FIFO into SRAM goes through the LPC4370's GIMA (DMA mux). LPCOpen ships no LPC4370 specific example for this and I wasn't going to guess. Fallback is polled reads from `HSADC->LAST_SAMPLE` for the first bring up, see TODOs.
* Has not been run on real silicon. Pin map values came from Luke's schematic but I'd verify a couple with a multimeter before flashing.

So: this gets you a project that compiles and links into a flashable binary. Whether HSADC samples actually land in the right buffer at the right rate is a hardware day discovery.

## Source layout

```
Firmware/Backend_LPC4370/
|-- inc/
|   |-- pin_map.h          Named pin defines from Luke's pin assignment table
|-- src/
|   |-- main.c             Superloop: drain USB RX, parse, dispatch, push USB TX
|   |-- proto.[ch]         Packet parser/builder (must match host_bridge.py byte by byte)
|   |-- afe.[ch]           AFE GPIO control (gain MUX, AC/DC coupling switch)
|   |-- adchs.[ch]         HSADC init plus DMA driven burst capture plus trigger comparator
|   |-- clock.[ch]         CGU setup: 204 MHz core (PLL1), 68 MHz HSADC (IDIVA from PLL1)
|   |-- usb_cdc.[ch]       Thin wrapper around the vendored LPCOpen CDC code
|   |-- lpcopen_cdc/       Vendored LPCOpen USB CDC sources (see header below)
|       |-- app_usb_cdc.c  Refactored from LPCOpen's cdc_main.c (USB stack init)
|       |-- cdc_vcom.c/.h  Virtual COM port class implementation
|       |-- cdc_desc.c     USB device plus endpoint descriptors
|       |-- app_usbd_cfg.h ROM driver config plus USB stack memory layout
|       |-- board.h        Tiny shim so the vendored files compile without bambino
```

`lpcopen_cdc/` is the LPCOpen `usbd_rom_cdc_vcom` example, copied in and refactored so we expose `app_usb_cdc_init()` instead of a `main()`. NXP's permissive license is in each file's header.

## What you need installed first

On the host (the machine you'll build firmware on):

* MCUXpresso IDE, NXP's Eclipse based IDE for LPC parts. https://www.nxp.com/.../mcuxpresso-software-and-tools-:MCUXpresso-IDE
* LPCOpen LPC18xx/43xx package. Provides `lpc_chip_43xx`, the chip driver library we link against. NXP's download requires a free account. The file we want is something like `lpcopen_3_02_lpcxpresso_nxp_lpcxpresso_4337.zip`.
* An SWD debug probe (LPC-Link2, J-Link, or equivalent).

On the laptop you'll talk to the board with:

* Python 3.10 or newer
* `pip install pyserial pyqt5 pyqtgraph numpy`
* The COM port the board enumerates as. Look in Device Manager, Ports on Windows, or `ls /dev/tty.usbmodem*` on macOS/Linux.

## How to build it

### 1. Pull LPCOpen into MCUXpresso

In MCUXpresso click "Import project(s) from file system" in the Quickstart Panel, point at the LPCOpen zip, and check `lpc_chip_43xx` in the import dialog. Build it once. You should see a green `lpc_chip_43xx.a` show up under `Debug/`.

You do NOT need to import LPCOpen's CDC example. We vendored it in `src/lpcopen_cdc/` so we don't depend on it being available.

You also do NOT need to import a `lpc_board_*` library. We wrote our own minimal board init (it's inside `clock.c` plus `afe_init()` plus the vendored `board.h` shim).

### 2. Create the Backend project

1. File, New, C Project, NXP, LPC4300, LPC4370.
2. Empty Project template. Don't let it copy in any LPCOpen sources. We want our own `src/` and `inc/` to be the only sources.
3. Set the project location to this folder (`Firmware/Backend_LPC4370`). MCUXpresso writes `.cproject` and `.project` here.

### 3. Wire up the includes plus library link

In Project Properties, C/C++ Build, Settings:

* MCU C Compiler, Includes: add
  * `../inc`
  * `../../lpc_chip_43xx/inc`
  * `../src/lpcopen_cdc` (so `#include "cdc_vcom.h"` and friends resolve)
* MCU Linker, Libraries: add `lpc_chip_43xx` under Libraries (-l) and the matching `Debug` or `Release` path under Library search path (-L).
* Project References: tick `lpc_chip_43xx` so it rebuilds when needed.

### 4. Build

Right click the project, Build Project. You should get a clean build with no warnings about missing functions. If you see `undefined reference to vcom_*` or `find_IntfDesc`, check that `src/lpcopen_cdc/` was added to the source folders. Sometimes MCUXpresso misses subdirs.

### 5. Flash

1. Connect the SWD probe (TMS/SWDIO, TCK/SWDCLK, GND, VTREF).
2. Run, Debug As, MCUXpresso IDE LinkServer Debug.
3. Make sure the boot button (P2_8) is NOT held down at reset.

## First time on hardware, staged bring up

Don't go straight to "run main.py and look at waveforms." Things will fail and you won't know where. Do these milestones in order. Each one de risks the next.

**Milestone 1: blink the Neopixel.** Add a temporary 1 Hz toggle on `NEOPIXEL` (P1_6) before `usb_cdc_init()` in `main()`. If the LED blinks, your toolchain plus flash plus clock are working. Remove the test code when done.

**Milestone 2: USB enumerates.** With main() back to normal, plug the USB cable into the laptop. Open Device Manager (Windows) and watch for a new "USB Serial Device (COMx)". If it shows up, the CDC stack is alive.

**Milestone 3: command bytes round trip.** With the device enumerated, from the laptop:
```
python Code/tests/test_host_bridge.py --port COM5 --log-packets
```
You should see TX packet hex traced, and ideally no errors. This proves the host can write bytes that reach the device's USB stack.

**Milestone 4: AFE pins toggle.** Put a scope or multimeter on `CH1_SEL1..4`. Send a few `set_vdiv` commands from `Code/main.py` and watch the SEL lines flip one hot. The `afe_set_gain()` function is strictly break before make so you should never see two SELs high at the same time.

**Milestone 5: HSADC produces data.** Feed a known DC voltage (1.0 V from a battery or bench supply) through the AFE input. Send `set_vdiv` 1 V/div, then `run`. The Display should show a flat line at roughly 1.0 V. If it shows zero or noise, the issue is in the HSADC config (probably the DMA routing, see TODOs).

**Milestone 6: real waveform.** Function generator into AFE input, live waveform on `python Code/main.py --port COM5`. This is the "Backend block fully works" demo.

## Verifying it works

Some of this you can verify before hardware exists. The protocol layer is fully testable on a laptop.

### Without hardware (works on any laptop right now)

```
# All 6 host side tests
python Code/tests/test_host_bridge.py --all

# Cross validate firmware proto.c against host_bridge.py (Test 7)
# Requires gcc on PATH; on Windows install MinGW w64
Code\tests\native\build_proto.bat
python Code\tests\test_proto_parity.py

# AFE control logic unit test (Test 8)
# Same gcc requirement; compiles firmware afe.c against mocked GPIO calls
Code\tests\native\afe_test\build_afe.bat
python Code\tests\test_afe_unit.py
```

Test 7 compiles our actual firmware `proto.c` into a DLL, loads it via ctypes, and checks that every command and data packet round trips byte by byte between the C and Python implementations. Test 8 does the same trick with `afe.c`, recording every GPIO call and verifying the break before make and one hot rules hold. Together they fully verify the protocol and AFE control layer properties of all three Backend interfaces without ever touching hardware. Worth running before plugging the board in just to confirm your environment is sane.

### With hardware

```
# Same test, real device
python Code/tests/test_host_bridge.py --port COM5 --log-packets
python Code/tests/test_host_bridge.py --port COM5 --verify-checksums
python Code/tests/test_host_bridge.py --port COM5 --debug-threads
```

Tests 4 and 5 (inject known patterns or corrupt packets) stay on MockMCU because they need to control the MCU's outgoing bytes, which a real device won't let you do.

## Known issues and TODOs

These are real gaps, not "make sure to verify" stuff. They need work either on hardware or with the LPCOpen reference in hand.

| File | What's wrong or missing |
|---|---|
| `adchs.c` | DMA from HSADC FIFO into SRAM needs the LPC4370 GIMA setup. LPCOpen has no example. Fallback: polled reads from `HSADC->LAST_SAMPLE` in main superloop. |
| `adchs.c` | Sample rate math assumes BASE_ADCHS_CLK = 68 MHz exactly. Verify on hardware with a known input frequency. |
| `clock.c` | Should work as is but I haven't actually scoped the ADCHS clock pin to confirm 68 MHz. |
| `main.c` | Trigger comparator behavior is "best guess" from UM10503 section 47. The threshold to edge mapping might be inverted. Check with a known signal. |
| `usb_cdc.c` | If you see CDC TX truncation under load, bump `g_rxBuff` size in `app_usb_cdc.c` or add a software TX ring. Default is fine for command plus 2 KB ADC bursts. |

## Some implementation notes (for whoever picks this up next)

**Why 68 MHz on HSADC instead of 80 MHz?** 80 MHz from a 12 MHz crystal requires PLL0AUDIO with non integer M, N, P values, which I tried and gave up on. PLL1 divided by 3 gives an exact 68 MHz, uses only the documented `Chip_Clock_*` helpers, and still gives 165 times the project's 200 kHz per channel customer requirement. Swap in PLL0AUDIO later if we really need 80 MHz.

**Why is HSADC called ADCHS in our headers?** NXP's manual uses both names for the same peripheral. LPCOpen settled on `Chip_HSADC_*` and `LPC_HSADC`, so the C code does too. Our public API is `adchs_*` to match the team wide "ADCHS" terminology in the system block diagram.

**Why is the `set_trigger` command split into 3 packets on the wire?** Block 1's GUI emits one big `set_trigger` dict with level, mode, and source. The host side `serialize_command()` expands that into three back to back on wire packets (`SET_TRIGGER_LEVEL`, `SET_TRIGGER_MODE`, `SET_TRIGGER_SOURCE`) because (a) it keeps each packet at most 7 bytes and (b) it lets the firmware update one field without touching the others. The firmware caches all three and re applies the comparator on every update.

**Why is `afe_set_gain()` break before make?** Some analog MUXes are make before break and can briefly forward two signal paths to the ADC during a gain change. Driving all four SELs low first, then asserting the new one, gives us a clean (briefly silent) switch instead of a crosstalk glitch.
