#!/usr/bin/env bash
# Runtime ROM injection smoke test for the steam_api.dll proxy.
#
#   inject_test.sh setup    install the proxy + roms/mm1.bin (pristine MM1)
#   inject_test.sh log      tail the proxy log
#   inject_test.sh restore  put the stock steam_api.dll back and remove test files
#
# The pristine MM1 PRG is read from Proteus.exe.orig.bak (SteamStub leaves
# .rdata intact, same file offsets). No game data is copied into the repo.
set -euo pipefail

GAME="${MMLC_GAME:-/var/home/user/.local/share/Steam/steamapps/common/Suzy}"
MOD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${MMLC_WORK:-/tmp/opencode/mmlc-inject}"
BAK="$WORK/steam_api.dll.orig"

mkdir -p "$WORK"

setup() {
    echo "[setup] game=$GAME"
    [ -f "$GAME/Proteus.exe.orig.bak" ] || { echo "missing Proteus.exe.orig.bak"; exit 1; }
    [ -f "$MOD/build/steam_api.dll" ]   || { echo "build the proxy first"; exit 1; }

    # keep one clean copy of the stock steam_api.dll. The proxy may already be
    # installed, in which case the stock lives at steam_api_orig.dll.
    if [ ! -f "$BAK" ]; then
        if [ -f "$GAME/steam_api_orig.dll" ]; then
            cp -f "$GAME/steam_api_orig.dll" "$BAK"
        else
            cp -f "$GAME/steam_api.dll" "$BAK"
        fi
    fi
    sz=$(stat -c%s "$BAK")
    [ "$sz" -gt 200000 ] || { echo "backup $BAK ($sz B) is not the stock DLL"; exit 1; }
    cp -f "$BAK" "$GAME/steam_api_orig.dll"

    # pristine MM1 (headerless) -> roms/mm1.bin
    mkdir -p "$GAME/roms"
    python3 - "$GAME" <<'PY'
import sys, zlib
game = sys.argv[1]
d = open(f"{game}/Proteus.exe.orig.bak", "rb").read()
mm1 = d[0x2AF2B0:0x2AF2B0 + 0x20000]
assert (zlib.crc32(mm1) & 0xFFFFFFFF) == 0x1C47D202, "not pristine MM1"
open(f"{game}/roms/mm1.bin", "wb").write(mm1)
print(f"[setup] wrote roms/mm1.bin ({len(mm1)} bytes, crc 0x1c47d202)")
PY

    cp -f "$MOD/build/steam_api.dll" "$GAME/steam_api.dll"
    cat > "$GAME/mmlc.ini" <<'INI'
# runtime proxy config (test)
nop_overlay=1
scan=1
rom_inject=1
rom_dir=roms
log=1
INI
    rm -f "$GAME/mmlc_proxy.log"
    echo "[setup] installed proxy + mmlc.ini (rom_inject=1)"
    echo "[setup] now launch MMLC and play Mega Man 1"
}

restore() {
    echo "[restore] restoring stock steam_api.dll"
    [ -f "$BAK" ] && cp -f "$BAK" "$GAME/steam_api.dll"
    rm -f "$GAME/steam_api_orig.dll" "$GAME/mmlc.ini"
    echo "[restore] sha256=$(sha256sum "$GAME/steam_api.dll" | cut -d' ' -f1)"
}

rand() {
    local seed="${1:?usage: $0 rand <seed>}"
    mkdir -p "$GAME/roms"
    python3 "$MOD/tools/mmlc_cli.py" randomize mm1 --seed "$seed" \
        --src "$GAME/Proteus.exe.orig.bak" --out "$GAME/roms/mm1.bin" \
        --spoiler "$WORK/spoiler-$seed.json" --apply
}

randpie() {
    local seed="${1:?usage: $0 randpie <seed>}"
    mkdir -p "$GAME/roms"
    python3 "$MOD/tools/mmlc_cli.py" randomize mm1 --seed "$seed" \
        --src "$GAME/Proteus.exe.orig.bak" --out "$GAME/roms/mm1.bin" \
        --pie "$GAME/data.pie" --spoiler "$WORK/spoiler-$seed.json" --apply
}

case "${1:-setup}" in
    setup)   setup ;;
    rand)    rand "${2:-}" ;;
    randpie) randpie "${2:-}" ;;
    restore) restore ;;
    log)     cat "$GAME/mmlc_proxy.log" 2>/dev/null || echo "(no log)" ;;
    *)       echo "usage: $0 {setup|restore|log|rand <seed>|randpie <seed>}"; exit 2 ;;
esac
