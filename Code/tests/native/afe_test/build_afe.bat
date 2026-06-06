@echo off
REM Build the AFE unit-test shared library (Windows).
REM Requires MinGW-w64 gcc on PATH (same prereq as build_proto.bat).
setlocal

set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..\..\..\..

set AFE_SRC=%REPO_ROOT%\Firmware\Backend_LPC4370\src\afe.c
set STUB_SRC=%SCRIPT_DIR%lpcopen_stub.c
set PIN_MAP_DIR=%REPO_ROOT%\Firmware\Backend_LPC4370\inc
set OUT=%SCRIPT_DIR%afe.dll

where gcc >nul 2>nul
if errorlevel 1 (
    echo [ERROR] gcc not found on PATH. Install MinGW-w64 and retry.
    exit /b 1
)

echo Compiling afe.c + lpcopen_stub.c -^> %OUT%
gcc -shared -O2 -Wall -Wextra -std=c99 ^
    -I "%SCRIPT_DIR%." ^
    -I "%PIN_MAP_DIR%" ^
    -o "%OUT%" ^
    "%AFE_SRC%" "%STUB_SRC%"

if errorlevel 1 (
    echo [ERROR] Build failed.
    exit /b 1
)
echo [OK] Built %OUT%
endlocal
