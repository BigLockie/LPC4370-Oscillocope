#!/usr/bin/env bash
# Build the AFE unit-test shared library (Unix).
# See build_afe.bat for the Windows equivalent.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

AFE_SRC="$REPO_ROOT/Firmware/Backend_LPC4370/src/afe.c"
STUB_SRC="$SCRIPT_DIR/lpcopen_stub.c"
PIN_MAP_DIR="$REPO_ROOT/Firmware/Backend_LPC4370/inc"

case "$(uname -s)" in
    Darwin)  OUT="$SCRIPT_DIR/afe.dylib" ;;
    Linux)   OUT="$SCRIPT_DIR/afe.so"    ;;
    *)       OUT="$SCRIPT_DIR/afe.so"    ;;
esac

CC="${CC:-cc}"
echo "Compiling afe.c + lpcopen_stub.c → $OUT"
"$CC" -shared -fPIC -O2 -Wall -Wextra -std=c99 \
    -I "$SCRIPT_DIR" \
    -I "$PIN_MAP_DIR" \
    -o "$OUT" \
    "$AFE_SRC" "$STUB_SRC"
echo "[OK] Built $OUT"
