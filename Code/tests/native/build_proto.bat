@echo off
REM ---------------------------------------------------------------------------
REM build_proto.bat — Windows build for the proto.c parity test.
REM
REM Compiles the firmware's protocol module (Firmware/Backend_LPC4370/src/proto.c)
REM into a Windows DLL that the Python parity test (test_proto_parity.py) loads
REM via ctypes. This lets us validate the firmware's packet parser/builder on a
REM laptop, with NO LPC4370 hardware in the loop.
REM
REM Requirements:
REM   - MinGW-w64 gcc on PATH (e.g. via MSYS2 or w64devkit). Verify with `gcc --version`.
REM
REM Usage from the repo root:
REM   Code\tests\native\build_proto.bat
REM
REM Output:
REM   Code\tests\native\proto.dll
REM ---------------------------------------------------------------------------

setlocal

REM Repo root, derived from this script's location (..\..\..\)
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..\..\..

set SRC=%REPO_ROOT%\Firmware\Backend_LPC4370\src\proto.c
set OUT=%SCRIPT_DIR%proto.dll

where gcc >nul 2>nul
if errorlevel 1 (
    echo [ERROR] gcc not found on PATH. Install MinGW-w64 - e.g. MSYS2 or w64devkit - and retry.
    exit /b 1
)

echo Compiling %SRC%
echo      ----^> %OUT%

gcc -shared -O2 -Wall -Wextra -std=c99 ^
    -o "%OUT%" "%SRC%"

if errorlevel 1 (
    echo [ERROR] Build failed.
    exit /b 1
)

echo [OK] Built %OUT%
endlocal
