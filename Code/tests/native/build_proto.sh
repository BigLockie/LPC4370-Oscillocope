#!/usr/bin/env bash
# build_proto.sh — Unix sibling of build_proto.bat.
#
# Compiles the firmware's proto.c into a shared library that the parity test
# can load via ctypes. Useful for developers on macOS / Linux who want to
# sanity-check the test before pushing to a Windows machine for the formal run.
#
# Usage from the repo root:
#   bash Code/tests/native/build_proto.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SRC="$REPO_ROOT/Firmware/Backend_LPC4370/src/proto.c"

case "$(uname -s)" in
    Darwin)  OUT="$SCRIPT_DIR/proto.dylib" ;;
    Linux)   OUT="$SCRIPT_DIR/proto.so"    ;;
    *)       OUT="$SCRIPT_DIR/proto.so"    ;;
esac

CC="${CC:-cc}"
echo "Compiling $SRC"
echo "      ----> $OUT"

"$CC" -shared -fPIC -O2 -Wall -Wextra -std=c99 -o "$OUT" "$SRC"

echo "[OK] Built $OUT"
