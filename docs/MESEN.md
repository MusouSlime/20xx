# Phase 6 — Mesen core (replace the Eclipse NES emulator)

> **Status (rev 3, 2026-10-07):** the Mesen core is now wired for **all 12 ROMs**
> (US Mega Man 1-6 + JP Rockman 1-6), driven by the **20xx** launcher
> (`tools/20xx/20xx.py`). 20xx extracts the legal ROMs from the user's own
> `Proteus.exe` into `roms/<key>.nes` and launches MMLC; the proxy hands each
> game's `.nes` to the host and silences the engine with a size-matched idle
> ROM. The Mesen replacer is an **option** of 20xx and the user supplies their
> own `MesenCore.so` (`20xx mesen --core PATH`). The in-engine randomizer and
> its Museum menu were removed and `data.pie` reverted. See `HANDOFF.md` for
> the live state.

Goal: run the six MMLC NES ROMs (vanilla and randomized) through a **standard,
well-understood NES core** instead of Digital Eclipse's custom Eclipse core, so
that MMLC-specific behavior — the `0xDF` native call-out opcodes, the overlay
hot-patching, the custom mapper (`STA $C005`), and whatever resets the game on
boss defeat — no longer applies.

This document records the architecture decision, the Mesen2 API we build on,
and the milestone plan. It supersedes the "deferred" Phase 6 stub in `PLAN.md`.

## Why

The in-engine randomizer writes only data tables, yet the game resets when
beating a Robot Master (observed on Bomb Man). The remaining suspects are all
Eclipse-core behaviors:

- `0xDF` is a **native call-out opcode**; the engine writes it into the ROM heap
  at five code sites at load (the overlay writer, `FUN_0045c220`). Disabling the
  writer (which the proxy does) may leave engine-required paths unhandled.
- Custom mapper register (`STA $C005` in the weapon-get routine) that a stock
  core treats as a plain UxROM bank write.
- Possible engine-side integrity/state checks on the modified `.rdata` ROM.

A standard core executes the ROM bytes verbatim, which is exactly what a
randomizer expects.

## Constraints / decisions

| Topic | Decision |
|---|---|
| Core | **Mesen2** (accurate, maintained, x86-64 Linux `.so`) |
| Process model | **Separate x64 host process** + IPC. `Proteus.exe` is x86 and runs under Proton; the core is x64 Linux ELF, so it cannot be loaded in-process. |
| Linking | The prebuilt `MesenCore.so` renders only to a window, so the recommended path is to **build the Mesen2 core from source** and link our own small host that implements `IRenderingDevice`/`IAudioDevice` to capture frames/audio headlessly. |
| License | Mesen2 is **GPLv3**. The host is therefore GPLv3 and ships **separately / user-supplied**, loaded at runtime — it is not linked into the MIT-licensed proxy. |
| ROM source | The proxy hands the ROM/CHR bytes to the host (from `[this+0x810]`/`[this+0x80C]`), so the host never needs a ROM on disk. |
| Fallback | If the engine's rendering cannot be replaced cleanly, present the Mesen framebuffer in a borderless child window over the game (a "Mesen overlay") as a stopgap. |

## Mesen2 core API (from `InteropDLL/*.cpp`)

Relevant `extern "C"` exports (Linux names are unmangled):

```
InitDll()
InitializeEmu(homeFolder, windowHandle, viewerHandle, softwareRenderer,
              noAudio, noVideo, noInput)          // renderer only if windowHandle!=NULL
LoadRom(filename, patchFile) -> bool              // starts the emulation thread
IsRunning() / IsPaused() / Pause() / Resume() / Stop()
GetMesenVersion() / GetMesenBuildDate()
SetRendererSize(w,h) / GetBaseScreenSize() / GetAspectRatio()
TakeScreenshot()                                   // writes a PNG (slow path)
SaveState(idx) / LoadState(idx) / SaveStateFile / LoadStateFile
GetMemoryValue(MemoryType, addr) / GetMemoryValues(...)
GetCpuState / GetPpuState / GetConsoleState        // via DebugApiWrapper
SetKeyState(scanCode, state) / ResetKeyState       // input (keyboard mapping)
```

Video frames reach `IRenderingDevice::UpdateFrame(RenderedFrame&)`; the built-in
`SoftwareRenderer` keeps them in `_textureBuffer`. Our host implements its own
`IRenderingDevice` to copy each frame into shared memory. Audio reaches
`IAudioDevice::WriteResampled`/`Play` — likewise captured by a custom device.

## Architecture

```
 ┌──────────────────────────────┐        IPC (loopback TCP + shm)        ┌────────────────────────────┐
 │ Proteus.exe (x86, Proton)    │  ── load_rom / input / save / command ─▶ │ mesen_host (x64 Linux)     │
 │  steam_api.dll proxy         │                                          │  dlopen/build Mesen2 core  │
 │   • hooks NESSystem vtable   │  ◀─ framebuffer / audio / ram / state ── │  custom IRenderingDevice   │
 │   • feeds engine renderer    │                                          │  custom IAudioDevice       │
 └──────────────────────────────┘                                          └────────────────────────────┘
```

- **Command channel:** TCP on `127.0.0.1` (works across the Wine↔Linux boundary)
  or a Unix socket if Wine's `AF_UNIX` support is adequate. Length-prefixed
  messages, little-endian.
- **Frame channel:** POSIX shared memory (`/dev/shm/mmlc_frame`) with a double
  buffer and a sequence counter; the proxy copies the latest frame into the
  engine's PPU output buffer each frame.
- **Audio channel:** ring buffer in shared memory (48 kHz stereo i16).
- **RAM channel:** the host mirrors CPU RAM + PPU/APU state into a shared region
  so the proxy can read/write NES RAM directly (`read6502`/`write6502`).

## Integration points in Proteus.exe

From `RE_FACTS.md`:

- `NESSystem` vtable @ `0x6f2c9c` (11 slots); MM1 subclass @ `0x6d04b4`.
  Slot 5 (`advance`, `0x460750`) runs one frame of cycles; slot 2/3 are the
  read/bank-translate helpers.
- ROM source `[this+0x810]`, heap copy `[this+0x81C]`, CHR `[this+0x80C]`,
  sizes `[this+0x814]`/`[this+0x818]`.
- `read6502`/`write6502` adjacent (`0x4645e0` / sibling) are the RAM bridge.

Bridge strategy (in order of preference):

1. **Frame-level handoff (simplest):** hook `advance` (slot 5) so it does nothing
   locally; instead ask the host to run one frame and copy the host framebuffer
   into the engine's output surface. Input is read from the engine and forwarded.
2. **Memory-level handoff:** also route `read6502`/`write6502` to the host's RAM
   mirror so the game's own logic (menus, savestates) sees the Mesen state.

## Build & run (verified 2026-10-06)

Build the core (Linux, from source) with the two MMLC patches applied:

```bash
git clone --depth 1 https://github.com/SourMesen/Mesen2.git
cd Mesen2 && git apply /path/to/mmlc-mod/tools/mesen_host/mesen2_capture.patch
CPATH=/home/linuxbrew/.linuxbrew/include LIBRARY_PATH=/home/linuxbrew/.linuxbrew/lib \
    make core STATICLINK=false -j$(nproc)
# -> InteropDLL/obj.linux-x64/MesenCore.so
```

The patch adds a `CaptureRenderer : IRenderingDevice` and exports
`EnableCaptureRenderer(bool)` + `GetVideoBuffer(uint32_t*,uint32_t*,uint32_t*)`
(InteropDLL), and falls back to the built-in 2C02 palette when the headless
`NesConfig.UserPalette` is all zero (the GUI normally populates it).

Build and run the host:

```bash
cc -O2 -o mesen_host tools/mesen_host/mesen_host.c -ldl -lpthread
./mesen_host --core MesenCore.so --rom mm1.nes --port 36344
```

Verified: the host runs the MMLC ROM headlessly and serves the real title
screen over the protocol (`GET_FRAME`), plus 2 KB CPU RAM (`GET_RAM`).

## Milestones

- **M6.1 Host spike (Linux x64): DONE.** Core builds from source; headless
  capture renderer returns the title screen; RAM reads work.
- **M6.2 IPC transport: DONE (MVP).** Loopback TCP + POSIX-shm frame/RAM
  regions, HELLO handshake, `LOAD_ROM`/`RUN_FRAME`/`GET_FRAME`/`GET_RAM`/
  `SET_INPUT`/`SAVE_STATE`/`LOAD_STATE`/`SHUTDOWN`. Proxy client in
  `src/proxy/mesen_bridge.c` compiles for i686-w64-mingw32.
### M6.3 findings (2026-10-07, verified in-game)

Video, input and performance are working end-to-end. Key facts:

- **Video upload point is the D3D11 immediate context, not `UpdateTexture`.**
  The renderer singleton is at VA `0x9cf174` (RVA `0x5cf174`); its ctor
  (`0x47c4c0`) calls the `D3D11CreateDeviceAndSwapChain` wrapper (`0x47cd40`)
  with `ppDevice = this+0x60`, `ppImmediateContext = this+0x64`. We patch the
  context vtable: `Map` (+0x38), `Unmap` (+0x3c), `UpdateSubresource` (+0xc0),
  `CopySubresourceRegion`/`CopyResource` (+0xb8/+0xbc).
- **The engine composites a 1024x960 (exactly 4x NES) BGRA buffer** that it
  `Map`s `READ_WRITE` every frame (`RowPitch=4096`, `DepthPitch=3932160`); it
  ping-pongs with a partner texture via `CopyResource`. `UpdateSubresource` is
  never called. Substitution: on `Unmap` of that buffer, nearest-neighbour
  scale Mesen's 256x240 frame 4x into it. A magenta band across the middle
  confirms the path (row 0 is cropped as overscan).
- **Mesen2's InteropDLL has no controller API.** We added a `SetControllerState
  (port, buttons)` export that stores a `DebugControllerState` via the
  debugger's input overrides (applied on the `InputPolled` event, so they
  survive the per-frame input refresh). `NesDebugger::ProcessInputOverrides`
  was patched to apply unconditionally (releases now clear state).
- **The host must configure NES input before `LoadRom`**: a headless default
  leaves `NesConfig.Port1/2.Type = None`, so no controllers are registered.
  Added `ConfigureNesInput()` (sets both ports to `NesController`) and the host
  calls it before every `LoadRom`; `InitializeEmu` now uses `noInput=false`.
- **Freezing the engine's own emulation crashes it.** `advance` (`0x460750`,
  slot 5) loops calling slot 4 (`0x403500`, the per-cycle CPU step). A no-op on
  slot 5 is survivable (the engine still renders) but does not stop emulation;
  no-op'ing slot 4 crashes the game. `mesen_freeze` therefore defaults to 0.
- **Engine CPU RAM is at `NESSystem+0x0b`** (vtable `0x6d04b4` at +0). Found by
  cross-correlating a NESSystem dump against Mesen's live 2 KB RAM: 2032/2048
  bytes match (OAM `0xF8` pattern, zero page, stack). Candidate for the RAM
  bridge.
- **Performance:** the initial 4x blit used a per-pixel integer divide (~1M per
  frame); replaced with a fixed 4x replicate. Input is forwarded only on button
  change. Result: 60 fps (Unmap dt ≈ 16-17 ms, blit ≈ 0-1 ms).
- **Remaining desync:** the engine still runs its own NES emulation in parallel
  with Mesen (its audio is independent), so engine state and Mesen state
  diverge. Full handoff (RAM/APU sync or save-state mapping) is M6.4/M6.5.

- **M6.3 Proxy bridge: PARTIAL.** `mesen=1` / `mesen_host` / `mesen_port` config
  plumbed into the proxy; on game load the proxy connects to the host and hands
  it the finalized PRG+CHR (`mesen_bridge_load_rom`). Proxy builds with the
  bridge (`-lws2_32`). Remaining: (a) hook `NESSystem` slot 5 (`advance`) to run
  the host frame, (b) copy Mesen's framebuffer into the engine's render target,
  (c) forward input, (d) fall back to the Eclipse core on disconnect.
  - Framebuffer RE notes: it is **not** in the 11-slot vtable (slots 0–10 return
    descriptors/state; slot 5 = `advance`, slot 4 = per-cycle CPU step). The NES
    frame reaches the screen through the `bs::` render layer.
  - MSVC RTTI walking works (TypeDescriptor name is inline at TD+8; COL at
    `vtable-4`). Located vtables:
    | class | vtable | notes |
    |---|---|---|
    | `bs::nes::NESSystem` | `0x6f2c9c` | 11 slots (verified) |
    | `bs::Renderable` | `0x6ef440` | |
    | `bs::RenderTarget` / `RenderTargetDX11` | `0x937f84` / `0x938438` | DX11 has ~4 methods |
    | `bs::TextureManager` / `TextureManagerDX11` | `0x9338e8` / `0x937e88` | DX11 has 12 methods (0x47b440…) |
    | `bs::MegaManGame` | `0x6f0a8c` | |
  - **FOUND (video upload point):** `bs::TextureManagerDX11` vtable slot 9 =
    `0x47bec0` = `UpdateTexture(tex, data)` (args on stack: `[ebp+8]`=tex,
    `[ebp+0xc]`=data). It `Map`s the D3D texture (`[eax+0x38]`), memcpy's
    `width*height*4` bytes from **`data` (the NES framebuffer)** via `0x471d90`,
    then `Unmap`s (`[eax+0x3c]`). `tex+4`/`tex+8` are width/height (floats).
    The NES framebuffer is therefore a **separate allocation passed as arg2**,
    not a `NESSystem` field (which is why the in-object probe failed).
  - Integration: hook `0x47bec0` (RVA `0x7bec0`) at entry; when `tex` is
    256×240, point `arg2` at a buffer holding Mesen's frame. Pump
    `mesen_bridge_get_frame()` once per frame (in the `advance` hook). Watch the
    engine's pixel byte order vs Mesen's `0xAARRGGBB`.
- **M6.4 RAM bridge:** `read6502`/`write6502` backed by the host RAM mirror;
  verify the game's stage select / save logic.
- **M6.5 Audio + save states:** audio ring buffer; map the engine's save-state
  calls onto Mesen `SaveState`/`LoadState`.
- **M6.6 Hardening:** per-game mapper config, CHR handling, latency/tearing
  tuning, fallback to the Eclipse core on any failure (fail closed).

## Feasibility spike results (2026-10-06)

Using the prebuilt `~/.config/Mesen2/MesenCore.so` (Mesen 2.2.1, x86-64) via
`dlopen` on Linux, with `InitializeEmu(home, NULL, NULL, true,true,true,true)`
(no window) and `LoadRom(...)`:

- **Core runs headless.** `LoadRom -> 1`, `IsRunning -> 1`; the emulation thread
  runs without a renderer/window.
- **Video capture works headless.** `NesPpu` feeds `VideoDecoder::UpdateFrame`
  directly, so `TakeScreenshot()` writes a real 256×240 PNG with no window.
- **RAM works headless.** `InitializeDebugger()` + `GetMemoryValues(NesMemory=8,
  ...)` returns live CPU RAM (values change frame to frame).

**The MMLC ROMs DO run on the standard mapper.** (An earlier draft of this doc
claimed they hang/blank — that was a **headless screenshot-pipeline artifact**,
not a real failure.)

Verified with the extracted `mm1.nes` (mapper 2 / CHR-RAM, PRG-only):

- NMI frame counter `$23` advances every frame, so the CPU and PPU run normally.
- CHR-RAM is populated by the game itself (6606/8192 bytes non-zero), the
  palette is written, and the nametable is filled (1000/1024 non-zero).
- Reconstructing the PPU background from CHR+nametable+palette shows the actual
  **"MEGA MAN / PRESS START / © 1987 CAPCOM"** title screen.
- A **randomized** ROM (`randomize_mm1(seed, weapons, palette)`) runs the same
  way, so the randomizer output is compatible with a stock core.

Caveat: `TakeScreenshot()` returns a blank white PNG in the no-window setup
(the `VideoDecoder` filter path needs a renderer), so headless frame capture
must go through a custom `IRenderingDevice` (build Mesen2 from source) rather
than the screenshot API. The MMLC blob being PRG-only is fine — the game fills
CHR-RAM itself; no engine-supplied CHR is required.

Consequence: the boss-defeat reset is an **Eclipse-core** behavior, which is
exactly what Phase 6 removes. This path is viable.

## Risks

| Risk | Mitigation |
|---|---|
| MMLC ROMs depend on engine-supplied CHR / custom mapper | Verify the extracted ROM is self-contained in M6.1; otherwise hand the engine's CHR buffer to the host. |
| x86↔x64 + Wine↔Linux IPC | Prefer loopback TCP (well supported under Wine) over `AF_UNIX`; shm via a file in `/dev/shm`. |
| Engine rendering not replaceable | Overlay a Mesen child window over the game as a stopgap. |
| GPLv3 contamination | Host ships separately, user-supplied; never linked into the proxy. |
| Latency/tearing | Double-buffered shm with vsync pacing; frame-level handoff. |
| Save-state format mismatch | Map to Mesen's own save states; keep engine savestates for non-Mesen mode. |

## Build layout

```
tools/mesen_host/           x64 Linux host (GPLv3, user-supplied core)
  mesen_host.c              entry point, arg parsing, IPC loop
  mesen_proto.h             shared IPC protocol (also included by the proxy)
  capture_renderer.{h,cpp}  custom IRenderingDevice  (needs Mesen2 source)
  capture_audio.{h,cpp}     custom IAudioDevice
src/proxy/mesen_bridge.c    proxy-side client (x86), behind `mesen=1`
```
