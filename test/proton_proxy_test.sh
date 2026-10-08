#!/usr/bin/env bash
# Live proxy smoke test v2: interval screenshots + window/process probing.
set -uo pipefail

GAME=/var/home/user/.local/share/Steam/steamapps/common/Suzy
MOD=/var/home/user/Projects/mmlc-mod
WORK=/tmp/opencode/mmlc-proxy-test
mkdir -p "$WORK"

restore() {
    echo "[restore] reverting steam_api.dll"
    [ -f "$WORK/steam_api.dll.orig" ] && cp -f "$WORK/steam_api.dll.orig" "$GAME/steam_api.dll"
    rm -f "$GAME/steam_api_orig.dll" "$GAME/mmlc.ini"
    echo "[restore] sha256=$(sha256sum "$GAME/steam_api.dll" | cut -d' ' -f1)"
}
trap restore EXIT

cp -f "$GAME/steam_api.dll" "$WORK/steam_api.dll.orig"
cp -f "$GAME/steam_api.dll" "$GAME/steam_api_orig.dll"
cp -f "$MOD/build/steam_api.dll" "$GAME/steam_api.dll"
cp -f "$MOD/config/mmlc.example.ini" "$GAME/mmlc.ini"
rm -f "$GAME/mmlc_proxy.log"

echo "[launch] $(date +%T)"
steam -applaunch 363440 >"$WORK/launch.out" 2>&1 &

for i in $(seq 1 10); do
    sleep 8
    alive=$(pgrep -c -f "Proteus.exe" 2>/dev/null || echo 0)
    wins=$(DISPLAY=:0 xdotool search --name "." 2>/dev/null | wc -l)
    echo "t=$((i*8))s alive=$alive xwin=$wins"
    DISPLAY=:0 timeout 15 spectacle -b -n -o "$WORK/shot_$i.png" >/dev/null 2>&1 \
        && echo "   shot_$i.png $(stat -c%s "$WORK/shot_$i.png" 2>/dev/null)"
    if [ "$i" -ge 2 ]; then
        echo "   windows:"
        DISPLAY=:0 xdotool search --name "." 2>/dev/null | while read -r id; do
            echo "     $id $(DISPLAY=:0 xdotool getwindowname "$id" 2>/dev/null)"
        done | sort -u | head -15
    fi
done

echo "[proxy log]"
cat "$GAME/mmlc_proxy.log" 2>/dev/null || echo "(none)"
echo "[ps]"
pgrep -af "Proteus" | grep -v pgrep || echo "(none)"

pkill -f "Proteus.exe" 2>/dev/null || true
sleep 4
echo "[done] $(date +%T)"
