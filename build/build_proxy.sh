#!/usr/bin/env bash
# Build the steam_api.dll proxy for MMLC1 (i386).
#
# Requires: i686-w64-mingw32-gcc (Homebrew `mingw-w64`) and python3.
# The proxy re-exports the original steam_api.dll surface and applies runtime
# patches. It never contains Capcom code/data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC="${CC:-i686-w64-mingw32-gcc}"
STEAM_API="${STEAM_API:-/var/home/user/.local/share/Steam/steamapps/common/Suzy/steam_api.dll}"
ORIG_NAME="${ORIG_NAME:-steam_api_orig}"
OUT="${OUT:-$ROOT/build/steam_api.dll}"

if ! command -v "$CC" >/dev/null 2>&1; then
    echo "error: $CC not found (brew install mingw-w64)" >&2
    exit 1
fi
if [[ ! -f "$STEAM_API" ]]; then
    echo "error: real steam_api.dll not found: $STEAM_API" >&2
    echo "       set STEAM_API=/path/to/steam_api.dll" >&2
    exit 1
fi

mkdir -p "$ROOT/build"
DEF="$ROOT/build/${ORIG_NAME}_proxy.def"
python3 "$ROOT/build/gen_def.py" "$STEAM_API" "$ORIG_NAME" > "$DEF"

echo "== building $OUT =="
"$CC" -O2 -s -shared -static -static-libgcc \
    -Wall -Wextra \
    "$ROOT/src/proxy/steam_api_proxy.c" \
    "$ROOT/src/proxy/mesen_bridge.c" \
    "$ROOT/src/proxy/overlay_hook.S" \
    "$DEF" \
    -o "$OUT" \
    -lkernel32 -lws2_32 -lwinmm

echo "OK: $OUT ($(stat -c%s "$OUT") bytes)"
"${OBJDUMP:-i686-w64-mingw32-objdump}" -p "$OUT" | grep -E "DLL Name|Export Address Table" | head -5 || true
