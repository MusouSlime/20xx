# 20XX

A **Mesen2-based launcher and mod host** for **Mega Man Legacy Collection 1**
(Steam appid `363440`, install folder `Suzy`, main binary `Proteus.exe`).

It runs the collection's six NES games — and the six JP *Rockman* ROMs — through
the accurate, standard **Mesen2** core instead of Digital Eclipse's in-engine
"Eclipse" emulator, and adds offline ROM patching (randomizer, palette shuffle,
ROM hacks) from a small terminal/GUI launcher.

> **No ROMs, no game assets, and no emulator core are included in this repo.**
> 20XX only modifies *your* legally-owned install, at runtime, and loads a
> Mesen2 core that *you* build. Nothing is redistributed.

## Why

The collection's built-in randomizer writes only data tables, yet the game
resets when a Robot Master is defeated — the remaining suspects are all
Eclipse-core behaviors (its native `0xDF` call-out opcode, a custom mapper
register, and engine-side integrity checks). A standard core executes the ROM
bytes verbatim, which is exactly what a randomizer expects.

## Features

- **Runs all 12 ROMs through Mesen2**, synced frame-by-frame into the engine's
  own 1024×960 screen buffer, with audio bridged out and controller input
  forwarded both ways.
- **Offline randomizer (MM1 first):** boss-weakness shuffle, weapon-reward
  shuffle, palette shuffle, and the weakness visualizer — deterministic per
  seed and byte-for-byte reproducible.
- **Drop-in ROM-hack folder:** put `.ips`/`.bps` patches under
  `romhacks/<game>/` and 20XX applies them when it patches the ROM.
- **Launcher:** a terminal UI (`20xx`), a Tk GUI (`20xx gui`), and a Steam
  pre-launch hook (`20xx prelaunch %command%`).
- **Patch-only and reversible:** it operates on your install; the game files are
  backed up before any on-disk change.

## Requirements

- Steam **Mega Man Legacy Collection 1** installed (appid 363440).
- **Python 3.9+** (stdlib only; `tkinter` for the GUI).
- For building the Mesen2 core: `git`, `make`, a C++ toolchain, and Mesen2's
  Linux build dependencies.
- For building the proxy on Linux: `i686-w64-mingw32-gcc` (mingw-w64).

## Build the Mesen2 core (user-supplied, GPLv3)

The public Mesen2 releases do **not** export the hooks this mod needs
(`GetVideoBuffer`, `EnableCaptureRenderer`, `ConfigureNesInput`,
`SetControllerState`, audio capture, …). Build it from source with the patch in
`tools/mesen_host/mesen2_capture.patch`:

```bash
git clone --depth 1 https://github.com/SourMesen/Mesen2.git
cd Mesen2
git apply /path/to/mmlc-mod/tools/mesen_host/mesen2_capture.patch
make core STATICLINK=false -j"$(nproc)"
# -> InteropDLL/obj.linux-x64/MesenCore.so
```

Or let 20XX do it (clone + patch + build), then point `mesen_core` at it:

```bash
20xx build-core
```

On Windows, apply the same patch and build `InteropDLL` (x64) from
`Mesen.sln` to get `MesenCore.dll`; then run `20xx mesen --core ...\MesenCore.dll`.

## Install / use

```bash
# point 20XX at a Mesen core (or run `20xx build-core` first)
20xx mesen --core /path/to/MesenCore.so

# launch the GUI wizard (install folder, setup, options, launch)
20xx gui
# or double-click the launcher:  ./20xx-gui.sh  (Linux)  20xx-gui.bat  (Windows)

# or drive it from the terminal
20xx extract                 # write roms/*.nes from your own Proteus.exe
20xx patch mm1 --seed hello  # randomize MM1 (offline)
20xx play mm1                # prepare + start the Mesen host + launch MMLC
```

Steam pre-launch hook (Steam → Properties → Launch Options):

```
/path/to/mmlc-mod/tools/20xx/20xx prelaunch %command%
```

This prepares the ROMs, starts the Mesen host, then hands off to the game.

## ROM hacks

```
romhacks/
  mm1/  mm2/  mm3/  mm4/  mm5/  mm6/    # US Mega Man
  rk1/  rk2/  rk3/  rk4/  rk5/  rk6/    # JP Rockman
```

Drop `.ips`/`.bps` patches into the game's folder; each game only looks in its
own folder. Multiple patches apply in filename order (`01-`, `02-`, …). An
optional `<patch>.ips.adjust` sidecar (e.g. `-16`) shifts IPS offsets for
headerless patches.

```bash
20xx romhacks mm1            # list what 20XX finds
20xx patch mm1               # apply (plus randomizer when seeded)
```

## Layout

```
tools/20xx/        the launcher (CLI + Tk GUI + Steam prelaunch)
tools/mesen_host/  x64 Linux host + mesen2_capture.patch
tools/mmlc/        toolkit: PE reader, ROM table, randomizer, IPS/BPS, data.pie
src/proxy/         steam_api.dll proxy (video/input/audio bridge + hooks)
docs/              reverse-engineering notes and status
test/              unit tests
```

## Legal

Patch-only. This project contains **no** Capcom code, ROMs, or assets, and does
not bundle the Mesen2 core. It modifies your own install in memory at runtime,
keeps backups, and is reversible. Mesen2 is GPLv3 and is loaded from a
user-built core.

## License

MIT (the mod code in this repo). Mesen2 is GPLv3 and remains separate.
