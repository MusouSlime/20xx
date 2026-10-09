# Phase 6 (Mesen) — handoff / status (2026-10-07, rev 3)

See `docs/MESEN.md` for the architecture and milestone plan, `docs/RE_FACTS.md`
for static facts. This file records the live state of the Mesen integration.

## Direction change (rev 3): the 20xx launcher

- **The randomizer is gone.** The in-engine MM1 randomizer and its Museum
  "settings" menu were removed from the proxy, and `data.pie` was reverted to
  the pristine Museum (1884 entries, no `sprites/Museum/settings/*`, no RANDO
  entries/strings). Safety copies: `data.pie.pre20xx.bak` (the modded archive)
  and `.mmlc-backup/data.pie.ed17e550a580.bak` (the vanilla source used).
- **All 12 ROMs** (US Mega Man 1-6 + JP Rockman 1-6) now run through Mesen. The
  proxy reads a per-game `roms/<key>.nes`, silences the engine with a
  size-matched idle ROM, and loads the ROM (PRG+CHR+mapper) into the host when
  the game screen appears. Verified: all 12 `.nes` produce live frames in the
  core (mappers 1/2/4, incl. CHR-ROM MM3/MM5/RK3/RK5).
- **20xx** (`tools/20xx/20xx.py`) is the launcher. It extracts the legal ROMs
  from the user's own `Proteus.exe`/`Proteus.exe.orig.bak` into patched iNES
  `.nes` files and launches MMLC. The **Mesen replacer is an option** and the
  user supplies their own `MesenCore.so`:
  `20xx mesen --core /path/to/MesenCore.so [--start]` verifies the core's MMLC
  hooks, writes `mmlc.ini` (`mesen=1`, `mesen_core`, `mesen_port`,
  `mesen_defer_load`, `mesen_null_rom`), installs the proxy, and can start the
  host. Nothing GPL is shipped; the core is runtime-loaded/user-supplied.

## What is deployed and working

- **Mesen2 core built from source** with `tools/mesen_host/mesen2_capture.patch`
  applied, at
  `~/.cache/mmlc-mesen/Mesen2/InteropDLL/obj.linux-x64/MesenCore.so`.
- **Headless host** `~/.cache/mmlc-mesen/mesen_host` (x64 Linux) runs the ROM and
  serves video/audio/input/RAM over loopback TCP. `20xx` can build/start it.
- **Proxy** `steam_api.dll` (installed in `.../Suzy/`) forwards the engine's
  controller to Mesen, substitutes Mesen's video into the engine's screen
  buffer, bridges Mesen's audio out via `waveOut`, and hands each game's
  `roms/<key>.nes` to the host. No randomizer menu.
- **Video substitution works** (verified in-game): the engine composites a
  **1024x960 (4x NES)** BGRA buffer that it `Map`s `READ_WRITE` every frame; the
  proxy scales Mesen's 256x240 frame 4x into it on `Unmap`. 60 fps.
- **Input works** (A/B/Start/dpad; RB=Select; Back=MMLC UI).
- **Audio works** (Mesen's audio, played by the proxy); engine is silenced with
  a per-game idle ROM.
- **Intro skip** (`skip_intro=1`): patches `anyButtonPressed` (`0x43cc70`) so
  the photosensitivity warning / logo splash is skipped.

## Architecture / key findings (all verified)

- **D3D11 immediate context** = `[*(g_base+0x5cf174)+0x64]` (device at +0x60).
  Vtable: `Map` +0x38, `Unmap` +0x3c, `UpdateSubresource` +0xc0,
  `CopySubresourceRegion` +0xb8, `CopyResource` +0xbc.
- **Screen path** = the engine's 1024x960 buffer (`RowPitch=4096`,
  `DepthPitch=3932160`), mapped `READ_WRITE` per frame; substitution on `Unmap`.
  `UpdateSubresource` is never called. (`0x47bec0` `UpdateTexture` is a 64x64
  texture, NOT the screen.)
- **Engine CPU RAM** is at `NESSystem+0x0b` (vtable `0x6d04b4` at +0). RAM sync
  copies Mesen's 2 KB RAM into the engine each frame (`mesen_ram_sync=1`).
- **Freezing the engine's CPU step crashes it** (`advance` slot 5 = `0x460750`
  calls slot 4 = `0x403500`, the per-cycle step; no-op'ing slot 4 crashes).
  Instead `mesen_null_rom=1` gives the engine an idle ROM (all NOPs + JMP loop),
  which neutralizes its emulation without crashing.
- **Mesen2 InteropDLL has no controller API.** Added exports in the core:
  `SetControllerState(port,buttons)` (debugger input overrides),
  `ConfigureNesInput()`, `ResetConsole()`, `SetEmulationSpeed(percent)`,
  `SetNesOverclock(scanlines)`, `EnableCaptureAudio(bool)`, `GetAudioBuffer(...)`,
  `FlushAudioCapture()`, `EnableCaptureRenderer(bool)`, `GetVideoBuffer(...)`.
  `NesDebugger::ProcessInputOverrides` patched to apply unconditionally.
- **Headless Mesen defaults that must be fixed by `ConfigureNesInput()`:**
  `NesConfig.Port1/2.Type = None` (no controllers) and
  `NesConfig.ChannelVolumes[11] = {0}` (**mutes the APU** — the GUI sets these).
  Also `InitializeEmu` must use `noInput=false`.
- **Intro splash** is a state machine at `0x43c2f0` (`[this+8]`=state,
  `[this+0xc]`=frame). It waits for a button in the state entered when
  `[0x9ceb62] != 0` (the PHOTOSENSITIVITY WARNING screen) and skips via
  `anyButtonPressed()` at **`0x43cc70`** (single caller at `0x43c4d2`). The
  logos do NOT go through the hooked asset reader (`0x46c5c0`). Forcing
  `anyButtonPressed` to return 1 skips it safely (see open issue 5).
- **IPC must use `TCP_NODELAY` on both ends.** The protocol writes a 16-byte
  header and a separate payload; without it, Nagle delays every small
  reply by the delayed-ACK interval (~40 ms). This was the entire audio/video
  choppiness cause (see open issue 1).
- **The pristine MM1 PRG lives in `Proteus.exe.orig.bak`** at file offset
  `src_rva - 0x1200` (the packed backup stores `.rdata` unencrypted at unpacked
  offsets). Use it as the randomizer base; the installed `Proteus.exe` may
  already be modified.
- **Game-start signal = the engine's 4x NES screen.** The MMLC front-end never
  maps the `RowPitch=4096, DepthPitch=3932160` buffer; it first appears when a
  game is displayed. Used to release the deferred host ROM load. The engine's
  cycle counter (`+0x50D78`) stays 0 and is not usable for this.
- **`tools/mesen_host/mesen2_capture.patch` is now the full core diff** (video
  *and* audio capture + `ConfigureNesInput`/`SetControllerState`/`ResetConsole`/
  `SetEmulationSpeed`/`SetNesOverclock`); regenerate with
  `git -C ~/.cache/mmlc-mesen/Mesen2 diff > tools/mesen_host/mesen2_capture.patch`
  after any core edit.

## Build commands

```bash
# core (from source, patch already applied in ~/.cache/mmlc-mesen/Mesen2)
cd ~/.cache/mmlc-mesen/Mesen2
CPATH=/home/linuxbrew/.linuxbrew/include LIBRARY_PATH=/home/linuxbrew/.linuxbrew/lib \
    make core STATICLINK=false -j8
# -> InteropDLL/obj.linux-x64/MesenCore.so

# host
cc -O2 -o ~/.cache/mmlc-mesen/mesen_host \
   /var/home/user/Projects/mmlc-mod/tools/mesen_host/mesen_host.c -ldl -lpthread

# proxy (install's steam_api_orig.dll must exist)
cd /var/home/user/Projects/mmlc-mod
STEAM_API=/var/home/user/.local/share/Steam/steamapps/common/Suzy/steam_api_orig.dll \
   bash build/build_proxy.sh
cp build/steam_api.dll /var/home/user/.local/share/Steam/steamapps/common/Suzy/
```

## 20xx launcher

All commands are terminal/CLI (no GUI needed). `tools/20xx/20xx` is a wrapper for
`tools/20xx/20xx.py`.

```bash
# list / verify / extract the 12 legal ROMs into <game>/roms/<key>.nes
20xx list
20xx verify
20xx extract --game all

# offline patching (MM1 randomizer / palette / ROM hacks)
20xx patch mm1 --seed hello                    # weakness+weapons+palette
20xx patch mm1 --seed random --visualizer      # fresh seed + weakness colors
20xx patch mm1 --seed hello --palette-only     # palette shuffler only
20xx patch mm1 --romhack hack.ips --ips-adjust -16
20xx patch mm1 --romhack hack.bps              # BPS may resize the image

# enable the Mesen replacer with YOUR OWN core (verifies hooks, writes mmlc.ini,
# installs the proxy, optionally starts the host)
20xx mesen --core /path/to/MesenCore.so --start
20xx mesen --off

# patch + start host + launch, all from the terminal
20xx play mm1 --seed hello                     # patch + start host + launch
20xx play mm3 --seed hello                     # (pick the game in the collection)
20xx play --no-launch                          # prepare ROMs only
20xx launch --print                            # show the launch command

# interactive: pick a game -> curated action list -> launch with Mesen
20xx
#   [ 1] Mega Man 1 ... <-- randomizer     (randomizer only on MM1)
#   Mega Man 1 [US] -- what do you want to do?
#     [1] Vanilla   [2] Randomized (fresh seed)   [3] Randomized (seed)
#     [4] Palette shuffle only   [5] + weakness visualizer
#     [6] Apply a ROM hack (IPS/BPS)   [7] Just launch
#   -> "Launch the collection with Mesen now? [Y/n]"
```

`patch`/`play` build the PRG randomizer first, then apply ROM hacks to the whole
`.nes`. The randomizer is MM1-only (`RANDO_GAMES`); the ROM-hack patcher
(`tools/mmlc/romhack.py`, IPS + BPS) is generic. `--ips-adjust -16` targets a
headerless patch; omit it for `.nes`-targeted patches.

Note: `pkill -f "mesen_host --core"` matches its own shell — use `pkill -x mesen_host`.

## Open issues / status (rev 4)

0. **"No Mesen" root cause — FIXED.** The host called `InitializeDebugger()`
   unconditionally at startup; with no ROM loaded (20xx starts the host without
   `--rom` and the proxy sends the ROM later) that **segfaults** before the host
   listens, so the proxy fell back to Eclipse. The host now initializes the
   debugger after each successful `LOAD_ROM` (and only at startup if `--rom`).
   Also `20xx` now **auto-detects the Mesen core** (`mesen_core` ini, then
   `~/.cache/mmlc-mesen/...`, then `build/`) and starts the host.
1. **No in-collection auto-select.** The player picks the game in the MMLC menu
   after 20xx launches it. An XInput-injection auto-select was tried and removed:
   the front-end navigation was unreliable (it landed on the wrong game), so 20xx
   does **not** autorun a game. `20xx play <game>` just patches + starts the host
   + launches.
2. **Audio/video choppiness — FIXED.** Root cause was **TCP Nagle + delayed-ACK**:
   `send_msg` writes a 16-byte header then the payload as two small writes, so
   `GET_AUDIO`/`GET_RAM` cost ~41 ms. `TCP_NODELAY` on both ends fixed it
   (now ~0.1 ms). In-game: 4x `Unmap` dt 16-17 ms (~60 fps); CPU ~58%→~29%.
3. **Engine CPU ~29%** and already presents at 60 fps; a limiter is optional and
   would need the swap-chain present, not the context vtable we hook.
4. **Mesen ROM load — now per game.** The ctor queues `roms/<key>.nes`; the D3D
   `Map` hook releases it when the 4x NES screen first appears (a game started).
   `mesen_defer_load=1` (default). The front-end never composites the 4x screen.
5. **All 12 games supported.** US MM1-6 + JP RK1-6. The engine is silenced with a
   size-matched idle ROM; the host gets PRG+CHR+mapper+mirroring from the `.nes`.
   JP `[this+0x810]` sources verified = `file + 0x401200` (the old `+0x18` note
   was wrong), so `ROM_SRCS` maps every game. All 12 ROMs verified to render in
   the core (offline host test).
   - **Per-game `advance` override (why MM3-6 were black):** MM1/2 use the base
     `advance` (`0x460750`), but **MM3-6 and RK3-6 override vtable slot 5**
     (US `0x401b00/0x402090/0x402580/0x403100`, JP
     `0x463180/0x463670/0x463b30/0x4641b0`). Hooking only the base missed them,
     so active-detection never fired → no ROM loaded → black screen. The proxy
     now patches slot 5 of **all 12 game vtables** (RVAs in
     `GAME_VTABLE_RVAS`) with a stub that records the stepped instance
     (`g_active_inst`) and jumps to the original. Verified: MM3 loads
     (`loaded mm3 (active)`, host `mapper=4`).
6. **Randomizer removed from the engine; Museum restored.** `data.pie` reverted to
   the vanilla archive; the in-engine menu/randomizer code is gone. Patching now
   lives in 20xx (offline): `--seed` randomizer + palette + IPS/BPS ROM hacks.
   Offline randomizers now exist for **MM1, MM2, MM3, MM4, MM5**
   (`RANDO_GAMES`): weakness + reward + palette. MM2/MM3/MM5 boss weaknesses
   and MM5's weapon-get reward are byte-for-byte ports of the upstream
   randomizers (verified against them); MM4 and the other reward/palette paths
   are 20xx shuffles. MM6 is not yet randomized (its damage/reward tables are
   not yet located).
7. **Intro skip — implemented.** `skip_intro=1` patches `anyButtonPressed`
   (`0x43cc70`, single caller) to return 1, skipping the photosensitivity
   warning/logo splash without touching input state.
8. **Eclipse engine cannot be removed** (it is `Proteus.exe`; its CPU step is
   load-bearing and its render pipeline is the display path).
9. **`nop_overlay` finds 0 sites** — the install's `Proteus.exe` already has the
   `C6 04 08 DF` store NOPed on disk; the overlay hook still installs.
10. **On-disk `Proteus.exe` is modified** (old randomized MM1 ROM + visualizer
    IPS). 20xx extracts from `Proteus.exe.orig.bak` (pristine); a clean reinstall
    is still advisable.

## Useful diagnostics

- Proxy log: `.../Suzy/mmlc_proxy.log` (`d3d Unmap 4x dt=`, `MENU:`,
  `reloaded MM1`, `engine cycle=`, `audio:`, `options watch:`).
- Host log: `~/.cache/mmlc-mesen/host.log`.
- Test client scripts: `~/.cache/mmlc-mesen/test_*.py`.
- `mesen_probe=1` dumps `this+0..0x90000` to `mmlc_mesen_dump0..3.bin`.
