# 20XX v0.7.0 — collection picker, Windows host, automatic setup

20XX is a Mesen2-based launcher/mod host for **Mega Man Legacy Collection 1**
(Steam appid 363440). It runs the six NES games — and the six JP *Rockman* ROMs
— through a user-built Mesen2 core, and patches/randomizes them offline.

This release adds the new navigation flow, a Windows Mesen host, and one-command
setup.

## What's new in 0.7.0

- **Collection → game → page GUI.** Choose a collection, then a game, then work
  on that game's page. Global install/core/control options moved to Settings.
  **+ Add collection** registers another install folder; MMLC1 is the only
  collection kind with a game table today, others are kept as placeholders.
- **Per-game capability gating.** US MM1–MM6 pages offer the randomizer modes;
  JP Rockman pages offer vanilla/ROM-hack only; MM6 hides the not-yet-located
  palette mode.
- **Windows Mesen host.** `mesen_host.exe` is now built and bundled with the
  Windows release (Winsock + `LoadLibrary`, x86-64). Windows launch uses
  `steam://rungameid/...`. The Mesen2 core and `steam_api.dll` proxy are still
  user-supplied.
- **Automatic setup.** `20xx setup` follows Steam's `libraryfolders.vdf` to find
  the install on any drive, downloads Steamless on first use, unpacks
  `Proteus.exe`, backs up Valve's DLL as `steam_api_orig.dll`, installs the
  proxy, finds (or optionally builds) the core, configures Mesen, extracts and
  verifies all 12 ROMs. The GUI has an **Automatic setup** button.
- **`20xx doctor`.** Read-only readiness report for install, ROM source,
  packed/unpacked exe, Steamless, proxy, core hooks, host and extracted ROMs.
  Exits non-zero when the setup is not ready.
- **D-pad repair.** A duplicate direction binding (e.g. `btn_up=dpad_left` and
  `btn_left=dpad_left`) is detected by `doctor`, repaired by `setup`, and can no
  longer be saved from the GUI. Other custom bindings are preserved.
- **All 0.6.0 features remain:** MM1 Wily patches, hard mode/OHKO, cursor tint,
  MM1–MM6 randomizer/export, save-state hotkeys, and CPU-SPEED overclock follow.

## Downloads

| Platform | File | Notes |
|---|---|---|
| Linux x86_64 | `20XX-v0.7.0-x86_64.AppImage` | Tk GUI + CLI, bundled Mesen host |
| Windows x64 | `20XX-v0.7.0-win64.zip` | `20XX.exe` (GUI/CLI), bundled `mesen_host.exe` |

Both bundles contain **only 20XX code**. They do **not** include ROMs, game
assets, Steamless, the Mesen2 core, or the `steam_api.dll` proxy. You must own
the game and supply the user-built pieces below.

## Install (Linux)

```bash
chmod +x 20XX-v0.7.0-x86_64.AppImage
./20XX-v0.7.0-x86_64.AppImage          # GUI
./20XX-v0.7.0-x86_64.AppImage doctor   # check what is missing
./20XX-v0.7.0-x86_64.AppImage setup    # automatic setup
```

Mesen mode additionally needs:

1. A Mesen2 core built with `tools/mesen_host/mesen2_capture.patch`
   (`20xx build-core`, or let `20xx setup --yes` clone/build it), and
2. the `steam_api.dll` proxy, built once from **your own** installed
   `steam_api.dll`:

```bash
# requires bash + mingw-w64 (i686-w64-mingw32-gcc)
STEAM_API=/path/to/Suzy/steam_api.dll ./build/build_proxy.sh
```

`20xx setup` attempts the proxy build automatically when the toolchain is
available. Without the proxy/core, patching and export still work.

## Install (Windows)

Unzip and run `20XX.exe`. The GUI/CLI, automatic setup, offline patching,
seeding, verification and export all work. The bundled host is ready, but Mesen
mode still needs:

1. `MesenCore.dll` built from Mesen2 with the capture patch applied (build
   `InteropDLL` x64 from `Mesen.sln`), and
2. the proxy built from your own `steam_api.dll` using MSYS2/Git Bash +
   mingw-w64 (`build/build_proxy.sh`).

Then run `20XX.exe doctor` and `20XX.exe setup`.

## Upgrade note

Existing installs can simply run `20xx setup` (or the GUI's **Automatic
setup**). It repairs a previously duplicated D-pad binding and refreshes the
proxy while keeping the original DLL backed up as `steam_api_orig.dll`. Restart
MMLC after setup so the proxy reloads `mmlc.ini`.

## Legal

Patch-only. No Capcom code, ROMs or assets are distributed. 20XX reads your
own legally-owned install, keeps backups, and loads a Mesen2 core that you
build yourself (Mesen2 is GPLv3 and separate from this MIT project).

Full instructions: <https://github.com/MusouSlime/20xx#readme>
