#!/usr/bin/env python3
"""20xx -- a launcher for the MMLC1 NES games.

Extracts the legal ROMs from the user's own Mega Man Legacy Collection install
(`Proteus.exe` / `Proteus.exe.orig.bak`) into patched iNES `.nes` files, then
launches the collection, which runs the selected game through its Mesen2
integration.

Patch-only: 20xx never bundles or redistributes any ROM or game asset. It only
reads the user's legally-owned install and writes `.nes` files next to it.

Usage (all commands are terminal/CLI; no GUI required):
    20xx.py list                     # list the 12 games
    20xx.py verify                   # check the ROM anchors in the install
    20xx.py extract [--game KEY]     # write vanilla .nes files into <game>/roms/
    20xx.py export [GAME] [opts]     # export the 12 ROMs (patched where picked)
                                     #   to ~/MMLC-ROMs for your own emulator
    20xx.py patch GAME [opts]        # offline randomizer / palette / ROM hacks
    20xx.py play [GAME] [opts]       # patch + start host + launch, from the CLI
    20xx.py launch [--print]         # launch the collection (or print the cmd)
    20xx.py doctor                   # readiness check (install/proxy/core/host)
    20xx.py setup [--start]          # automatic setup (unpack, proxy, core, ROMs)
    20xx.py mesen --core PATH        # enable the Mesen replacer (your core)
    20xx.py unpack [--steamless P]   # Steamless-unpack Proteus.exe by hand
    20xx.py                          # interactive: pick a game, choose what to
                                     #   do (vanilla / rando / palette / ROM
                                     #   hack), then launch with Mesen

Patch options (for `patch` and `play`):
    --seed S            randomizer seed ('random' for a fresh one)
    --[no-]weakness     shuffle boss weaknesses        (MM1/MM2/MM3/MM4/MM5)
    --[no-]weapons      shuffle weapon rewards         (MM1/MM2/MM3/MM4/MM5)
    --[no-]palette      shuffle Mega Man's palette     (MM1/MM2/MM3/MM4/MM5)
    --palette-only      palette shuffle only
    --[no-]visualizer   stage-select colors + green marker (MM1, default on)
    --victory-lap       max out all MM1 boss damage (easy kills)
    --door-palette      tint each MM1 boss door with its weakness color
    --vanilla-wily      keep the MM1 Wily-stage damage rows vanilla
    --cursor-tint       tint the selected stage-select portrait (MM1)
    --hard-mode         MM1 hard mode (damage received x2, dealt /1.5)
    --ohko              MM1 one-hit kills (both directions)
    --romhack FILE      apply an IPS/BPS ROM hack (repeatable)
    --ips-adjust N      add N to IPS offsets (-16 = headerless patch)

Options:
    --game-dir DIR   install folder (default $MMLC_GAME or the Steam path)
    --from PATH      ROM source (default: Proteus.exe.orig.bak, then Proteus.exe)
    --out DIR        output folder (default <game-dir>/roms)
    --appid N        Steam app id (default 363440)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

# --- Frozen vs. source-tree layout ---------------------------------------
# `BUNDLE` is the read-only resource root (the repo, or PyInstaller's _MEIPASS).
# `BUILD_DIR` is writable (built host/proxy, logs): <repo>/build in a checkout,
# ~/.cache/mmlc-mesen/build in a frozen AppImage/exe (which may be read-only).
FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
if FROZEN:
    BUNDLE = os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
    BUILD_DIR = os.environ.get(
        "MMLC_BUILD_DIR", os.path.expanduser("~/.cache/mmlc-mesen/build"))
else:
    ROOT = os.path.dirname(os.path.dirname(HERE))
    BUNDLE = ROOT
    BUILD_DIR = os.environ.get("MMLC_BUILD_DIR", os.path.join(ROOT, "build"))
sys.path.insert(0, os.path.join(BUNDLE, "tools"))
os.makedirs(BUILD_DIR, exist_ok=True)

from mmlc import (hardmode, patcher, randomizer, romhack, romtable,
                  setup_env, steamless, wily)
from mmlc.pe import PE, write_file_atomic

_WIN = sys.platform.startswith("win")


def _default_game_dir() -> str:
    if _WIN:
        bases = [os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("ProgramFiles")]
        for base in bases:
            if base:
                p = os.path.join(base, "Steam", "steamapps", "common", "Suzy")
                if os.path.isdir(p):
                    return p
        return os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                            "Steam", "steamapps", "common", "Suzy")
    return "/var/home/user/.local/share/Steam/steamapps/common/Suzy"


DEFAULT_GAME_DIR = _default_game_dir()
DEFAULT_APPID = "363440"

# Brand/version shown so it is obvious which build is running.
APP = "20XX"
VERSION = "0.6.0"

# --- Mesen replacer (optional; the user supplies their own Mesen2 core) ------
HOST_SRC = os.path.join(BUNDLE, "tools", "mesen_host", "mesen_host.c")
HOST_BIN = "mesen_host.exe" if _WIN else "mesen_host"
# A host prebuilt into the bundle (release AppImage/exe) is used as-is;
# otherwise build it on first use into the writable BUILD_DIR.
_BUNDLED_HOST = next((p for p in (os.path.join(ROOT, "build", HOST_BIN),
                                  os.path.join(BUNDLE, "build", HOST_BIN))
                      if os.path.exists(p)), None)
# Always run the host from the writable cache, never from a read-only AppImage
# mount or PyInstaller's temporary _MEIPASS (which may be cleaned up while the
# detached host is still running).
HOST_BUILD = os.path.join(BUILD_DIR, HOST_BIN)
PROXY_BUILD = next((p for p in (os.path.join(BUNDLE, "build", "steam_api.dll"),
                                os.path.join(ROOT, "build", "steam_api.dll"),
                                os.path.join(BUILD_DIR, "steam_api.dll"))
                    if os.path.exists(p)),
                   os.path.join(BUILD_DIR, "steam_api.dll"))
PROXY_BUILD_SCRIPT = os.path.join(BUNDLE, "build", "build_proxy.sh")
DEFAULT_PORT = 36344
# Mesen2 source + the MMLC capture patch, for `20xx build-core`.
MESEN2_REPO = "https://github.com/SourMesen/Mesen2.git"
MESEN2_PATCH = os.path.join(BUNDLE, "tools", "mesen_host", "mesen2_capture.patch")
MESEN2_DEFAULT_DIR = os.path.expanduser("~/.cache/mmlc-mesen/Mesen2")
# Places to look for a user-supplied Mesen2 core if mmlc.ini has no mesen_core.
CORE_SEARCH = [
    os.path.expanduser("~/.cache/mmlc-mesen/Mesen2/InteropDLL/obj.linux-x64/MesenCore.so"),
    os.path.expanduser("~/.cache/mmlc-mesen/MesenCore.so"),
    os.path.join(BUILD_DIR, "MesenCore.so"),
    os.path.join(ROOT, "build", "MesenCore.so"),
    os.path.join(ROOT, "MesenCore.so"),
]
if _WIN:
    _mesen2 = os.path.expanduser("~/.cache/mmlc-mesen/Mesen2")
    CORE_SEARCH = [
        os.path.join(BUILD_DIR, "MesenCore.dll"),
        os.path.join(ROOT, "build", "MesenCore.dll"),
        os.path.join(ROOT, "MesenCore.dll"),
        os.path.join(_mesen2, "InteropDLL", "bin", "x64", "Release", "MesenCore.dll"),
        os.path.join(_mesen2, "InteropDLL", "bin", "Release", "MesenCore.dll"),
        os.path.join(_mesen2, "InteropDLL", "x64", "Release", "MesenCore.dll"),
    ]
# Exports the host needs from the core (added by tools/mesen_host/mesen2_capture.patch).
CORE_REQUIRED_EXPORTS = [
    "InitDll", "InitializeEmu", "LoadRom", "EnableCaptureRenderer",
    "GetVideoBuffer", "GetMemoryValues", "ConfigureNesInput",
    "SetControllerState", "EnableCaptureAudio", "GetAudioBuffer",
    "FlushAudioCapture", "Pause", "Resume", "ResetConsole",
]

# Display order: US Mega Man 1-6, then JP Rockman 1-6.
GAME_ORDER = ["mm1", "mm2", "mm3", "mm4", "mm5", "mm6",
              "rk1", "rk2", "rk3", "rk4", "rk5", "rk6"]


def game_dir_default() -> str:
    env = os.environ.get("MMLC_GAME")
    if env:
        return env
    # Follow Steam libraries instead of assuming the default install drive.
    found = setup_env.find_install()
    return found or DEFAULT_GAME_DIR


def find_source(game_dir: str, override: Optional[str] = None) -> str:
    """Return the pristine ROM source.

    ``Proteus.exe.orig.bak`` (the packed SteamStub backup) keeps ``.rdata``
    unencrypted at unpacked offsets, so its ROM blobs are the original legal
    dumps. The installed ``Proteus.exe`` may have been modified (e.g. an on-disk
    randomizer pass), so it is only a fallback.
    """
    if override:
        return override
    for name in ("Proteus.exe.orig.bak", "Proteus.exe"):
        p = os.path.join(game_dir, name)
        if os.path.exists(p):
            return p
    raise SystemExit(f"20xx: no Proteus.exe(.orig.bak) in {game_dir}")


# --- Steamless (SteamStub unpack) -------------------------------------------
# The Mesen proxy's hooks are AOB-scanned in the *unpacked* .text. A clean Steam
# install ships the SteamStub-packed Proteus.exe (extra `.bind` section, entry in
# `.bind`), so every scan misses and 20XX does nothing. Automatic setup unpacks it.
PROTEUS_NAME = "Proteus.exe"
PROTEUS_ORIG_BAK = "Proteus.exe.orig.bak"
PROTEUS_PACKED_BAK = "Proteus.exe.packed.bak"
STEAMSTUB_SECTION = ".bind"
# `advance` (NESSystem vtable slot 5) prologue, VA 0x460750 in the unpacked exe.
# Its presence proves .text is decrypted, i.e. the proxy's AOB scans will match.
PROTEUS_ANCHOR = bytes.fromhex("568BF18B8E780D050081F9557400007D1D")


def _section_names(path: str) -> List[str]:
    try:
        return [s.name for s in PE(path).sections]
    except (OSError, ValueError):
        return []


def proteus_is_packed(path: str) -> bool:
    """True if *path* is still SteamStub-packed (has a `.bind` section)."""
    return STEAMSTUB_SECTION in _section_names(path)


def proteus_is_unpacked(path: str) -> bool:
    """True if *path* has a decrypted .text (the proxy's AOB anchor is present)."""
    try:
        with open(path, "rb") as fh:
            return PROTEUS_ANCHOR in fh.read()
    except OSError:
        return False


def _detect_steamless() -> Optional[str]:
    """Return a Steamless.CLI.exe path if one is discoverable, else None."""
    try:
        return steamless.find_cli()
    except steamless.SteamlessError:
        return None


def ensure_proteus_unpacked(game_dir: str, steamless_cli: Optional[str] = None,
                            *, force: bool = False, status=print) -> bool:
    """Unpack SteamStub-protected ``Proteus.exe`` so the proxy's runtime hooks
    resolve. No-op when the exe is already unpacked.

    Keeps ``Proteus.exe.orig.bak`` (the packed original, used for ROM
    extraction) and ``Proteus.exe.packed.bak`` (restore point). Returns True on
    success (including the already-unpacked case).
    """
    import shutil
    exe = os.path.join(game_dir, PROTEUS_NAME)
    if not os.path.exists(exe):
        status(f"20xx: {exe} not found")
        return False
    if not force and not proteus_is_packed(exe):
        if proteus_is_unpacked(exe):
            status("[20xx] Proteus.exe is already unpacked")
        else:
            status("[20xx] Proteus.exe has no SteamStub `.bind` section; "
                   "leaving as-is")
        return True

    orig_bak = os.path.join(game_dir, PROTEUS_ORIG_BAK)
    if not os.path.exists(orig_bak):
        shutil.copy2(exe, orig_bak)
        status(f"[20xx] saved packed backup -> {os.path.basename(orig_bak)}")

    packed_bak = os.path.join(game_dir, PROTEUS_PACKED_BAK)
    shutil.copy2(exe, packed_bak)
    stale = exe + ".unpacked.exe"
    if os.path.exists(stale):
        try:
            os.remove(stale)
        except OSError:
            pass
    try:
        cli = steamless.ensure_cli(steamless_cli, status=status)
    except steamless.SteamlessError as ex:
        status(f"20xx: Steamless unavailable: {ex}")
        status("      pass --steamless PATH or set $STEAMLESS_CLI")
        return False
    status(f"[20xx] unpacking Proteus.exe with Steamless ...")
    try:
        steamless.unpack(exe, cli, out=exe)
    except steamless.SteamlessError as ex:
        status(f"20xx: Steamless failed: {ex}")
        return False
    except OSError as ex:
        status(f"20xx: cannot replace {os.path.basename(exe)}: {ex}")
        status("      close the game (and Steam's 'verify' dialog) and retry")
        try:
            shutil.copy2(packed_bak, exe)
        except OSError:
            pass
        return False
    if not proteus_is_unpacked(exe) or proteus_is_packed(exe):
        status("20xx: Steamless output is not unpacked; restoring original")
        try:
            shutil.copy2(packed_bak, exe)
        except OSError:
            pass
        return False
    status(f"[20xx] unpacked Proteus.exe "
           f"(packed backup: {os.path.basename(packed_bak)})")
    return True


def selected_games(game: Optional[str]) -> List[romtable.Game]:
    if game and game.lower() != "all":
        return [romtable.resolve(game)]
    return [romtable.resolve(k) for k in GAME_ORDER]


def extract(game_dir: str, out_dir: str, game: Optional[str] = None,
            source: Optional[str] = None, quiet: bool = False) -> Dict[str, str]:
    """Extract the selected games to iNES ``.nes`` files. Returns key->path."""
    src = find_source(game_dir, source)
    pe = PE(src)
    os.makedirs(out_dir, exist_ok=True)
    written: Dict[str, str] = {}
    for g in selected_games(game):
        out = os.path.join(out_dir, f"{g.key}.nes")
        write_file_atomic(out, patcher.extract_ines(pe, g))
        written[g.key] = out
        if not quiet:
            prg = patcher.read_prg(pe, g)
            print(f"  {g.key:4} {g.title:20} {g.region:2} "
                  f"prg={g.prg.size:#07x} chr={(g.chr.size if g.chr else 0):#07x} "
                  f"crc={patcher.crc32(prg):#010x} -> {out}")
    if not quiet:
        print(f"[20xx] extracted {len(written)} ROM(s) from {os.path.basename(src)}")
    return written


# --- offline patching (randomizer / palette / ROM hacks) --------------------

# Games with an offline randomizer: MM1 (weakness/rewards/palette);
# MM2/MM3/MM5 (weakness + reward + palette, MM2/MM3/MM5 weaknesses and the MM5
# weapon-get reward are byte-for-byte ports of the upstream tools); MM4
# (weakness + reward + palette, own deterministic shuffles); MM6 (weapon-get
# reward only -- damage/weakness and palettes are not located yet).
RANDO_GAMES = {"mm1", "mm2", "mm3", "mm4", "mm5", "mm6"}


def random_seed(length: int = 5) -> str:
    """A short, letter-only seed (default 5 letters)."""
    import random
    import string
    return "".join(random.choice(string.ascii_lowercase) for _ in range(length))


# --- ROM-hack drop-in folder ------------------------------------------------
ROMHACKS_DIRNAME = "romhacks"

def romhacks_root(game_dir: str) -> str:
    return os.path.join(game_dir, ROMHACKS_DIRNAME)

def discover_romhacks(game_dir: str, key: str) -> List[str]:
    """Patches for a game: `romhacks/<key>/*` sorted by filename (.ips/.bps).
    Each game only looks in its own folder."""
    d = os.path.join(romhacks_root(game_dir), key)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, name) for name in sorted(os.listdir(d))
            if name.lower().endswith((".ips", ".bps"))]

def ensure_romhacks_dir(game_dir: str) -> str:
    """Create the drop-in folder (one subfolder per game key). Returns its path."""
    root = romhacks_root(game_dir)
    os.makedirs(root, exist_ok=True)
    for k in GAME_ORDER:
        os.makedirs(os.path.join(root, k), exist_ok=True)
    return root

def _patch_adjust(path: str, default: int) -> int:
    """Optional per-patch IPS offset adjust from a `<patch>.adjust` sidecar."""
    try:
        with open(path + ".adjust", "r", encoding="utf-8") as fh:
            return int(fh.read().strip(), 0)
    except (OSError, ValueError):
        return default


def build_patched_nes(game: romtable.Game, pe: PE, *,
                      seed: Optional[str] = None,
                      weakness: bool = True,
                      weapons: bool = True,
                      palette: bool = True,
                      visualizer: bool = False,
                      victory_lap: bool = False,
                      door_palette: bool = False,
                      vanilla_wily: bool = False,
                      cursor_tint: bool = False,
                      hard_mode: bool = False,
                      ohko: bool = False,
                      romhacks: Optional[List[str]] = None,
                      ips_adjust: int = 0) -> bytes:
    """Return a patched iNES image: randomizer/palette (per game) + ROM hacks.

    The PRG is randomized first, then the MM1 Wily patches (victory lap /
    door palette, which read the final damage charts), then ROM-hack patches
    are applied to the whole .nes (so a hack sees the final tables). BPS
    hacks may resize the image."""
    nes = bytearray(patcher.extract_ines(pe, game))
    prg_off, prg_size = 16, game.prg.size

    if seed is not None:
        if game.key not in RANDO_GAMES:
            print(f"20xx: no randomizer for {game.key} yet; ignoring --seed")
        else:
            prg = bytearray(nes[prg_off:prg_off + prg_size])
            rkw = dict(weakness=weakness, weapons=weapons,
                       visualizer=visualizer, palette=palette)
            if game.key == "mm1":
                rkw["cursor_tint"] = cursor_tint
            spoiler = randomizer.randomize(game.key, prg, seed, **rkw)
            nes[prg_off:prg_off + prg_size] = prg
            print(f"20xx: randomized {game.key} seed={seed!r} "
                  f"weakness={weakness} weapons={weapons} palette={palette} "
                  f"visualizer={visualizer}")
            for line in spoiler.lines()[1:]:
                print("   " + line)
    elif game.key == "mm1" and cursor_tint:
        # Standalone hook on vanilla charts (no randomizer run): no
        # visualizer routine, so record the top-buster index for the
        # hook's runtime green check the same way randomize_mm1 does.
        prg = bytearray(nes[prg_off:prg_off + prg_size])
        charts = [list(prg[randomizer.MM1_DAMAGE_TABLE + b * 8:
                           randomizer.MM1_DAMAGE_TABLE + b * 8 + 8])
                  for b in range(6)]
        prg[randomizer.MM1_GREEN_BOSS] = max(
            range(6), key=lambda b: charts[b][0])
        randomizer.apply_cursor_hook(prg, charts)
        nes[prg_off:prg_off + prg_size] = prg
        print("20xx: installed mm1 cursor tint hook (vanilla charts)")

    hook = game.key == "mm1" and (
        cursor_tint or (seed is not None and visualizer))
    if game.key == "mm1" and (victory_lap or door_palette or vanilla_wily
                              or hard_mode or ohko):
        prg = bytearray(nes[prg_off:prg_off + prg_size])
        if victory_lap or door_palette or vanilla_wily:
            wily.apply_patches(prg, wily.WilyPatchConfig(
                victory_lap=victory_lap, colored_doors=door_palette,
                vanilla_wily=vanilla_wily))
            print(f"20xx: wily patches mm1 victory_lap={victory_lap} "
                  f"door_palette={door_palette} vanilla_wily={vanilla_wily}")
        if ohko:
            # OHKO wins over hard mode; applied after the randomizer so the
            # visualizer/doors/tint (computed pre-OHKO) stay meaningful.
            hardmode.apply_ohko(prg)
            print("20xx: mm1 OHKO mode (any hit = instant death)")
        elif hard_mode:
            # Scale the final damage tables (after the randomizer/victory
            # lap) so it applies "no matter what mode"; preserves argmax,
            # so the weakness colors stay correct.
            hardmode.apply_hard_mode(prg)
            print("20xx: mm1 hard mode (damage received x2, dealt /1.5)")
        if hook and not ohko:
            # victory lap / hard mode rewrite the damage charts after the
            # cursor hook was installed; refresh its weakness table
            # (+ $FF43) from the final charts (no-op rewrite otherwise).
            # OHKO flattens the charts, so skip it and keep the pre-OHKO
            # weakness colors.
            randomizer.refresh_cursor_hook(prg)
        nes[prg_off:prg_off + prg_size] = prg

    for path in (romhacks or []):
        with open(path, "rb") as fh:
            patch = fh.read()
        nes = romhack.apply(nes, patch, _patch_adjust(path, ips_adjust))
        print(f"20xx: applied romhack {os.path.basename(path)} "
              f"({len(patch)} bytes) -> {len(nes):#x} bytes")
    return bytes(nes)


def patch_game(game_dir: str, out_dir: str, game_key: str, *,
               source: Optional[str] = None, seed: Optional[str] = None,
               weakness: bool = True, weapons: bool = True,
               palette: bool = True, visualizer: bool = False,
               victory_lap: bool = False, door_palette: bool = False,
               vanilla_wily: bool = False, cursor_tint: bool = False,
               hard_mode: bool = False, ohko: bool = False,
               romhacks: Optional[List[str]] = None,
               ips_adjust: int = 0, auto_romhacks: bool = True) -> str:
    src = find_source(game_dir, source)
    pe = PE(src)
    g = romtable.resolve(game_key)
    os.makedirs(out_dir, exist_ok=True)
    hacks = list(romhacks or [])
    if auto_romhacks:
        hacks = discover_romhacks(game_dir, g.key) + hacks
    data = build_patched_nes(g, pe, seed=seed, weakness=weakness,
                             weapons=weapons, palette=palette,
                             visualizer=visualizer, victory_lap=victory_lap,
                             door_palette=door_palette, vanilla_wily=vanilla_wily,
                             cursor_tint=cursor_tint, hard_mode=hard_mode,
                             ohko=ohko, romhacks=hacks,
                             ips_adjust=ips_adjust)
    out = os.path.join(out_dir, f"{g.key}.nes")
    write_file_atomic(out, data)
    print(f"20xx: wrote {out} ({len(data)} bytes)")
    return out


def _seed_arg(args: argparse.Namespace) -> Optional[str]:
    """Resolve --seed: None = vanilla, 'random' = fresh seed, else literal."""
    if args.seed is None and args.palette_only:
        args.seed = "random"
    if args.seed is None:
        return None
    return random_seed() if args.seed.lower() == "random" else args.seed


def _apply_palette_only(args: argparse.Namespace) -> None:
    if args.palette_only:
        args.weakness = args.weapons = False
        args.palette = True
        # "palette only" means exactly that: don't let the now-default-on
        # visualizer (and its green/cursor-tint extras) sneak in, matching
        # the GUI's "Palette shuffle only" mode.
        args.visualizer = False


def verify(game_dir: str, source: Optional[str] = None) -> int:
    src = find_source(game_dir, source)
    pe = PE(src)
    bad = 0
    print(f"[20xx] ROM anchors in {src}:")
    for g in selected_games(None):
        prg = patcher.read_prg(pe, g)
        crc = patcher.crc32(prg)
        want = romtable.ORIGINAL_PRG_CRC.get(g.key)
        ok = want is None or crc == want
        if not ok:
            bad += 1
        mark = "ok" if ok else f"MISMATCH (want {want:#010x})"
        print(f"  {g.key:4} {g.title:20} crc={crc:#010x} {mark}")
    if bad:
        print(f"[20xx] {bad} ROM(s) differ; use Proteus.exe.orig.bak as --from.")
    return 1 if bad else 0


def launch(appid: str = DEFAULT_APPID, status=print) -> None:
    if _WIN:
        status(f"[20xx] steam://rungameid/{appid}")
        try:
            os.startfile(f"steam://rungameid/{appid}")  # type: ignore[attr-defined]
            status("[20xx] launch requested (collection should open)")
        except OSError as ex:
            status(f"20xx: launch failed: {ex}")
        return
    try:
        if subprocess.run(["pgrep", "-x", "Proteus.exe"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          ).returncode == 0:
            status("[20xx] note: Proteus.exe is already running — close it first, "
                   "Steam ignores -applaunch while the game is up")
    except (OSError, ValueError):
        pass
    status(f"[20xx] steam -applaunch {appid}")
    try:
        subprocess.Popen(["steam", "-applaunch", appid])
        status("[20xx] launch requested (collection should open)")
    except FileNotFoundError:
        status("20xx: 'steam' not found on PATH")
    except OSError as ex:
        status(f"20xx: launch failed: {ex}")


# --- Mesen replacer option --------------------------------------------------

def ini_path(game_dir: str) -> str:
    return os.path.join(game_dir, "mmlc.ini")


def ini_get(game_dir: str, key: str) -> Optional[str]:
    path = ini_path(game_dir)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s[0] in "#;" or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return None


def ini_set(game_dir: str, values: Dict[str, str]) -> str:
    """Update/append key=value pairs, preserving the rest of the file."""
    path = ini_path(game_dir)
    lines: List[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    remaining = dict(values)
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s[0] in "#;" or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k in remaining:
            lines[i] = f"{k}={remaining.pop(k)}"
    for k, v in remaining.items():
        lines.append(f"{k}={v}")
    write_file_atomic(path, ("\n".join(lines) + "\n").encode("utf-8"))
    return path


def ini_remove(game_dir: str, keys) -> str:
    """Drop obsolete keys (e.g. the removed randomizer options)."""
    path = ini_path(game_dir)
    if not os.path.exists(path):
        return path
    drop = set(keys)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    kept = []
    for line in lines:
        s = line.strip()
        if s and s[0] not in "#;" and "=" in s and s.split("=", 1)[0].strip() in drop:
            continue
        kept.append(line)
    write_file_atomic(path, ("\n".join(kept) + "\n").encode("utf-8"))
    return path


def find_core(game_dir: Optional[str] = None, override: Optional[str] = None) -> Optional[str]:
    """Locate a Mesen2 core: explicit path, mmlc.ini, then known build paths."""
    if override and os.path.exists(override):
        return override
    if game_dir:
        v = ini_get(game_dir, "mesen_core")
        if v and os.path.exists(v):
            return v
    for p in CORE_SEARCH:
        if os.path.exists(p):
            return p
    return None


def check_core(core: str) -> bool:
    """Verify a user-supplied Mesen2 core has the MMLC capture hooks."""
    import ctypes
    if not os.path.exists(core):
        print(f"20xx: core not found: {core}")
        return False
    try:
        if _WIN and hasattr(os, "add_dll_directory"):
            # Let the loader find dependent DLLs shipped beside MesenCore.dll.
            os.add_dll_directory(os.path.dirname(os.path.abspath(core)))
        lib = ctypes.CDLL(core)
    except OSError as ex:
        print(f"20xx: cannot load core: {ex}")
        return False
    missing = [s for s in CORE_REQUIRED_EXPORTS if not hasattr(lib, s)]
    if missing:
        print(f"20xx: core is missing {len(missing)} MMLC hook(s): {', '.join(missing)}")
        print("      Build Mesen2 with tools/mesen_host/mesen2_capture.patch applied.")
        return False
    print(f"20xx: core OK ({len(CORE_REQUIRED_EXPORTS)} hooks present)")
    return True


def ensure_host() -> Optional[str]:
    if _BUNDLED_HOST:
        try:
            stale = (not os.path.exists(HOST_BUILD)
                     or os.path.getsize(HOST_BUILD) != os.path.getsize(_BUNDLED_HOST)
                     or os.path.getmtime(HOST_BUILD) < os.path.getmtime(_BUNDLED_HOST))
        except OSError:
            stale = True
        if stale:
            import shutil
            os.makedirs(os.path.dirname(HOST_BUILD), exist_ok=True)
            try:
                shutil.copy2(_BUNDLED_HOST, HOST_BUILD)
                os.chmod(HOST_BUILD, 0o755)
                print(f"20xx: host installed -> {HOST_BUILD}")
            except OSError as ex:
                print(f"20xx: cannot install bundled host: {ex}")
    if os.path.exists(HOST_BUILD):
        return HOST_BUILD
    if not os.path.exists(HOST_SRC):
        print(f"20xx: host source missing: {HOST_SRC}")
        return None
    os.makedirs(os.path.dirname(HOST_BUILD), exist_ok=True)
    print(f"20xx: building host -> {HOST_BUILD}")
    if _WIN:
        # Release zips bundle mesen_host.exe; this fallback is for source runs
        # with a MinGW-w64 toolchain on PATH.
        cmd = ["gcc", "-O2", "-o", HOST_BUILD, HOST_SRC, "-lws2_32"]
    else:
        cmd = ["cc", "-O2", "-o", HOST_BUILD, HOST_SRC, "-ldl", "-lpthread"]
    try:
        rc = subprocess.call(cmd)
    except FileNotFoundError:
        print("20xx: no C compiler found; use a release build or install "
              + ("MinGW-w64" if _WIN else "a C compiler"))
        return None
    if rc != 0:
        return None
    return HOST_BUILD if os.path.exists(HOST_BUILD) else None


def _mesen_patch_applied(dest: str) -> bool:
    f = os.path.join(dest, "InteropDLL", "EmuApiWrapper.cpp")
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            return "EnableCaptureRenderer" in fh.read()
    except OSError:
        return False


def build_core(dest: Optional[str] = None, *, jobs: Optional[int] = None,
               patch: Optional[str] = None, status=print) -> Optional[str]:
    """Clone Mesen2, apply the MMLC capture patch, and build MesenCore.so.

    Returns the built core path (or None). Linux/make only; on Windows build
    InteropDLL (x64) in Mesen.sln after applying the same patch."""
    dest = dest or MESEN2_DEFAULT_DIR
    patch = patch or MESEN2_PATCH
    jobs = jobs or (os.cpu_count() or 4)
    if sys.platform.startswith("win"):
        status("20xx: Windows: apply " + patch + " then build InteropDLL (x64) in Mesen.sln")
        return find_core(None)
    if not os.path.exists(patch):
        status(f"20xx: patch not found: {patch}")
        return None
    if not os.path.isdir(os.path.join(dest, ".git")):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        status(f"[20xx] cloning Mesen2 -> {dest}")
        subprocess.check_call(["git", "clone", "--depth", "1", MESEN2_REPO, dest])
    if _mesen_patch_applied(dest):
        status("[20xx] capture patch already applied")
    else:
        status(f"[20xx] applying {os.path.basename(patch)}")
        subprocess.check_call(["git", "apply", patch], cwd=dest)
    env = dict(os.environ)
    brew = "/home/linuxbrew/.linuxbrew"
    if os.path.isdir(os.path.join(brew, "include")):
        env.setdefault("CPATH", os.path.join(brew, "include"))
        env.setdefault("LIBRARY_PATH", os.path.join(brew, "lib"))
    status(f"[20xx] building Mesen2 core (make core STATICLINK=false -j{jobs}) ...")
    subprocess.check_call(["make", "core", "STATICLINK=false", "-j", str(jobs)],
                          cwd=dest, env=env)
    core = os.path.join(dest, "InteropDLL", "obj.linux-x64", "MesenCore.so")
    if os.path.exists(core):
        status(f"[20xx] built core: {core}")
        return core
    status("20xx: build finished but MesenCore.so not found")
    return None


def cmd_build_core(args: argparse.Namespace) -> int:
    core = build_core(getattr(args, "dir", None), jobs=getattr(args, "jobs", None),
                      status=lambda m: print(m, flush=True))
    if not core:
        return 1
    gd = args.game_dir
    if gd and os.path.isdir(gd):
        ini_set(gd, {"mesen": "1", "mesen_core": core})
        print(f"[20xx] configured mesen_core in {ini_path(gd)}")
    return 0


def install_proxy(game_dir: str) -> bool:
    """Install our proxy, keeping Valve's DLL as ``steam_api_orig.dll``.

    The original must be renamed *before* the proxy is copied in; the proxy
    forwards every export to ``steam_api_orig.dll``. If a proxy is already
    installed this is a no-op apart from refreshing the DLL.
    """
    import filecmp
    import shutil

    dest = os.path.join(game_dir, "steam_api.dll")
    orig = os.path.join(game_dir, "steam_api_orig.dll")

    if not os.path.exists(PROXY_BUILD):
        if not os.path.exists(PROXY_BUILD_SCRIPT):
            print("20xx: no proxy build script or prebuilt proxy")
            return False
        if not os.path.exists(orig) and not os.path.exists(dest):
            print("20xx: steam_api.dll not found in the install")
            return False
        print("20xx: building proxy (needs i686-w64-mingw32-gcc)...")
        env = dict(os.environ)
        env["OUT"] = PROXY_BUILD
        env["STEAM_API"] = orig if os.path.exists(orig) else dest
        try:
            rc = subprocess.call(["bash", PROXY_BUILD_SCRIPT], env=env)
        except FileNotFoundError:
            print("20xx: bash not found; install Git Bash/MSYS2 or build the "
                  "proxy on a machine that has mingw-w64")
            return False
        if rc != 0 or not os.path.exists(PROXY_BUILD):
            print("20xx: proxy build failed")
            return False

    if not os.path.exists(dest) and not os.path.exists(orig):
        print("20xx: steam_api.dll not found in the install; cannot install proxy")
        return False

    if os.path.exists(dest) and not os.path.exists(orig):
        try:
            if not filecmp.cmp(PROXY_BUILD, dest, shallow=False):
                os.replace(dest, orig)
                print(f"20xx: saved original DLL -> {os.path.basename(orig)}")
            else:
                print("20xx: warning: proxy is installed but no "
                      "steam_api_orig.dll backup exists")
        except OSError as ex:
            print(f"20xx: cannot back up steam_api.dll: {ex}")
            return False

    try:
        shutil.copy2(PROXY_BUILD, dest)
    except OSError as ex:
        print(f"20xx: cannot install proxy: {ex}")
        return False
    print(f"20xx: installed proxy -> {dest}")
    return True


def host_running(port: int = DEFAULT_PORT) -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
        s.close()
        return True
    except OSError:
        return False


def start_host(core: str, port: int = DEFAULT_PORT) -> bool:
    host = ensure_host()
    if not host:
        return False
    if host_running(port):
        print(f"20xx: host already listening on :{port}")
        return True
    log = os.path.join(BUILD_DIR, "mesen_host.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    lf = open(log, "ab")
    kwargs = {}
    if _WIN:
        # No console window; new process group so closing 20XX does not kill it.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([host, "--core", core, "--port", str(port)],
                     stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                     **kwargs)
    for _ in range(40):
        if host_running(port):
            print(f"20xx: host started on :{port} (core {os.path.basename(core)})")
            return True
        import time as _t
        _t.sleep(0.1)
    print(f"20xx: host did not come up; see {log}")
    return False


def cmd_mesen(args: argparse.Namespace) -> int:
    gd = args.game_dir
    if args.off:
        ini_set(gd, {"mesen": "0"})
        print("[20xx] Mesen replacer disabled (mesen=0).")
        return 0

    core = find_core(gd, args.core)
    if not core:
        print("[20xx] Mesen replacer status:")
        print(f"  mesen     = {ini_get(gd, 'mesen') or '0'}")
        print(f"  mesen_core= {ini_get(gd, 'mesen_core') or '(unset)'}")
        print(f"  enable with: 20xx mesen --core /path/to/MesenCore{'.dll' if _WIN else '.so'}")
        return 0
    if not check_core(core):
        return 1

    port = args.port or int(ini_get(gd, "mesen_port") or DEFAULT_PORT)
    ini_set(gd, {
        "mesen": "1",
        "mesen_host": "127.0.0.1",
        "mesen_port": str(port),
        "mesen_core": core,
        "mesen_defer_load": "1",
        "mesen_null_rom": "1",
    })
    # The in-engine randomizer and its Museum menu were removed; drop the keys.
    ini_remove(gd, ["randomize", "seed", "visualizer", "vanilla", "palette",
                    "menu_rb"])
    print(f"[20xx] Mesen replacer enabled (core={core}, port={port}).")
    if not args.no_install:
        ensure_proteus_unpacked(gd, getattr(args, "steamless", None))
        install_proxy(gd)
    if args.start:
        start_host(core, port)
    else:
        print("  start the host with: 20xx mesen --core %s --start" % core)
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    ok = ensure_proteus_unpacked(args.game_dir, args.steamless,
                                 force=args.force)
    return 0 if ok else 1


def _check_line(label: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
    mark = "ok" if ok else ("warn" if warn else "FAIL")
    print(f"  [{mark:4}] {label}: {detail}")


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print a setup readiness report without changing anything."""
    gd = args.game_dir
    if not setup_env.has_game_files(gd):
        found = setup_env.find_install()
        if found:
            print(f"[20xx] auto-detected install: {found}")
            gd = found
        else:
            print("[20xx] MMLC install not found. Candidates:")
            for p in setup_env.install_candidates():
                print(f"    {p}")
    print("20XX doctor")
    install_ok = setup_env.has_game_files(gd)
    _check_line("install", install_ok, gd or "(unset)")
    source = ""
    if install_ok:
        for name in ("Proteus.exe.orig.bak", "Proteus.exe"):
            p = os.path.join(gd, name)
            if os.path.exists(p):
                source = p
                break
    _check_line("ROM source", bool(source), os.path.basename(source) if source else "missing")

    packed = unpacked = False
    exe = os.path.join(gd, PROTEUS_NAME) if gd else ""
    if exe and os.path.exists(exe):
        try:
            packed = proteus_is_packed(exe)
            unpacked = proteus_is_unpacked(exe)
        except (OSError, ValueError):
            pass
    _check_line("Proteus.exe", unpacked and not packed,
                "unpacked" if unpacked and not packed else
                ("packed (run: 20xx setup)" if packed else "unknown"),
                warn=not (unpacked and not packed))

    try:
        sl = steamless.find_cli(getattr(args, "steamless", None))
        _check_line("Steamless", True, sl)
    except steamless.SteamlessError as ex:
        _check_line("Steamless", False, str(ex).splitlines()[0], warn=True)

    proxy_ok = install_ok and setup_env.proxy_installed(gd)
    _check_line("proxy", proxy_ok,
                "installed" if proxy_ok else "not installed (20xx setup)",
                warn=not proxy_ok)

    if install_ok:
        _, dpad_bad = normalize_input_map(read_input_map(gd))
        _check_line("D-pad mapping", not dpad_bad,
                    "ok" if not dpad_bad else
                    "duplicate directions (run: 20xx setup)", warn=True)

    core = find_core(gd, getattr(args, "core", None))
    core_ok = False
    if core:
        try:
            core_ok = check_core(core)
            _check_line("core", core_ok, core)
        except Exception as ex:  # noqa: BLE001 - doctor should not crash
            _check_line("core", False, f"{core} ({ex})")
    else:
        _check_line("core", False, "not found (20xx build-core / --core PATH)",
                    warn=True)

    host = _BUNDLED_HOST or (HOST_BUILD if os.path.exists(HOST_BUILD) else None)
    host_ok = bool(host)
    if not host:
        import shutil
        cc = shutil.which("cc") or shutil.which("gcc")
        host_ok = bool(cc and os.path.exists(HOST_SRC))
    _check_line("Mesen host", host_ok,
                host or ("will build from source" if host_ok else "missing"))

    roms = 0
    rdir = os.path.join(gd, "roms") if gd else ""
    if rdir and os.path.isdir(rdir):
        roms = len([n for n in os.listdir(rdir) if n.lower().endswith(".nes")])
    _check_line("extracted ROMs", roms > 0, f"{roms} file(s)", warn=roms == 0)

    if core_ok:
        port = int(ini_get(gd, "mesen_port") or DEFAULT_PORT)
        listening = host_running(port)
        _check_line("host status", listening,
                    f"listening on :{port}" if listening else "not running",
                    warn=True)

    ready = (install_ok and unpacked and not packed and proxy_ok
             and core_ok and host_ok)
    print("[20xx] " + ("ready to play through Mesen" if ready else
                       "not ready - run: 20xx setup"))
    return 0 if ready else 1


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(prompt + " [Y/n] ").strip().lower() not in ("n", "no")
    except EOFError:
        return False


def cmd_setup(args: argparse.Namespace) -> int:
    """Automatic setup: find the install, unpack, install proxy, locate/build
    a core, configure the host, extract ROMs and verify them."""
    gd = args.game_dir
    if not setup_env.has_game_files(gd):
        found = setup_env.find_install()
        if found:
            print(f"[20xx] auto-detected install: {found}")
            gd = found
        else:
            print("20xx: MMLC install not found; pass --game-dir PATH")
            return 1

    try:
        src = find_source(gd)
    except SystemExit as ex:
        print(ex)
        return 1
    print(f"[20xx] install: {gd}")
    print(f"[20xx] ROM source: {os.path.basename(src)}")

    if repair_input_map(gd):
        print("[20xx] repaired a duplicate D-pad mapping in mmlc.ini")

    unpack_ok = ensure_proteus_unpacked(gd, getattr(args, "steamless", None),
                                        status=lambda m: print(m, flush=True))
    if not unpack_ok:
        print("20xx: could not unpack Proteus.exe; proxy hooks will not load")

    proxy_ok = install_proxy(gd)
    if not proxy_ok:
        print("20xx: proxy missing; Mesen mode will not work until it is built")

    core = find_core(gd, getattr(args, "core", None))
    if not core and not args.no_core_build:
        if _WIN:
            print("20xx: Windows core: apply tools/mesen_host/mesen2_capture.patch")
            print("      then build InteropDLL (x64) in Mesen.sln, or pass --core "
                  "C:\\path\\MesenCore.dll")
        elif _confirm("No Mesen core found. Build Mesen2 now (clone + make, "
                      "several minutes)?", args.yes):
            try:
                core = build_core(status=lambda m: print(m, flush=True))
            except Exception as ex:  # noqa: BLE001 - report build failures
                print(f"20xx: core build failed: {ex}")
                print("      install the Mesen2 build dependencies and retry, "
                      "or pass --core PATH")
    core_ok = bool(core) and check_core(core)
    if core and not core_ok:
        print("20xx: core is present but lacks the MMLC hooks; not enabling it")
        print("      rebuild with tools/mesen_host/mesen2_capture.patch applied")

    if core and core_ok:
        vals = {
            "mesen": "1",
            "mesen_host": "127.0.0.1",
            "mesen_port": str(DEFAULT_PORT),
            "mesen_core": core,
            "mesen_defer_load": "1",
            "mesen_null_rom": "1",
        }
        # Preserve an existing overclock/port choice.
        if ini_get(gd, "mesen_port"):
            vals.pop("mesen_port")
        ini_set(gd, vals)
        if ini_get(gd, "mesen_overclock_follow") is None:
            ini_set(gd, {"mesen_overclock_follow": "1",
                         "mesen_overclock_turbo": "262"})
        print(f"[20xx] configured mesen_core = {core}")

    host = ensure_host()
    host_ok = bool(host)
    if host_ok:
        print(f"[20xx] Mesen host ready: {host}")

    if not args.no_extract:
        written = extract(gd, os.path.join(gd, "roms"), None, None, quiet=True)
        print(f"[20xx] extracted {len(written)} ROM(s) -> {os.path.join(gd, 'roms')}")
        bad = 0
        pe = PE(src)
        for key in GAME_ORDER:
            g = romtable.GAMES[key]
            crc = patcher.crc32(patcher.read_prg(pe, g))
            want = romtable.ORIGINAL_PRG_CRC.get(key)
            if want is not None and crc != want:
                bad += 1
                print(f"  {key:4} CRC {crc:#010x} != {want:#010x} "
                      "(modified install? run Verify)")
        if not bad:
            print(f"[20xx] ROM CRCs match ({len(GAME_ORDER)}/{len(GAME_ORDER)})")

    if args.start and core_ok:
        start_host(core)
    elif args.start:
        print("20xx: --start needs a working core")

    ready = unpack_ok and proxy_ok and core_ok and host_ok
    print("[20xx] setup " + ("complete - ready to play through Mesen"
                             if ready else
                             "finished with missing pieces (see above)"))
    return 0 if ready else 1


def cmd_overclock(args: argparse.Namespace) -> int:
    """Configure the NES overclock. By default it follows MMLC's in-game
    CPU SPEED option (ORIGINAL/TURBO); --scanlines pins an explicit value."""
    gd = args.game_dir
    vals: Dict[str, str] = {}
    if args.off:
        vals = {"mesen_overclock_follow": "0", "mesen_overclock": "0"}
    else:
        if args.follow is not None:
            vals["mesen_overclock_follow"] = "1" if args.follow else "0"
        if args.scanlines is not None:
            if args.scanlines < 0:
                print("20xx: --scanlines must be >= 0")
                return 2
            vals["mesen_overclock"] = str(args.scanlines)
        if args.turbo is not None:
            if args.turbo < 0:
                print("20xx: --turbo must be >= 0")
                return 2
            vals["mesen_overclock_turbo"] = str(args.turbo)
    if vals:
        ini_set(gd, vals)
    print("[20xx] overclock settings:")
    for k in ("mesen_overclock_follow", "mesen_overclock",
              "mesen_overclock_turbo"):
        print(f"  {k} = {ini_get(gd, k) or '(default)'}")
    print("  (restart the game for changes to take effect)")
    return 0


def cmd_states(args: argparse.Namespace) -> int:
    """Configure the Mesen save/load-state and reset hotkeys (handy for
    ROM-hack testing). Defaults: LB+Y save, LB+B load, LB+Start reset,
    LB+X turbo (hold), slot 0."""
    gd = args.game_dir
    vals: Dict[str, str] = {}
    off = {"off", "none", ""}

    def combo(flag):
        return "0" if flag.lower() in off else flag

    if args.save_button is not None:
        vals["state_save_button"] = combo(args.save_button)
    if args.load_button is not None:
        vals["state_load_button"] = combo(args.load_button)
    if args.reset_button is not None:
        vals["state_reset_button"] = combo(args.reset_button)
    if getattr(args, "turbo_button", None) is not None:
        vals["state_turbo_button"] = combo(args.turbo_button)
    if args.slot is not None:
        vals["state_slot"] = str(args.slot)
    if vals:
        ini_set(gd, vals)
    print("[20xx] save-state hotkeys (restart the game to apply):")
    for k in ("state_save_button", "state_load_button", "state_reset_button",
              "state_turbo_button", "state_slot"):
        print(f"  {k} = {ini_get(gd, k) or '(default)'}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    print(f"{APP} v{VERSION}")
    for k in GAME_ORDER:
        g = romtable.GAMES[k]
        chr_s = f"{g.chr.size:#x}" if g.chr else "-"
        print(f"  {k:4} {g.title:20} {g.region:2} mapper={g.ines_header[6] >> 4:#x} "
              f"prg={g.prg.size:#x} chr={chr_s}")
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(f"{APP} v{VERSION}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    return verify(args.game_dir, args.source)


def cmd_extract(args: argparse.Namespace) -> int:
    out = args.out or os.path.join(args.game_dir, "roms")
    extract(args.game_dir, out, args.game, args.source)
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    """Launch the collection from the terminal (or just print the command)."""
    if args.print_only:
        print(f"steam -applaunch {args.appid}")
        return 0
    launch(args.appid)
    return 0


def _split_targets(target) -> "set[str]":
    """Normalize a target spec to a set of game keys.

    Accepts ``"all"``, a single key (``"mm1"``), a comma/space list
    (``"mm1,rk1"``) or an iterable of keys. Used by ``prepare_roms`` /
    ``export_roms`` so a tab can patch a US/JP pair in one pass.
    """
    if isinstance(target, (set, list, tuple, frozenset)):
        keys = {str(t).strip() for t in target if str(t).strip()}
    else:
        keys = {t for t in str(target or "all").replace(",", " ").split() if t}
    return keys or {"all"}


def prepare_roms(game_dir: str, out: str, *, source: Optional[str] = None,
                 target="all", seed: Optional[str] = None,
                 weakness: bool = True, weapons: bool = True,
                 palette: bool = True, visualizer: bool = False,
                 victory_lap: bool = False, door_palette: bool = False,
                 vanilla_wily: bool = False, cursor_tint: bool = False,
                 hard_mode: bool = False, ohko: bool = False,
                 romhacks: Optional[List[str]] = None,
                 ips_adjust: int = 0, auto_romhacks: bool = True) -> None:
    """Patch `target` (a game key, a comma list like ``"mm1,rk1"``, or
    'all') with the options and write every game's .nes (vanilla for the
    rest) so any pick in the collection works."""
    targets = _split_targets(target)
    src = find_source(game_dir, source)
    pe = PE(src)
    os.makedirs(out, exist_ok=True)
    for k in GAME_ORDER:
        g = romtable.GAMES[k]
        opts = {}
        if "all" in targets or k in targets:
            hacks = list(romhacks or [])
            if auto_romhacks:
                hacks = discover_romhacks(game_dir, k) + hacks
            opts = dict(seed=(seed if k in RANDO_GAMES else None),
                        weakness=weakness, weapons=weapons, palette=palette,
                        visualizer=visualizer, victory_lap=victory_lap,
                        door_palette=door_palette, vanilla_wily=vanilla_wily,
                        cursor_tint=cursor_tint, hard_mode=hard_mode,
                        ohko=ohko, romhacks=hacks,
                        ips_adjust=ips_adjust)
        write_file_atomic(os.path.join(out, f"{k}.nes"),
                          build_patched_nes(g, pe, **opts))
    print(f"[20xx] prepared ROMs in {out}")


# Friendly filenames for exporting to the user's own emulators.
REGION_TAG = {"US": "USA", "JP": "Japan"}


def export_roms(game_dir: str, dest: str, *, source: Optional[str] = None,
                target="all", seed: Optional[str] = None,
                weakness: bool = True, weapons: bool = True,
                palette: bool = True, visualizer: bool = False,
                victory_lap: bool = False, door_palette: bool = False,
                vanilla_wily: bool = False, cursor_tint: bool = False,
                hard_mode: bool = False, ohko: bool = False,
                romhacks: Optional[List[str]] = None,
                ips_adjust: int = 0, auto_romhacks: bool = True,
                quiet: bool = False) -> Dict[str, str]:
    """Extract the 12 ROMs (patched where selected) to *dest* with friendly
    filenames, for use in the user's own emulators. Returns key -> path.

    Same options as :func:`prepare_roms`: `target` is a game key, a comma
    list, or 'all'; games other than the target are exported vanilla."""
    targets = _split_targets(target)
    src = find_source(game_dir, source)
    pe = PE(src)
    os.makedirs(dest, exist_ok=True)
    written: Dict[str, str] = {}
    for k in GAME_ORDER:
        g = romtable.GAMES[k]
        opts = {}
        if "all" in targets or k in targets:
            hacks = list(romhacks or [])
            if auto_romhacks:
                hacks = discover_romhacks(game_dir, k) + hacks
            opts = dict(seed=(seed if k in RANDO_GAMES else None),
                        weakness=weakness, weapons=weapons, palette=palette,
                        visualizer=visualizer, victory_lap=victory_lap,
                        door_palette=door_palette, vanilla_wily=vanilla_wily,
                        cursor_tint=cursor_tint, hard_mode=hard_mode,
                        ohko=ohko, romhacks=hacks,
                        ips_adjust=ips_adjust)
        name = f"{g.title} ({REGION_TAG.get(g.region, g.region)}).nes"
        out = os.path.join(dest, name)
        write_file_atomic(out, build_patched_nes(g, pe, **opts))
        written[k] = out
    if not quiet:
        for k in GAME_ORDER:
            print(f"  {os.path.basename(written[k])}")
        print(f"[20xx] exported {len(written)} ROM(s) to {dest}")
    return written


def ensure_mesen_host(game_dir: str) -> bool:
    """Start the Mesen host if the replacer is enabled (auto-detecting a core)."""
    if ini_get(game_dir, "mesen") != "1":
        print("[20xx] Mesen replacer is off; enable with: 20xx mesen --core PATH")
        return False
    ensure_proteus_unpacked(game_dir, status=lambda m: print(m, flush=True))
    core = find_core(game_dir)
    if not core:
        print("[20xx] no Mesen core found; run: 20xx mesen --core "
              + ("C:\\path\\MesenCore.dll" if _WIN else "/path/MesenCore.so"))
        return False
    if ini_get(game_dir, "mesen_core") != core:
        ini_set(game_dir, {"mesen_core": core})
        print(f"[20xx] using Mesen core: {core}")
    if not check_core(core):
        return False
    port = int(ini_get(game_dir, "mesen_port") or DEFAULT_PORT)
    return start_host(core, port)


def launch_collection(game_dir: str, appid: str, *, ensure_host: bool = True) -> None:
    """Launch the collection (starting the Mesen host if enabled). The player
    picks the game in the collection's menu."""
    if ensure_host:
        ensure_mesen_host(game_dir)
    launch(appid)


_TERMINALS = [
    ("konsole", ["-e"]),
    ("xterm", ["-e"]),
    ("alacritty", ["-e"]),
    ("kitty", ["-e"]),
    ("xfce4-terminal", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("foot", []),
    ("wezterm", ["start", "--"]),
]


def find_terminal():
    """Return (program, prefix_args) for a terminal emulator, or None."""
    import shutil
    for name, pre in _TERMINALS:
        if shutil.which(name):
            return name, pre
    return None


def do_launch(game_dir: str, appid: str, launch_cmd=None) -> None:
    """Start the Mesen host, then either exec `launch_cmd` (Steam prelaunch) or
    ask Steam to launch the collection."""
    if launch_cmd:
        ensure_mesen_host(game_dir)
        print(f"[20xx] exec {' '.join(launch_cmd)}")
        try:
            os.execvp(launch_cmd[0], list(launch_cmd))
        except OSError as ex:
            print(f"20xx: exec failed: {ex}", file=sys.stderr)
        return
    launch_collection(game_dir, appid)


def cmd_patch(args: argparse.Namespace) -> int:
    _apply_palette_only(args)
    seed = _seed_arg(args)
    out = args.out or os.path.join(args.game_dir, "roms")
    targets = _split_targets(args.game)
    keys = GAME_ORDER if "all" in targets else [k for k in GAME_ORDER if k in targets]
    for k in keys:
        patch_game(args.game_dir, out, k, source=args.source, seed=seed,
                   weakness=args.weakness, weapons=args.weapons, palette=args.palette,
                   visualizer=args.visualizer, victory_lap=args.victory_lap,
                   door_palette=args.door_palette, vanilla_wily=args.vanilla_wily,
                   cursor_tint=args.cursor_tint, hard_mode=args.hard_mode,
                   ohko=args.ohko, romhacks=args.romhack,
                   ips_adjust=args.ips_adjust, auto_romhacks=not args.no_romhacks)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export the ROMs (patched where selected) for the user's own emulators."""
    dest = args.dest or os.path.expanduser("~/MMLC-ROMs")
    if args.vanilla:
        seed = None
        weakness = weapons = palette = visualizer = False
        victory_lap = door_palette = vanilla_wily = cursor_tint = False
        hard_mode = False
        ohko = False
    else:
        _apply_palette_only(args)
        seed = _seed_arg(args)
        weakness, weapons, palette = args.weakness, args.weapons, args.palette
        visualizer = args.visualizer
        victory_lap, door_palette = args.victory_lap, args.door_palette
        vanilla_wily = args.vanilla_wily
        cursor_tint = args.cursor_tint
        hard_mode = args.hard_mode
        ohko = args.ohko
    export_roms(args.game_dir, dest, source=args.source, target=args.game,
                seed=seed, weakness=weakness, weapons=weapons, palette=palette,
                visualizer=visualizer, victory_lap=victory_lap,
                door_palette=door_palette, vanilla_wily=vanilla_wily,
                cursor_tint=cursor_tint, hard_mode=hard_mode, ohko=ohko,
                romhacks=args.romhack,
                ips_adjust=args.ips_adjust, auto_romhacks=not args.no_romhacks)
    return 0


def cmd_romhacks(args: argparse.Namespace) -> int:
    """List (and create) the drop-in romhacks folder."""
    root = ensure_romhacks_dir(args.game_dir)
    print(f"20xx: romhacks folder: {root}")
    games = [args.game] if args.game and args.game != "all" else GAME_ORDER
    found = 0
    for k in games:
        hacks = discover_romhacks(args.game_dir, k)
        if hacks:
            found += len(hacks)
            for h in hacks:
                print(f"  {k:4} {os.path.basename(h)}")
    if not found:
        print("  (none yet) - drop .ips/.bps into a game folder above")
    return 0


def cmd_prelaunch(args: argparse.Namespace) -> int:
    """Steam launch-options hook. Runs before Proteus.exe.

    Steam -> Properties -> Launch Options:
        /path/to/20xx prelaunch %command%

    With no controlling terminal (Steam launch), it opens a terminal so you can
    pick options interactively; the chosen ROMs are prepared, the Mesen host is
    started, and finally the original `%command%` (Proteus) is exec'd.
    """
    cmd = list(args.command or [])
    if not cmd:
        print("20xx: prelaunch: no command (use: 20xx prelaunch %command%)")
        return 2

    if not sys.stdin.isatty():
        term = find_terminal()
        if term:
            name, pre = term
            inner = [sys.executable, os.path.abspath(__file__),
                     "--game-dir", args.game_dir, "prelaunch"] + cmd
            print(f"[20xx] prelaunch: opening {name} for interactive selection")
            try:
                return subprocess.call([name] + pre + inner)
            except OSError as ex:
                print(f"20xx: {name} failed: {ex}; preparing non-interactively",
                      file=sys.stderr)
        else:
            print("[20xx] prelaunch: no terminal found; preparing non-interactively")
        # Fallback: non-interactive prepare + exec.
        gd = args.game_dir
        out = args.out or os.path.join(gd, "roms")
        ensure_romhacks_dir(gd)
        prepare_roms(gd, out, source=args.source, target="all")
        do_launch(gd, args.appid, cmd)
        return 0

    # We have a TTY: run the interactive menu, then hand off to `cmd`.
    return interactive(args, launch_cmd=cmd)


def cmd_play(args: argparse.Namespace) -> int:
    _apply_palette_only(args)
    if args.romhack and args.game == "all":
        print("20xx: --romhack needs a specific game "
              "(e.g. 20xx play mm1 --romhack hack.ips)")
        return 2
    seed = _seed_arg(args)
    out = args.out or os.path.join(args.game_dir, "roms")
    prepare_roms(args.game_dir, out, source=args.source, target=args.game,
                 seed=seed, weakness=args.weakness, weapons=args.weapons,
                 palette=args.palette, visualizer=args.visualizer,
                 victory_lap=args.victory_lap, door_palette=args.door_palette,
                 vanilla_wily=args.vanilla_wily, cursor_tint=args.cursor_tint,
                 hard_mode=args.hard_mode, ohko=args.ohko,
                 romhacks=args.romhack, ips_adjust=args.ips_adjust,
                 auto_romhacks=not args.no_romhacks)
    if args.no_launch:
        print("[20xx] --no-launch: not starting the collection")
        return 0
    launch_collection(args.game_dir, args.appid)
    return 0


# --- GUI (Tkinter setup + options + launch wizard) --------------------------

GUI_MODES = [
    "Vanilla",
    "ROM hack only",
    "Randomized",
    "Palette shuffle only",
]


def open_folder(path: str) -> None:
    """Open a folder/file in the OS file manager."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as ex:
        print(f"20xx: could not open {path}: {ex}")


# NES button -> XInput button mapping. Names match `parse_button` in the proxy.
INPUT_CHOICES = ["a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs",
                 "dpad_up", "dpad_down", "dpad_left", "dpad_right", "guide"]
INPUT_LABELS = {
    "btn_a": "A", "btn_b": "B", "btn_select": "Select", "btn_start": "Start",
    "btn_up": "Up", "btn_down": "Down", "btn_left": "Left", "btn_right": "Right",
}
# Defaults match the collection's layout (NES B = Xbox X).
DEFAULT_INPUT_MAP = {
    "btn_a": "a", "btn_b": "x", "btn_select": "rb", "btn_start": "start",
    "btn_up": "dpad_up", "btn_down": "dpad_down",
    "btn_left": "dpad_left", "btn_right": "dpad_right",
}

# Save/load-state hotkeys (Mesen): a modifier + a button ("lb+y").
STATE_MODIFIERS = ["lb", "rb", "back", "start", "ls", "rs", "guide", "(none)"]
STATE_BUTTONS = ["a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs",
                 "dpad_up", "dpad_down", "dpad_left", "dpad_right", "off"]
DEFAULT_SAVE_STATE = "lb+y"
DEFAULT_LOAD_STATE = "lb+b"
DEFAULT_RESET_STATE = "lb+start"
DEFAULT_TURBO_STATE = "lb+x"


def split_state_combo(s: str):
    """'lb+y' -> ('lb', 'y'); '0'/'off'/'' -> ('(none)', 'off')."""
    s = (s or "").strip()
    if not s or s.lower() in ("0", "off", "none"):
        return "(none)", "off"
    parts = s.split("+")
    if len(parts) == 1:
        return "(none)", parts[0]
    return parts[0], "+".join(parts[1:])


def join_state_combo(mod: str, btn: str) -> str:
    if not btn or btn == "off":
        return "0"
    mod = "" if mod in ("(none)", "") else mod
    return f"{mod}+{btn}" if mod else btn


DPAD_DIRECTIONS = ("btn_up", "btn_down", "btn_left", "btn_right")


def normalize_input_map(mapping: dict):
    """Return (map, repaired).

    A duplicated D-pad direction is always a misconfiguration (e.g. Up and
    Left both bound to ``dpad_left``), so repair those four to the defaults.
    Other bindings are preserved, including intentional face-button choices.
    """
    m = dict(DEFAULT_INPUT_MAP)
    for k in INPUT_LABELS:
        v = (mapping or {}).get(k)
        if v:
            m[k] = str(v)
    directions = [m[k] for k in DPAD_DIRECTIONS]
    if len(set(directions)) != len(directions):
        for k in DPAD_DIRECTIONS:
            m[k] = DEFAULT_INPUT_MAP[k]
        return m, True
    return m, False


def read_input_map(game_dir: str) -> dict:
    return {k: ini_get(game_dir, k) or DEFAULT_INPUT_MAP[k]
            for k in INPUT_LABELS}


def write_input_map(game_dir: str, mapping: dict) -> None:
    """Persist btn_* values into mmlc.ini (the proxy reads them at load)."""
    fixed, _ = normalize_input_map(mapping)
    ini_set(game_dir, fixed)


def repair_input_map(game_dir: str) -> bool:
    """Rewrite mmlc.ini if its D-pad directions collide. Returns True if fixed."""
    fixed, changed = normalize_input_map(read_input_map(game_dir))
    if changed:
        write_input_map(game_dir, fixed)
    return changed


def gui_config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "20xx", "config.json")


def gui_config_load() -> dict:
    try:
        with open(gui_config_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def gui_config_save(d: dict) -> None:
    p = gui_config_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except OSError:
        pass


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog
        from tkinter.scrolledtext import ScrolledText
    except Exception as ex:  # pragma: no cover - environment dependent
        print(f"20xx: Tk unavailable ({ex}); falling back to CLI", file=sys.stderr)
        return interactive(args)

    import queue
    import threading

    from mmlc import collections as colmod

    cfg = gui_config_load()
    logq: "queue.Queue[str]" = queue.Queue()

    root = tk.Tk()
    root.title("20XX")
    root.geometry("900x760")
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass

    # --- Global state -------------------------------------------------------
    core = tk.StringVar(value=cfg.get("core") or "")
    steamless_cli = tk.StringVar(value=cfg.get("steamless") or "")
    legacy_dir = cfg.get("game_dir") or args.game_dir or game_dir_default()
    collections = colmod.normalize(cfg.get("collections"), legacy_dir,
                                   cfg.get("appid", DEFAULT_APPID))
    active_id = cfg.get("collection_id") or collections[0]["id"]
    if not any(c["id"] == active_id for c in collections):
        active_id = collections[0]["id"]
    state = {"collections": collections}
    active = tk.StringVar(value=active_id)
    game_dir = tk.StringVar(value=next(
        (c["game_dir"] for c in collections if c["id"] == active_id),
        legacy_dir))

    oc_follow = tk.BooleanVar(value=bool(cfg.get("oc_follow", True)))
    oc_turbo = tk.StringVar(value=str(cfg.get("oc_turbo", 262)))
    auto_build_core = tk.BooleanVar(value=bool(cfg.get("auto_build_core", False)))
    input_vars: dict = {}
    saved_map = cfg.get("input_map", DEFAULT_INPUT_MAP)
    for _key in INPUT_LABELS:
        input_vars[_key] = tk.StringVar(value=saved_map.get(_key, DEFAULT_INPUT_MAP[_key]))
    sv_mod, sv_btn = split_state_combo(
        cfg.get("state_save_button", DEFAULT_SAVE_STATE))
    ld_mod, ld_btn = split_state_combo(
        cfg.get("state_load_button", DEFAULT_LOAD_STATE))
    rs_mod, rs_btn = split_state_combo(
        cfg.get("state_reset_button", DEFAULT_RESET_STATE))
    tb_mod, tb_btn = split_state_combo(
        cfg.get("state_turbo_button", DEFAULT_TURBO_STATE))
    save_mod, save_btn = tk.StringVar(value=sv_mod), tk.StringVar(value=sv_btn)
    load_mod, load_btn = tk.StringVar(value=ld_mod), tk.StringVar(value=ld_btn)
    reset_mod, reset_btn = tk.StringVar(value=rs_mod), tk.StringVar(value=rs_btn)
    turbo_mod, turbo_btn = tk.StringVar(value=tb_mod), tk.StringVar(value=tb_btn)
    state_slot = tk.StringVar(value=str(cfg.get("state_slot", 0)))

    # --- Navigation chrome --------------------------------------------------
    header = ttk.Frame(root)
    header.pack(fill="x", padx=10, pady=(8, 0))
    ttk.Label(header, text="20XX", font=("Helvetica", 16, "bold")).pack(side="left")
    crumb = tk.StringVar(value="Collections")
    ttk.Label(header, textvariable=crumb, foreground="#6a6a6a").pack(
        side="left", padx=(10, 0))
    ttk.Button(header, text="Settings",
               command=lambda: show_settings()).pack(side="right")
    ttk.Button(header, text="Collections",
               command=lambda: show_collections()).pack(side="right", padx=6)

    view = ttk.Frame(root)
    view.pack(fill="both", expand=True, padx=0, pady=(4, 0))
    canvas = tk.Canvas(view, highlightthickness=0)
    vsb = ttk.Scrollbar(view, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    body = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda _e: canvas.configure(
        scrollregion=canvas.bbox("all")))

    log = ScrolledText(root, height=8, state="disabled", font=("monospace", 9))
    log.pack(fill="x", padx=10, pady=(4, 8))

    def append(msg):
        log.configure(state="normal")
        log.insert("end", str(msg) + "\n")
        log.see("end")
        log.configure(state="disabled")

    def pump():
        try:
            while True:
                append(logq.get_nowait())
        except queue.Empty:
            pass
        root.after(100, pump)

    class _QWriter:
        def __init__(self):
            self.buf = ""

        def write(self, s):
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                logq.put(line)

        def flush(self):
            if self.buf:
                logq.put(self.buf)
                self.buf = ""

    def run(task):
        def wrapper():
            w = _QWriter()
            old = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = w
            try:
                task()
            except Exception as ex:
                import traceback
                logq.put(f"ERROR: {ex}")
                logq.put(traceback.format_exc())
            finally:
                w.flush()
                sys.stdout, sys.stderr = old
        threading.Thread(target=wrapper, daemon=True).start()

    def clear_page():
        for w in body.winfo_children():
            w.destroy()
        canvas.yview_moveto(0)

    def page_title(title, subtitle=""):
        ttk.Label(body, text=title, font=("Helvetica", 13, "bold")).pack(
            anchor="w", padx=12, pady=(8, 0))
        if subtitle:
            ttk.Label(body, text=subtitle, foreground="#555").pack(
                anchor="w", padx=12, pady=(0, 6))

    def active_col() -> dict:
        for c in state["collections"]:
            if c["id"] == active.get():
                return c
        return state["collections"][0]

    def save_config():
        c = active_col()
        c["game_dir"] = game_dir.get().strip() or c["game_dir"]
        cfg2 = dict(gui_config_load())
        cfg2.update({
            "collections": state["collections"],
            "collection_id": active.get(),
            "game_dir": game_dir.get().strip(),
            "appid": c.get("appid") or DEFAULT_APPID,
            "core": core.get().strip(),
            "steamless": steamless_cli.get().strip(),
            "oc_follow": bool(oc_follow.get()),
            "oc_turbo": oc_turbo.get(),
            "auto_build_core": bool(auto_build_core.get()),
        })
        gui_config_save(cfg2)

    def use_collection(c: dict):
        active.set(c["id"])
        game_dir.set(c["game_dir"])
        save_config()

    def input_map_values() -> dict:
        m, repaired = normalize_input_map(
            {k: v.get() for k, v in input_vars.items()})
        if repaired:
            for k, v in m.items():
                input_vars[k].set(v)
            logq.put("[20xx] duplicate D-pad directions reset to defaults")
        return m

    # --- Collection page ----------------------------------------------------
    def show_collections():
        clear_page()
        crumb.set("Collections")
        page_title("Choose a collection",
                   "Each collection has its own install folder and game list.")
        for c in state["collections"]:
            fr = ttk.LabelFrame(body, text=c["name"])
            fr.pack(fill="x", padx=12, pady=6)
            path = c["game_dir"] or "(no folder set)"
            ttk.Label(fr, text=path, wraplength=760).grid(
                row=0, column=0, sticky="w", padx=8, pady=(6, 0))
            kind = colmod.KIND_LABELS.get(c["kind"], c["kind"])
            n = len(colmod.games(c))
            status = f"{kind} — {n} game(s)" if n else f"{kind} — no games yet"
            if c["kind"] == colmod.KIND_MMLC1 and not colmod.has_rom_source(
                    c["game_dir"]):
                status += "  (install not found)"
            ttk.Label(fr, text=status, foreground="#666").grid(
                row=1, column=0, sticky="w", padx=8)
            bf = ttk.Frame(fr)
            bf.grid(row=2, column=0, sticky="w", padx=6, pady=(4, 6))
            fr.columnconfigure(0, weight=1)
            ttk.Button(bf, text="Open",
                       command=lambda c=c: open_collection(c)).pack(side="left")
            ttk.Button(bf, text="Edit",
                       command=lambda c=c: collection_dialog(c)).pack(
                           side="left", padx=4)
            ttk.Button(bf, text="Remove",
                       command=lambda c=c: remove_collection(c)).pack(side="left")
        bf = ttk.Frame(body)
        bf.pack(fill="x", padx=12, pady=10)
        ttk.Button(bf, text="+ Add collection",
                   command=lambda: collection_dialog(None)).pack(side="left")
        ttk.Button(bf, text="Automatic setup",
                   command=lambda: run(setup_task)).pack(side="left", padx=6)
        ttk.Label(bf, text="More collections can be registered here; only "
                           "MMLC1 has a game table so far.",
                  foreground="#666").pack(side="left", padx=8)

    def open_collection(c: dict):
        use_collection(c)
        show_games(c)

    def remove_collection(c: dict):
        state["collections"] = [x for x in state["collections"] if x["id"] != c["id"]]
        if not state["collections"]:
            state["collections"] = colmod.normalize([], game_dir.get(),
                                                    c.get("appid", DEFAULT_APPID))
        if active.get() == c["id"]:
            active.set(state["collections"][0]["id"])
            game_dir.set(state["collections"][0]["game_dir"])
        save_config()
        show_collections()

    def collection_dialog(existing):
        top = tk.Toplevel(root)
        top.title("Edit collection" if existing else "Add collection")
        top.transient(root)
        top.grab_set()
        initial_path = (existing or {}).get(
            "game_dir", game_dir.get() or "") or ""
        initial_kind = (existing or {}).get("kind") or colmod.detect_kind(
            initial_path)
        default_name = (existing or {}).get("name") or (
            os.path.basename(initial_path.rstrip("/\\")) if initial_path else "")
        name = tk.StringVar(value=default_name)
        path = tk.StringVar(value=initial_path)
        labels = list(colmod.KIND_LABELS.values())
        kind_label = tk.StringVar(value=colmod.KIND_LABELS.get(
            initial_kind, colmod.KIND_LABELS[colmod.KIND_UNKNOWN]))
        appid = tk.StringVar(value=(existing or {}).get("appid", DEFAULT_APPID))

        top.geometry("+%d+%d" % (root.winfo_rootx() + 100,
                                 root.winfo_rooty() + 80))
        ttk.Label(top, text="Name").grid(row=0, column=0, sticky="e", padx=8, pady=6)
        ttk.Entry(top, textvariable=name, width=42).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=4)
        ttk.Label(top, text="Folder").grid(row=1, column=0, sticky="e", padx=8)
        ttk.Entry(top, textvariable=path, width=42).grid(
            row=1, column=1, sticky="ew", padx=4)

        def browse():
            d = filedialog.askdirectory(parent=top, initialdir=path.get() or
                                        os.path.expanduser("~"))
            if d:
                path.set(d)
                if not name.get().strip():
                    name.set(os.path.basename(d.rstrip("/")) or d)
                if colmod.detect_kind(d) == colmod.KIND_MMLC1:
                    kind_label.set(colmod.KIND_LABELS[colmod.KIND_MMLC1])

        ttk.Button(top, text="Browse", command=browse).grid(row=1, column=2, padx=4)
        ttk.Label(top, text="Type").grid(row=2, column=0, sticky="e", padx=8, pady=6)
        ttk.Combobox(top, textvariable=kind_label, values=labels,
                     state="readonly", width=38).grid(
                         row=2, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Label(top, text="Steam appid").grid(row=3, column=0, sticky="e", padx=8)
        ttk.Entry(top, textvariable=appid, width=12).grid(
            row=3, column=1, sticky="w", padx=4)
        hint = ttk.Label(top, text="Only MMLC1 collections expose games today; "
                                   "other types are kept as placeholders.",
                         foreground="#666")
        hint.grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=4)

        def save():
            kind = next((k for k, v in colmod.KIND_LABELS.items()
                         if v == kind_label.get()), colmod.KIND_UNKNOWN)
            nm = name.get().strip() or colmod.KIND_LABELS[kind]
            if existing:
                existing.update(name=nm, game_dir=path.get().strip(), kind=kind,
                                appid=appid.get().strip() or DEFAULT_APPID)
                if existing["id"] == active.get():
                    game_dir.set(existing["game_dir"])
            else:
                taken = {c["id"] for c in state["collections"]}
                state["collections"].append({
                    "id": colmod.new_id(nm, taken), "name": nm,
                    "game_dir": path.get().strip(), "kind": kind,
                    "appid": appid.get().strip() or DEFAULT_APPID,
                })
            save_config()
            top.destroy()
            show_collections()

        bf = ttk.Frame(top)
        bf.grid(row=5, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        ttk.Button(bf, text="Cancel", command=top.destroy).pack(side="right")
        ttk.Button(bf, text="Save", command=save).pack(side="right", padx=6)
        top.columnconfigure(1, weight=1)

    # --- Game list page -----------------------------------------------------
    def show_games(c: dict):
        use_collection(c)
        clear_page()
        crumb.set(f"Collections / {c['name']}")
        page_title(c["name"], "Choose a game.")
        ttk.Button(body, text="< Collections",
                   command=show_collections).pack(anchor="w", padx=12)
        keys = colmod.games(c)
        if not keys:
            ttk.Label(body, text="This collection has no game table yet.",
                      foreground="#a33").pack(anchor="w", padx=12, pady=12)
            ttk.Button(body, text="Edit collection",
                       command=lambda: collection_dialog(c)).pack(
                           anchor="w", padx=12)
            return
        grid = ttk.Frame(body)
        grid.pack(fill="x", padx=12, pady=8)
        ttk.Label(grid, text="#", width=3).grid(row=0, column=0)
        ttk.Label(grid, text="Mega Man (US)").grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="Rockman (JP)").grid(row=0, column=2, sticky="w")
        for i in range(6):
            us, jp = f"mm{i + 1}", f"rk{i + 1}"
            ttk.Label(grid, text=str(i + 1), width=3).grid(row=i + 1, column=0)
            for col, key in ((1, us), (2, jp)):
                if key not in keys:
                    continue
                g = romtable.GAMES[key]
                label = g.title
                if key in RANDO_GAMES:
                    label += "  *"
                ttk.Button(grid, text=label, width=24,
                           command=lambda k=key: show_game(c, k)).grid(
                               row=i + 1, column=col, sticky="w", pady=2)
        ttk.Label(body, text="* = offline randomizer available (US ROMs).",
                  foreground="#666").pack(anchor="w", padx=12, pady=(4, 8))
        bf = ttk.Frame(body)
        bf.pack(fill="x", padx=12, pady=6)
        ttk.Button(bf, text="Verify install",
                   command=lambda: run(verify_task)).pack(side="left")
        ttk.Button(bf, text="Open romhacks folder",
                   command=lambda: run(open_romhacks)).pack(side="left", padx=6)
        ttk.Button(bf, text="Automatic setup",
                   command=lambda: run(setup_task)).pack(side="left", padx=6)

    # --- Per-game page ------------------------------------------------------
    game_state: dict = {}

    def vars_for(key: str) -> dict:
        v = game_state.get(key)
        if v is None:
            v = dict(
                mode=tk.StringVar(value=GUI_MODES[0]),
                seed=tk.StringVar(value=""),
                hack=tk.StringVar(value="<none>"),
                both=tk.BooleanVar(value=False),
                victory_lap=tk.BooleanVar(value=False),
                door_palette=tk.BooleanVar(value=False),
                vanilla_wily=tk.BooleanVar(value=False),
                cursor_tint=tk.BooleanVar(value=False),
                hard_mode=tk.BooleanVar(value=False),
                ohko=tk.BooleanVar(value=False),
            )
            game_state[key] = v
        return v

    def modes_for(key: str) -> list:
        if key not in RANDO_GAMES:
            return ["Vanilla", "ROM hack only"]
        if key == "mm6":
            # MM6 only has the weapon-get shuffle; palette is not located.
            return [m for m in GUI_MODES if m != "Palette shuffle only"]
        return list(GUI_MODES)

    def page_targets(c: dict, key: str, v: dict) -> list:
        keys = [key]
        tw = colmod.twin(key)
        if tw and tw in colmod.games(c) and v["both"].get():
            keys.append(tw)
        return keys

    def page_opts(key: str, v: dict):
        gd = game_dir.get().strip()
        opts: dict = {"auto_romhacks": False}
        m = v["mode"].get()
        if m == "Randomized":
            opts["seed"] = v["seed"].get().strip() or random_seed()
            opts["visualizer"] = True
        elif m == "Palette shuffle only":
            opts.update(seed=random_seed(), weakness=False, weapons=False,
                        palette=True)
        if key == "mm1":
            opts["victory_lap"] = v["victory_lap"].get()
            opts["door_palette"] = v["door_palette"].get()
            opts["vanilla_wily"] = v["vanilla_wily"].get()
            opts["cursor_tint"] = v["cursor_tint"].get()
            opts["hard_mode"] = v["hard_mode"].get()
            opts["ohko"] = v["ohko"].get()
        hs = v["hack"].get()
        if hs and hs != "<none>":
            found = discover_romhacks(gd, key)
            if hs == "<all>":
                opts["romhacks"] = found
            else:
                picks = [h for h in found if os.path.basename(h) == hs]
                if picks:
                    opts["romhacks"] = picks
        elif m == "ROM hack only":
            found = discover_romhacks(gd, key)
            if found:
                opts["romhacks"] = found
            else:
                logq.put(f"[20xx] no patches in romhacks/{key}/ - "
                         "drop .ips/.bps there")
                return None
        return opts

    def show_game(c: dict, key: str):
        use_collection(c)
        clear_page()
        g = romtable.GAMES[key]
        crumb.set(f"Collections / {c['name']} / {g.title}")
        page_title(g.title, f"{g.region} · mapper {g.ines_header[6] >> 4:#x} · "
                            f"PRG {g.prg.size:#x}"
                            + (f" · CHR {g.chr.size:#x}" if g.chr else ""))
        ttk.Button(body, text=f"< {c['name']}",
                   command=lambda: show_games(c)).pack(anchor="w", padx=12,
                                                       pady=(0, 6))
        v = vars_for(key)
        modes = modes_for(key)
        if v["mode"].get() not in modes:
            v["mode"].set(modes[0])
        mm1 = key == "mm1"

        fr = ttk.LabelFrame(body, text="Options")
        fr.pack(fill="x", padx=12, pady=6)
        ttk.Label(fr, text="Mode").grid(row=0, column=0, sticky="w", padx=8, pady=5)
        ttk.Combobox(fr, textvariable=v["mode"], values=modes,
                     state="readonly", width=22).grid(
                         row=0, column=1, sticky="w", padx=6)
        if key not in RANDO_GAMES:
            ttk.Label(fr, text="Offline randomizer: US Mega Man ROMs only "
                               "(vanilla/hacks work here).",
                      foreground="#666").grid(row=0, column=2, columnspan=2,
                                              sticky="w", padx=6)
        ttk.Label(fr, text="Seed").grid(row=1, column=0, sticky="w", padx=8, pady=5)
        sf = ttk.Frame(fr)
        sf.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Entry(sf, textvariable=v["seed"], width=14).pack(side="left")
        ttk.Button(sf, text="Random",
                   command=lambda: v["seed"].set(random_seed())).pack(
                       side="left", padx=4)
        ttk.Label(fr, text="ROM hack").grid(row=2, column=0, sticky="w",
                                            padx=8, pady=5)
        found = ([os.path.basename(h) for h in discover_romhacks(
            game_dir.get().strip(), key)] if game_dir.get().strip() else [])
        hack_cb = ttk.Combobox(fr, textvariable=v["hack"],
                               values=["<none>"] + found +
                               (["<all>"] if found else []),
                               state="readonly", width=30)
        hack_cb.grid(row=2, column=1, sticky="w", padx=6)
        if v["hack"].get() not in ["<none>", "<all>"] + found:
            v["hack"].set("<none>")
        tw = colmod.twin(key)
        if tw and tw in colmod.games(c):
            ttk.Checkbutton(fr, text=f"Also patch {romtable.GAMES[tw].title}",
                            variable=v["both"]).grid(
                                row=2, column=2, columnspan=2, sticky="w", padx=6)

        if mm1:
            ef = ttk.LabelFrame(body, text="MM1 extras")
            ef.pack(fill="x", padx=12, pady=6)
            ttk.Checkbutton(ef, text="Victory lap (max boss damage)",
                            variable=v["victory_lap"]).grid(
                                row=0, column=0, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(ef, text="Boss doors tinted by weakness",
                            variable=v["door_palette"]).grid(
                                row=0, column=1, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(ef, text="Vanilla Wily (no Wily changes)",
                            variable=v["vanilla_wily"]).grid(
                                row=1, column=0, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(ef, text="Stage-select cursor tint (weakness + green)",
                            variable=v["cursor_tint"]).grid(
                                row=1, column=1, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(ef, text="Hard mode (damage received x2, dealt /1.5)",
                            variable=v["hard_mode"]).grid(
                                row=2, column=0, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(ef, text="OHKO (any hit = instant death)",
                            variable=v["ohko"]).grid(
                                row=2, column=1, sticky="w", padx=8, pady=3)
        if key == "mm6":
            ttk.Label(body, text="MM6 currently shuffles weapon-get rewards only; "
                                 "its damage/weakness and palette tables are not "
                                 "located yet.",
                      foreground="#666").pack(anchor="w", padx=14)

        def do_launch():
            opts = page_opts(key, v)
            if opts is None:
                return
            gd = game_dir.get().strip()
            targets = page_targets(c, key, v)
            logq.put(f"[20xx] build {targets} mode={v['mode'].get()} "
                     f"seed={opts.get('seed', '-')}")
            prepare_roms(gd, os.path.join(gd, "roms"), target=targets, **opts)
            logq.put("[20xx] launching collection...")
            try:
                ensure_mesen_host(gd)
            except Exception as ex:
                logq.put(f"[20xx] host start failed (continuing): {ex}")
            launch(c.get("appid") or args.appid)

        def do_build():
            opts = page_opts(key, v)
            if opts is None:
                return
            gd = game_dir.get().strip()
            targets = page_targets(c, key, v)
            logq.put(f"[20xx] building {targets} (no launch) "
                     f"seed={opts.get('seed', '-')}")
            prepare_roms(gd, os.path.join(gd, "roms"), target=targets, **opts)
            logq.put("[20xx] done - launch the collection when ready.")

        def do_export():
            opts = page_opts(key, v)
            if opts is None:
                return
            dest = filedialog.askdirectory(
                initialdir=os.path.expanduser("~"), title="Export ROMs to folder")
            if not dest:
                logq.put("[20xx] export cancelled")
                return
            dest = os.path.join(dest, "MMLC-ROMs")
            targets = page_targets(c, key, v)
            export_roms(game_dir.get().strip(), dest, target=targets, **opts)
            logq.put(f"[20xx] exported -> {dest}")

        def open_hacks():
            d = os.path.join(romhacks_root(game_dir.get().strip()), key)
            os.makedirs(d, exist_ok=True)
            logq.put(f"[20xx] romhacks folder: {d}")
            open_folder(d)

        bf = ttk.Frame(body)
        bf.pack(fill="x", padx=12, pady=10)
        ttk.Button(bf, text="Build & Launch",
                   command=lambda: run(do_launch)).pack(side="left")
        ttk.Button(bf, text="Build only",
                   command=lambda: run(do_build)).pack(side="left", padx=6)
        ttk.Button(bf, text="Export...",
                   command=lambda: run(do_export)).pack(side="left", padx=6)
        ttk.Button(bf, text="Open romhacks",
                   command=lambda: run(open_hacks)).pack(side="left", padx=6)

    # --- Settings page ------------------------------------------------------
    def show_settings():
        clear_page()
        crumb.set("Settings")
        page_title("Settings", "Global options plus the active collection's "
                               "install folder.")
        c = active_col()
        gd = game_dir.get()
        f1 = ttk.LabelFrame(body, text=f"Install location — {c['name']}")
        f1.pack(fill="x", padx=12, pady=6)
        ttk.Entry(f1, textvariable=game_dir).grid(row=0, column=0, sticky="ew",
                                                 padx=8, pady=8)
        f1.columnconfigure(0, weight=1)

        def browse_dir():
            d = filedialog.askdirectory(initialdir=game_dir.get() or
                                        os.path.expanduser("~"))
            if d:
                game_dir.set(d)
                c["game_dir"] = d
                save_config()

        def auto_dir():
            game_dir.set(game_dir_default())
            c["game_dir"] = game_dir.get()
            save_config()

        ttk.Button(f1, text="Browse", command=browse_dir).grid(row=0, column=1, padx=4)
        ttk.Button(f1, text="Auto-detect", command=auto_dir).grid(row=0, column=2, padx=4)
        ttk.Label(f1, text="Steamless.CLI.exe").grid(row=1, column=0, sticky="w",
                                                     padx=8, pady=(0, 4))
        ttk.Entry(f1, textvariable=steamless_cli).grid(row=2, column=0, sticky="ew",
                                                       padx=8, pady=(0, 8))

        def browse_steamless():
            f = filedialog.askopenfilename(
                initialdir=os.path.expanduser("~"),
                title="Select Steamless.CLI.exe",
                filetypes=[("Steamless CLI", "*.exe"), ("All files", "*")])
            if f:
                steamless_cli.set(f)

        ttk.Button(f1, text="Browse", command=browse_steamless).grid(
            row=2, column=1, padx=4, pady=(0, 8))
        ttk.Button(f1, text="Auto-detect",
                   command=lambda: steamless_cli.set(_detect_steamless() or "")
                   ).grid(row=2, column=2, padx=4, pady=(0, 8))

        f2 = ttk.LabelFrame(body, text="Mesen core (user-supplied; GPLv3, not bundled)")
        f2.pack(fill="x", padx=12, pady=6)
        ttk.Entry(f2, textvariable=core).grid(row=0, column=0, sticky="ew",
                                              padx=8, pady=8)
        f2.columnconfigure(0, weight=1)

        def browse_core():
            f = filedialog.askopenfilename(initialdir=os.path.expanduser("~"),
                                           title="Select MesenCore.so / MesenCore.dll")
            if f:
                core.set(f)

        ttk.Button(f2, text="Browse", command=browse_core).grid(row=0, column=1, padx=4)
        ttk.Button(f2, text="Auto-detect",
                   command=lambda: core.set(find_core(game_dir.get()) or "")
                   ).grid(row=0, column=2, padx=4)
        ttk.Button(f2, text="Build core",
                   command=lambda: run(build_core_task)).grid(row=0, column=3, padx=4)
        ttk.Checkbutton(f2, text="Automatic setup may build the core if missing "
                                 "(clone + make)",
                        variable=auto_build_core).grid(
                            row=1, column=0, columnspan=4, sticky="w",
                            padx=8, pady=(0, 6))

        f3 = ttk.Frame(body)
        f3.pack(fill="x", padx=12, pady=2)
        ttk.Button(f3, text="Automatic setup",
                   command=lambda: run(setup_task)).pack(side="left")
        ttk.Button(f3, text="Verify install",
                   command=lambda: run(verify_task)).pack(side="left", padx=6)

        fg = ttk.LabelFrame(body, text="Overclock (global)")
        fg.pack(fill="x", padx=12, pady=6)
        ttk.Checkbutton(fg, text="Follow in-game CPU SPEED",
                        variable=oc_follow).grid(row=0, column=0, sticky="w",
                                                 padx=8, pady=4)
        ttk.Label(fg, text="Turbo scanlines").grid(row=0, column=1, sticky="w", padx=6)
        ttk.Entry(fg, textvariable=oc_turbo, width=8).grid(
            row=0, column=2, sticky="w", padx=6)

        f6 = ttk.LabelFrame(body, text="Controls (NES button -> Xbox button)")
        f6.pack(fill="x", padx=12, pady=6)
        for i, key in enumerate(INPUT_LABELS):
            r, col = divmod(i, 4)
            ttk.Label(f6, text=INPUT_LABELS[key]).grid(row=r, column=col * 2,
                                                       sticky="e", padx=4, pady=3)
            ttk.Combobox(f6, textvariable=input_vars[key], values=INPUT_CHOICES,
                         state="readonly", width=10).grid(
                             row=r, column=col * 2 + 1, sticky="w", padx=4)
        ttk.Label(f6, text="Save state").grid(row=2, column=0, sticky="e",
                                              padx=4, pady=3)
        ttk.Combobox(f6, textvariable=save_mod, values=STATE_MODIFIERS,
                     state="readonly", width=6).grid(row=2, column=1, sticky="w")
        ttk.Combobox(f6, textvariable=save_btn, values=STATE_BUTTONS,
                     state="readonly", width=9).grid(row=2, column=2, sticky="w")
        ttk.Label(f6, text="Load state").grid(row=2, column=3, sticky="e", padx=4)
        ttk.Combobox(f6, textvariable=load_mod, values=STATE_MODIFIERS,
                     state="readonly", width=6).grid(row=2, column=4, sticky="w")
        ttk.Combobox(f6, textvariable=load_btn, values=STATE_BUTTONS,
                     state="readonly", width=9).grid(row=2, column=5, sticky="w")
        ttk.Label(f6, text="Slot").grid(row=2, column=6, sticky="e", padx=4)
        ttk.Entry(f6, textvariable=state_slot, width=4).grid(row=2, column=7,
                                                             sticky="w")
        ttk.Label(f6, text="Reset").grid(row=3, column=0, sticky="e", padx=4,
                                         pady=3)
        ttk.Combobox(f6, textvariable=reset_mod, values=STATE_MODIFIERS,
                     state="readonly", width=6).grid(row=3, column=1, sticky="w")
        ttk.Combobox(f6, textvariable=reset_btn, values=STATE_BUTTONS,
                     state="readonly", width=9).grid(row=3, column=2, sticky="w")
        ttk.Label(f6, text="Turbo (hold)").grid(row=3, column=3, sticky="e",
                                                padx=4)
        ttk.Combobox(f6, textvariable=turbo_mod, values=STATE_MODIFIERS,
                     state="readonly", width=6).grid(row=3, column=4, sticky="w")
        ttk.Combobox(f6, textvariable=turbo_btn, values=STATE_BUTTONS,
                     state="readonly", width=9).grid(row=3, column=5, sticky="w")
        ttk.Button(f6, text="Save controls",
                   command=lambda: run(save_controls)).grid(
                       row=4, column=0, columnspan=2, pady=4, sticky="w")
        bf = ttk.Frame(body)
        bf.pack(fill="x", padx=12, pady=8)
        ttk.Button(bf, text="< Back",
                   command=show_collections).pack(side="left")

    # --- Tasks shared by pages ---------------------------------------------
    def save_runtime():
        gd = game_dir.get().strip()
        if not gd:
            return
        write_input_map(gd, input_map_values())
        ini_set(gd, {"state_save_button": join_state_combo(save_mod.get(), save_btn.get()),
                     "state_load_button": join_state_combo(load_mod.get(), load_btn.get()),
                     "state_reset_button": join_state_combo(reset_mod.get(), reset_btn.get()),
                     "state_turbo_button": join_state_combo(turbo_mod.get(), turbo_btn.get()),
                     "state_slot": state_slot.get().strip() or "0"})

    def build_core_task():
        c = build_core(None, status=lambda m: logq.put(m))
        if c:
            core.set(c)
            gd = game_dir.get().strip()
            if gd:
                ini_set(gd, {"mesen": "1", "mesen_core": c})
            logq.put("[20xx] core ready.")

    def open_romhacks():
        p = ensure_romhacks_dir(game_dir.get())
        logq.put(f"[20xx] romhacks folder: {p}")
        open_folder(p)

    def save_controls():
        m = input_map_values()
        gd = game_dir.get().strip()
        sv = join_state_combo(save_mod.get(), save_btn.get())
        ld = join_state_combo(load_mod.get(), load_btn.get())
        rs = join_state_combo(reset_mod.get(), reset_btn.get())
        tb = join_state_combo(turbo_mod.get(), turbo_btn.get())
        slot = state_slot.get().strip() or "0"
        if gd:
            write_input_map(gd, m)
            ini_set(gd, {"state_save_button": sv, "state_load_button": ld,
                         "state_reset_button": rs, "state_turbo_button": tb,
                         "state_slot": slot})
        c = dict(gui_config_load())
        c.update({"game_dir": gd, "core": core.get().strip(),
                  "steamless": steamless_cli.get().strip(), "input_map": m,
                  "state_save_button": sv, "state_load_button": ld,
                  "state_reset_button": rs, "state_turbo_button": tb,
                  "state_slot": slot,
                  "collections": state["collections"],
                  "collection_id": active.get()})
        gui_config_save(c)
        logq.put("[20xx] controls saved: "
                 + ", ".join(f"{k[4:]}={v}" for k, v in m.items()))
        logq.put(f"[20xx] state hotkeys: save={sv} load={ld} reset={rs} "
                 f"turbo={tb} slot={slot}")

    def setup_task():
        gd = game_dir.get().strip()
        if not gd:
            logq.put("Set the install folder first.")
            return
        save_runtime()
        ns = argparse.Namespace(
            game_dir=gd,
            core=core.get().strip() or None,
            steamless=steamless_cli.get().strip() or None,
            no_core_build=not auto_build_core.get(),
            no_extract=False, yes=True, start=False)
        rc = cmd_setup(ns)
        c = find_core(gd)
        if c:
            core.set(c)
        try:
            turbo = int(oc_turbo.get().strip() or "262")
        except ValueError:
            turbo = 262
        ini_set(gd, {"mesen_overclock_follow": "1" if oc_follow.get() else "0",
                     "mesen_overclock_turbo": str(turbo)})
        logq.put(f"[20xx] overclock: follow in-game CPU SPEED="
                 f"{oc_follow.get()} turbo={turbo} scanlines")
        logq.put(f"[20xx] romhacks folder: {ensure_romhacks_dir(gd)}")
        write_input_map(gd, input_map_values())
        sv = join_state_combo(save_mod.get(), save_btn.get())
        ld = join_state_combo(load_mod.get(), load_btn.get())
        rs = join_state_combo(reset_mod.get(), reset_btn.get())
        tb = join_state_combo(turbo_mod.get(), turbo_btn.get())
        ini_set(gd, {"state_save_button": sv, "state_load_button": ld,
                     "state_reset_button": rs, "state_turbo_button": tb,
                     "state_slot": state_slot.get().strip() or "0"})
        logq.put(f"[20xx] state hotkeys: save={sv} load={ld} reset={rs} turbo={tb}")
        save_config()
        logq.put(f"[20xx] automatic setup finished (status {rc}).")

    def verify_task():
        gd = game_dir.get().strip()
        pe = PE(find_source(gd))
        bad = 0
        keys = colmod.games(active_col()) or GAME_ORDER
        for k in keys:
            g = romtable.GAMES[k]
            crc = patcher.crc32(patcher.read_prg(pe, g))
            want = romtable.ORIGINAL_PRG_CRC.get(k)
            ok = want is None or crc == want
            bad += 0 if ok else 1
            logq.put(f"  {k:4} {g.title:14} crc={crc:#010x} "
                     f"{'ok' if ok else 'MISMATCH'}")
        logq.put(f"[20xx] verify: {len(keys) - bad}/{len(keys)} ok")

    def _wheel(e):
        try:
            cls = e.widget.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Text", "TCombobox", "Listbox", "Scrollbar", "TScrollbar"):
            return
        if getattr(e, "num", None) == 4 or getattr(e, "delta", 0) > 0:
            canvas.yview_scroll(-1, "units")
        elif getattr(e, "num", None) == 5 or getattr(e, "delta", 0) < 0:
            canvas.yview_scroll(1, "units")

    for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_all(_seq, _wheel, add="+")

    def _select_all(e):
        w = e.widget
        try:
            if w.winfo_class() in ("Entry", "TEntry", "TCombobox"):
                w.selection_range(0, "end")
            elif w.winfo_class() == "Text":
                w.tag_add("sel", "1.0", "end-1c")
        except Exception:
            pass
        return "break"

    root.bind_all("<Control-a>", _select_all, add="+")

    show_collections()
    pump()
    root.mainloop()
    return 0


def _ask(prompt: str) -> Optional[str]:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _select_game() -> Optional[str]:
    """Return a game key, '' to retry, or None to quit."""
    print("\nSelect a game:")
    for i, k in enumerate(GAME_ORDER):
        g = romtable.GAMES[k]
        tag = "   <-- randomizer" if k in RANDO_GAMES else ""
        print(f"  [{i + 1:2}] {g.title:20} [{g.region}]{tag}")
    print("  [ 0] quit")
    c = _ask("game> ")
    if c is None or c in ("0", "q", ""):
        return None
    if c.isdigit() and 1 <= int(c) <= len(GAME_ORDER):
        return GAME_ORDER[int(c) - 1]
    print(f"20xx: enter 1-{len(GAME_ORDER)}")
    return ""


def _game_menu(game: romtable.Game) -> Optional[str]:
    """Return an action key, '' to retry, or None to go back."""
    print(f"\n{game.title} [{game.region}] -- what do you want to do?")
    items = [("vanilla", "Vanilla (unmodified ROM)")]
    if game.key in RANDO_GAMES:
        items += [
            ("rando", "Randomized (fresh seed)"),
            ("rando_seed", "Randomized (enter a seed)"),
            ("palette", "Palette shuffle only"),
            ("visualizer", "Randomized + weakness visualizer"),
        ]
    items += [
        ("romhack", "Apply a ROM hack (IPS/BPS)"),
        ("launch", "Just launch (no change)"),
    ]
    for i, (_, label) in enumerate(items):
        print(f"  [{i + 1}] {label}")
    print("  [ 0] back")
    c = _ask("action> ")
    if c is None or c in ("0", "q", ""):
        return None
    if c.isdigit() and 1 <= int(c) <= len(items):
        return items[int(c) - 1][0]
    print(f"20xx: enter 1-{len(items)}")
    return ""


def _run_action(game_key: str, action: str, args: argparse.Namespace,
                out: str) -> bool:
    """Prepare the ROM for the chosen action. Returns True to proceed to launch."""
    if action == "launch":
        return True
    # The interactive flow never auto-applies the folder; a patch is applied
    # only when picked (or the "all" option), so vanilla stays vanilla.
    opts = {"target": game_key, "auto_romhacks": False}
    if action == "rando":
        opts["seed"] = random_seed()
    elif action == "rando_seed":
        s = _ask("seed> ")
        if not s:
            print("20xx: no seed; cancelled")
            return False
        opts["seed"] = s
    elif action == "palette":
        opts.update(seed=random_seed(), weakness=False, weapons=False, palette=True)
    elif action == "visualizer":
        opts.update(seed=random_seed(), visualizer=True)
    elif action == "romhack":
        found = discover_romhacks(args.game_dir, game_key)
        if found:
            print(f"Select a ROM hack for {game_key}:")
            for i, h in enumerate(found):
                print(f"  [{i + 1}] {os.path.basename(h)}")
            print("  [ a] apply all    [ c] choose a file elsewhere    [ 0] cancel")
            c = _ask("hack> ")
            if c is None or c in ("0", "q", ""):
                return False
            if c.lower() == "a":
                opts["romhacks"] = list(found)
            elif c.lower() == "c":
                path = _ask("patch file > ")
                if not path:
                    return False
                if not os.path.exists(path):
                    print(f"20xx: {path} not found")
                    return False
                opts["romhacks"] = [path]
            elif c.isdigit() and 1 <= int(c) <= len(found):
                opts["romhacks"] = [found[int(c) - 1]]
            else:
                print("20xx: invalid selection")
                return False
        else:
            root = romhacks_root(args.game_dir)
            print(f"20xx: no patches in {root}/{game_key}/.")
            path = _ask("enter a patch file (blank to cancel)> ")
            if not path:
                return False
            if not os.path.exists(path):
                print(f"20xx: {path} not found")
                return False
            opts["romhacks"] = [path]
    prepare_roms(args.game_dir, out, source=args.source, **opts)
    return True


def interactive(args: argparse.Namespace, launch_cmd=None) -> int:
    out = args.out or os.path.join(args.game_dir, "roms")
    print(f"{APP} v{VERSION} -- Mega Man Legacy Collection launcher")
    if launch_cmd:
        print("  (Steam prelaunch: the collection starts when you finish here)")
    print(f"  source: {find_source(args.game_dir, args.source)}")
    print(f"  output: {out}")
    while True:
        key = _select_game()
        if key is None:
            return 0
        if key == "":
            continue
        g = romtable.GAMES[key]
        while True:
            action = _game_menu(g)
            if action is None:
                break
            if action == "":
                continue
            if not _run_action(key, action, args, out):
                continue
            ans = _ask("Launch the collection with Mesen now? [Y/n] ")
            if ans is None:
                return 0
            if ans.lower() not in ("n", "no"):
                do_launch(args.game_dir, args.appid, launch_cmd)
                return 0
            break
    return 0


def _add_patch_opts(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
    sp.add_argument("--seed", default=None,
                    help="randomizer seed ('random' for a fresh one); "
                         "omit for vanilla")
    sp.add_argument("--weakness", action=argparse.BooleanOptionalAction,
                    default=True, help="shuffle boss weaknesses (MM1/MM2/MM3/MM4/MM5)")
    sp.add_argument("--weapons", action=argparse.BooleanOptionalAction,
                    default=True, help="shuffle weapon rewards (MM1/MM2/MM3/MM4/MM5)")
    sp.add_argument("--palette", action=argparse.BooleanOptionalAction,
                    default=True, help="shuffle palettes (MM1/MM2/MM3/MM4/MM5)")
    sp.add_argument("--palette-only", action="store_true",
                    help="only shuffle the palette (seed implied)")
    sp.add_argument("--visualizer", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="apply the weakness visualizer IPS (MM1, always on "
                         "for stage-select colors + green marker)")
    sp.add_argument("--victory-lap", action="store_true",
                    help="MM1: max out all boss damage (easy kills)")
    sp.add_argument("--door-palette", action="store_true",
                    help="MM1: tint each boss door with its weakness color")
    sp.add_argument("--vanilla-wily", action="store_true",
                    help="MM1: keep the Wily-stage damage rows vanilla")
    sp.add_argument("--cursor-tint", action="store_true",
                    help="MM1: tint the selected stage-select portrait with "
                         "its weakness color (green for top-buster)")
    sp.add_argument("--hard-mode", action="store_true",
                    help="MM1: hard mode (damage received x2, dealt /1.5)")
    sp.add_argument("--ohko", action="store_true",
                    help="MM1: OHKO mode (any hit = instant death, both ways)")
    sp.add_argument("--romhack", action="append", default=None, metavar="FILE",
                    help="apply an IPS/BPS ROM hack (repeatable)")
    sp.add_argument("--no-romhacks", action="store_true",
                    help="do not auto-apply patches from the romhacks/ folder")
    sp.add_argument("--ips-adjust", type=lambda s: int(s, 0), default=0,
                    help="offset added to IPS offsets (e.g. -16 for headerless)")
    return sp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="20xx", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--game-dir", default=game_dir_default())
    p.add_argument("--from", dest="source", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--appid", default=DEFAULT_APPID)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list the 12 games").set_defaults(func=cmd_list)
    sub.add_parser("version", help="show the 20XX version").set_defaults(func=cmd_version)
    sub.add_parser("verify", help="verify ROM anchors").set_defaults(func=cmd_verify)
    ep = sub.add_parser("extract", help="write vanilla .nes files")
    ep.add_argument("--game", default="all")
    ep.set_defaults(func=cmd_extract)
    pp = sub.add_parser("play", help="patch + launch MMLC from the terminal")
    pp.add_argument("game", nargs="?", default="all")
    _add_patch_opts(pp)
    pp.add_argument("--no-launch", action="store_true",
                    help="prepare ROMs but do not start the collection")
    pp.set_defaults(func=cmd_play)
    pch = sub.add_parser("patch", help="patch one game's ROM offline "
                                       "(randomizer / palette / ROM hacks)")
    pch.add_argument("game")
    _add_patch_opts(pch)
    pch.set_defaults(func=cmd_patch)
    ex = sub.add_parser("export", help="extract the 12 ROMs (patched where "
                                       "selected) for your own emulators")
    ex.add_argument("game", nargs="?", default="all")
    ex.add_argument("--dest", default=None,
                    help="destination folder (default: ~/MMLC-ROMs)")
    ex.add_argument("--vanilla", action="store_true",
                    help="export unpatched ROMs (ignore the patch options)")
    _add_patch_opts(ex)
    ex.set_defaults(func=cmd_export)
    rh = sub.add_parser("romhacks", help="list/create the romhacks/ drop-in folder")
    rh.add_argument("game", nargs="?", default="all")
    rh.set_defaults(func=cmd_romhacks)
    lp = sub.add_parser("launch", help="launch the collection from the terminal")
    lp.add_argument("--print", dest="print_only", action="store_true",
                    help="print the command instead of running it")
    lp.set_defaults(func=cmd_launch)
    pl = sub.add_parser("prelaunch",
                        help="Steam launch hook: prep ROMs + start host, then exec "
                             "the launch command (use: 20xx prelaunch %%command%%)")
    pl.add_argument("command", nargs=argparse.REMAINDER)
    pl.set_defaults(func=cmd_prelaunch)
    mp = sub.add_parser("mesen", help="enable/disable the Mesen replacer (user-supplied core)")
    mp.add_argument("--core", default=None, help="path to your MesenCore.so")
    mp.add_argument("--port", type=int, default=None)
    mp.add_argument("--start", action="store_true", help="start the host now")
    mp.add_argument("--off", action="store_true", help="disable the replacer")
    mp.add_argument("--no-install", action="store_true",
                    help="do not install the proxy DLL")
    mp.add_argument("--steamless", default=None,
                    help="path to Steamless.CLI.exe (for the SteamStub unpack)")
    mp.set_defaults(func=cmd_mesen)
    up = sub.add_parser("unpack",
                        help="Steamless-unpack Proteus.exe (required for the "
                             "Mesen runtime hooks; also done by Automatic setup)")
    up.add_argument("--steamless", default=None, help="path to Steamless.CLI.exe")
    up.add_argument("--force", action="store_true",
                    help="re-unpack even if Proteus.exe is already unpacked")
    up.set_defaults(func=cmd_unpack)
    oc = sub.add_parser("overclock", help="NES overclock; follows MMLC's "
                                          "in-game CPU SPEED (ORIGINAL/TURBO) "
                                          "by default")
    oc.add_argument("--follow", action=argparse.BooleanOptionalAction,
                    default=None,
                    help="follow the in-game CPU SPEED option (default: on)")
    oc.add_argument("--scanlines", type=int, default=None,
                    help="explicit scanline count (disables following)")
    oc.add_argument("--turbo", type=int, default=None,
                    help="scanlines used when the in-game option is TURBO "
                         "(default: 262)")
    oc.add_argument("--off", action="store_true",
                    help="disable the overclock entirely")
    oc.set_defaults(func=cmd_overclock)
    st = sub.add_parser("states", help="Mesen save/load-state hotkeys (for "
                                       "ROM-hack testing)")
    st.add_argument("--save-button", default=None,
                    help="combo that saves a state, e.g. lb+y (or 'off')")
    st.add_argument("--load-button", default=None,
                    help="combo that loads a state, e.g. lb+b (or 'off')")
    st.add_argument("--reset-button", default=None,
                    help="combo that soft-resets the console, e.g. lb+start (or 'off')")
    st.add_argument("--turbo-button", default=None,
                    help="combo that fast-forwards while held, e.g. lb+x (or 'off')")
    st.add_argument("--slot", type=int, default=None,
                    help="save-state slot (default 0)")
    st.set_defaults(func=cmd_states)
    bc = sub.add_parser("build-core",
                        help="clone+patch+build the Mesen2 core (MesenCore.so)")
    bc.add_argument("--dir", default=None, help="where to clone/build Mesen2")
    bc.add_argument("--jobs", type=int, default=None)
    bc.set_defaults(func=cmd_build_core)
    doc = sub.add_parser("doctor", help="check install, Steamless, proxy, "
                                        "core, host and ROM readiness")
    doc.add_argument("--core", default=None, help="MesenCore.so/.dll to check")
    doc.add_argument("--steamless", default=None, help="Steamless.CLI.exe")
    doc.set_defaults(func=cmd_doctor)
    su = sub.add_parser("setup", help="automatic setup: detect install, unpack, "
                                      "proxy, core, host, ROMs")
    su.add_argument("--core", default=None, help="use this MesenCore.so/.dll")
    su.add_argument("--steamless", default=None, help="Steamless.CLI.exe")
    su.add_argument("--no-core-build", action="store_true",
                    help="never clone/build Mesen2; only use an existing core")
    su.add_argument("--no-extract", action="store_true",
                    help="do not extract/verify ROMs")
    su.add_argument("--yes", action="store_true",
                    help="answer yes to prompts (e.g. build the core)")
    su.add_argument("--start", action="store_true",
                    help="start the Mesen host when setup succeeds")
    su.set_defaults(func=cmd_setup)
    sub.add_parser("gui", help="Tk launcher").set_defaults(func=cmd_gui)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None):
        # A double-clicked frozen build (no console stdin) gets the GUI; a
        # terminal launch keeps the text wizard.
        if FROZEN and (sys.stdin is None or not sys.stdin.isatty()):
            return cmd_gui(args)
        return interactive(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
