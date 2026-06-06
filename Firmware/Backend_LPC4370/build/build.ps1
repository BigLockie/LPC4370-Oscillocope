# build.ps1 - command line build of the FastOscilloscope LPC4370 firmware.
#
# Produces a RAM image (scope.bin / .hex / .elf) for the USB ROM-DFU
# bootloader. No MCUXpresso IDE required. Run it from anywhere:
#     powershell -File Firmware\Backend_LPC4370\build\build.ps1
#
# Two things must be installed first (see build/BUILD.md for details):
#   1. The Arm GNU bare-metal toolchain (arm-none-eabi-gcc).
#   2. NXP LPCOpen 3.02, for the lpc_chip_43xx chip library.
# Point the build at them with env vars, or let it auto-detect:
#   $env:ARM_TOOLCHAIN_BIN = "C:\path\to\arm-none-eabi\bin"
#   $env:LPCOPEN_DIR       = "C:\path\to\lpcopen_3_02"
#
# Everything else the build needs (linker script, startup, sysinit, the
# HSADC driver that LPCOpen 3.02 omits, and the optional blink) is vendored
# in this build/ folder, so a fresh clone builds once the two installs above
# are in place.

# Do NOT stop on the first stderr line: gcc writes warnings to stderr, which
# this shell surfaces as error records. Gate on $LASTEXITCODE, the real signal.
$ErrorActionPreference = "Continue"

# ---- locate the repo (this script lives in Firmware/Backend_LPC4370/build) ----
$buildDir = $PSScriptRoot
$vendor   = Join-Path $buildDir "vendor"
$fw       = Split-Path $buildDir -Parent          # Firmware\Backend_LPC4370

# ---- find the Arm toolchain ----
function Resolve-ArmGcc {
    if ($env:ARM_TOOLCHAIN_BIN -and (Test-Path (Join-Path $env:ARM_TOOLCHAIN_BIN "arm-none-eabi-gcc.exe"))) {
        return $env:ARM_TOOLCHAIN_BIN
    }
    $onPath = Get-Command arm-none-eabi-gcc -ErrorAction SilentlyContinue
    if ($onPath) { return (Split-Path $onPath.Source -Parent) }
    # Last resort: the path the toolchain installs to under .mcuxpressotools.
    $guess = Get-ChildItem "$env:USERPROFILE\.mcuxpressotools" -Directory -Filter "arm-gnu-toolchain*" -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($guess -and (Test-Path "$($guess.FullName)\bin\arm-none-eabi-gcc.exe")) { return "$($guess.FullName)\bin" }
    return $null
}
$gccdir = Resolve-ArmGcc
if (-not $gccdir) {
    Write-Output "ERROR: arm-none-eabi-gcc not found."
    Write-Output "Install the Arm GNU bare-metal toolchain, then set:"
    Write-Output '  $env:ARM_TOOLCHAIN_BIN = "C:\path\to\arm-none-eabi\bin"'
    exit 1
}
$gcc     = Join-Path $gccdir "arm-none-eabi-gcc.exe"
$objcopy = Join-Path $gccdir "arm-none-eabi-objcopy.exe"
$size    = Join-Path $gccdir "arm-none-eabi-size.exe"

# ---- find LPCOpen 3.02 (we only need the lpc_chip_43xx library from it) ----
function Resolve-Lpcopen {
    $cands = @($env:LPCOPEN_DIR, "$env:USERPROFILE\lpcopen_3_02", "C:\nxp\lpcopen_3_02", "C:\lpcopen_3_02")
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c "lpc_chip_43xx\inc\chip.h"))) { return $c }
    }
    return $null
}
$lpc = Resolve-Lpcopen
if (-not $lpc) {
    Write-Output "ERROR: LPCOpen 3.02 not found (need lpc_chip_43xx\inc\chip.h)."
    Write-Output "Download LPCOpen 3.02 for the LPC4370, unzip it, then set:"
    Write-Output '  $env:LPCOPEN_DIR = "C:\path\to\lpcopen_3_02"'
    exit 1
}
Write-Output "toolchain: $gccdir"
Write-Output "lpcopen:   $lpc"

# ---- output dirs (everything here is gitignored by extension) ----
$out = Join-Path $buildDir "out"
$obj = Join-Path $out "obj"
$logf = Join-Path $out "build.log"
Remove-Item $obj -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $obj | Out-Null
Set-Content -Path $logf -Value "build started" -Encoding utf8

# ---- include search paths ----
$inc = @(
  "$fw\inc", "$fw\src", "$fw\src\lpcopen_cdc",
  "$lpc\lpc_chip_43xx\inc", "$lpc\lpc_chip_43xx\inc\config_43xx",
  "$lpc\lpc_chip_43xx\inc\usbd_rom"
)
$incFlags = $inc | ForEach-Object { "-I$_" }

# ---- preprocessor defines ----
# Full firmware (afe_init + adchs_init run, real DMA HSADC capture). Add
# -DMILESTONE1_BLINK for the NeoPixel blink bring-up image instead.
$defs = @("-DCORE_M4","-D__USE_LPCOPEN","-D__CODE_RED","-DNO_BOARD_LIB")
# Proven-good 68 MHz ADC clock (clock_init default). The 80 MHz route is broken
# (IDIVB sources the 12 MHz IRC, giving 2 MHz); fix that separately.
$defs += "-DADCHS_CLOCK_68MHZ"

$cpu  = @("-mcpu=cortex-m4","-mthumb","-mfloat-abi=hard","-mfpu=fpv4-sp-d16")
$cflags = $cpu + @("-Os","-g3","-ffunction-sections","-fdata-sections","-fno-common","-Wall","-std=gnu11") + $defs + $incFlags

# GCC 14 promotes several old-C constructs to hard errors. LPCOpen 3.02 is
# 2015-era code that trips these, so relax them to warnings for the vendored
# sources only. Our own firmware stays strict.
$relax = @("-Wno-error=incompatible-pointer-types","-Wno-error=implicit-function-declaration",
           "-Wno-error=int-conversion","-Wno-error=implicit-int","-Wno-error=discarded-qualifiers")

# ---- source lists ----
$fwSrcs = @()   # firmware we authored: strict warnings
$fwSrcs += (Get-ChildItem "$fw\src" -Filter *.c | ForEach-Object FullName)
$fwSrcs += (Get-ChildItem "$fw\src\lpcopen_cdc" -Filter *.c | ForEach-Object FullName)
$fwSrcs += "$buildDir\milestone1_blink.c"         # optional Milestone-1 WS2812 blink
$libSrcs = @()  # LPCOpen library + vendored glue: relaxed warnings
$libSrcs += (Get-ChildItem "$lpc\lpc_chip_43xx\src" -Filter *.c | ForEach-Object FullName)
$libSrcs += "$vendor\cr_startup_lpc43xx.c"        # LPCXpresso startup (section-table walk)
$libSrcs += "$vendor\sysinit.c"                   # SystemInit + OscRateIn (NO_BOARD_LIB)
$libSrcs += "$vendor\hsadc_18xx_43xx.c"           # HSADC driver (missing from LPCOpen 3.02)

# ---- compile ----
$objs = @(); $i = 0; $fail = $false
foreach ($grp in @(@{ s = $fwSrcs; f = $cflags }, @{ s = $libSrcs; f = ($cflags + $relax) })) {
    foreach ($s in $grp.s) {
        $o = Join-Path $obj ("{0:D3}_{1}.o" -f $i, [System.IO.Path]::GetFileNameWithoutExtension($s))
        $cout = & $gcc $grp.f -c $s -o $o 2>&1
        "### $s (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $logf
        $cout | Out-File -Append -Encoding utf8 $logf
        if ($LASTEXITCODE -ne 0) { Write-Output ">>> COMPILE FAILED: $s"; $fail = $true; break }
        $objs += $o; $i++
    }
    if ($fail) { break }
}
if ($fail) { Write-Output "see $logf"; exit 1 }
Write-Output ("Compiled {0} translation units OK." -f $objs.Count)

# ---- link ----
$ld = "$buildDir\lpc4370_ram.ld"
$ldflags = $cpu + @("-T$ld","-nostartfiles","--specs=nano.specs","--specs=nosys.specs",
                    "-Wl,--gc-sections","-Wl,-Map=$out\scope.map","-Wl,--print-memory-usage")
$lout = & $gcc $ldflags $objs -o "$out\scope.elf" 2>&1
"### LINK (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $logf
$lout | Out-File -Append -Encoding utf8 $logf
$lout | Where-Object { $_ -match "Region|RamLoc|RamAHB|undefined|error" }
if ($LASTEXITCODE -ne 0) { Write-Output ">>> LINK FAILED (see $logf)"; exit 1 }

& $objcopy -O binary "$out\scope.elf" "$out\scope.bin"
& $objcopy -O ihex   "$out\scope.elf" "$out\scope.hex"

# Patch the LPC43xx "valid user code" checksum at offset 0x1C of the .bin.
# word[7] = two's complement of sum(word[0..6]), so the first 8 vectors sum to 0.
# The boot ROM checks this before executing a downloaded image.
$bin = "$out\scope.bin"
$bytes = [System.IO.File]::ReadAllBytes($bin)
$sum = [uint64]0
for ($k = 0; $k -lt 7; $k++) { $sum += [BitConverter]::ToUInt32($bytes, $k * 4) }
$chk = [uint32](((0x100000000 - ($sum -band 0xFFFFFFFF))) -band 0xFFFFFFFF)
[Array]::Copy([BitConverter]::GetBytes($chk), 0, $bytes, 28, 4)
[System.IO.File]::WriteAllBytes($bin, $bytes)
Write-Output ("Patched boot checksum word[7] = 0x{0:X8} at offset 0x1C" -f $chk)
Write-Output "=== size ==="
& $size "$out\scope.elf"
Write-Output "BUILD OK -> $out\scope.elf / scope.bin / scope.hex"
