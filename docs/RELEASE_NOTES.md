# 20XX v0.6.0 — first official release

20XX is a Mesen2-based launcher/mod host for **Mega Man Legacy Collection 1**
(Steam appid 363440). This first release packages everything up to and including
the MM1 Wily-stage/hard-mode/cursor-tint work.

## What's new in 0.6.0

- **MM1 Wily stage patches:** victory lap (max damage against Wily bosses only),
  boss doors tinted by the robot master whose weapon the boss is weak to, and a
  byte-exact "vanilla Wily" restore.
- **MM1 hard mode and OHKO:** doubled damage received, ~2/3 damage dealt, or
  any hit kills — implemented as pure data patches so they compose with the
  randomizer and keep weakness colors correct.
- **Stage-select cursor tint:** the selected portrait is recolored with its
  weakness colors; the top-buster boss renders green.
- **MM1–MM6 randomizer/export:** weakness, weapon-reward and palette shuffles
  where supported; MM6 reward shuffle; MM2/MM3/MM5 weakness tables are
  byte-for-byte upstream parity.
- **Save-state hotkeys:** LB+Y save, LB+B load, LB+Start reset, LB+X turbo.
- **MMLC CPU SPEED** (ORIGINAL/TURBO) is followed by the Mesen overclock.
- **locale module rename** (`mmlc.locale` → `mmlc.locale_mod`) for frozen builds.

## Downloads

| Platform | File | Notes |
|---|---|---|
| Linux x86_64 | `20XX-v0.6.0-x86_64.AppImage` | Tk GUI + CLI, bundled host binary |
| Windows x64 | `20XX-v0.6.0-win64.zip` | `20XX.exe` (GUI/CLI) |

Both bundles contain **only 20XX code**. They do **not** include ROMs, game
assets, Steamless, the Mesen2 core, or the `steam_api.dll` proxy. You must own
the game and supply the user-built pieces described below.

## Install (Linux)

```bash
chmod +x 20XX-v0.6.0-x86_64.AppImage
./20XX-v0.6.0-x86_64.AppImage          # GUI
./20XX-v0.6.0-x86_64.AppImage version  # CLI
```

The Mesen replacer additionally needs:

1. A Mesen2 core built with `tools/mesen_host/mesen2_capture.patch`
   (`20xx build-core` does this for you), and
2. the `steam_api.dll` proxy, built once from **your own** installed
   `steam_api.dll`:

```bash
# requires mingw-w64 (i686-w64-mingw32-gcc)
STEAM_API=/path/to/Suzy/steam_api.dll ./build/build_proxy.sh
```

Then `20xx mesen --core /path/to/MesenCore.so --start`.

## Install (Windows)

Unzip and run `20XX.exe`. The Windows build is for **offline ROM patching,
seeding, verification and export** (`patch`, `export`, `extract`, `verify`,
`list`, `romhacks`); the Mesen host/AppImage integration is Linux-only in this
release. Export patched `.nes` files and play them in any emulator.

## Legal

Patch-only. No Capcom code, ROMs or assets are distributed. 20XX reads your
own legally-owned install, keeps backups, and loads a Mesen2 core that you
build yourself (Mesen2 is GPLv3 and separate from this MIT project).

Full instructions: <https://github.com/MusouSlime/20xx#readme>
