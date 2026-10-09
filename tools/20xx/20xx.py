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
    20xx.py mesen --core PATH        # enable the Mesen replacer (your core)
    20xx.py unpack [--steamless P]   # Steamless-unpack Proteus.exe (Setup/Repair)
    20xx.py                          # interactive: pick a game, choose what to
                                     #   do (vanilla / rando / palette / ROM
                                     #   hack), then launch with Mesen

Patch options (for `patch` and `play`):
    --seed S            randomizer seed ('random' for a fresh one)
    --[no-]weakness     shuffle boss weaknesses        (MM1/MM2/MM3/MM4/MM5)
    --[no-]weapons      shuffle weapon rewards         (MM1/MM2/MM3/MM4/MM5)
    --[no-]palette      shuffle Mega Man's palette     (MM1/MM2/MM3/MM4/MM5)
    --palette-only      palette shuffle only
    --visualizer        apply the weakness-visualizer IPS (MM1)
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from mmlc import patcher, randomizer, romhack, romtable, steamless
from mmlc.pe import PE, write_file_atomic

DEFAULT_GAME_DIR = "/var/home/user/.local/share/Steam/steamapps/common/Suzy"
DEFAULT_APPID = "363440"

# Brand/version shown so it is obvious which build is running.
APP = "20XX"
VERSION = "0.5"

# --- Mesen replacer (optional; the user supplies their own Mesen2 core) ------
HOST_SRC = os.path.join(ROOT, "tools", "mesen_host", "mesen_host.c")
HOST_BUILD = os.path.join(ROOT, "build", "mesen_host")
PROXY_BUILD = os.path.join(ROOT, "build", "steam_api.dll")
PROXY_BUILD_SCRIPT = os.path.join(ROOT, "build", "build_proxy.sh")
DEFAULT_PORT = 36344
# Mesen2 source + the MMLC capture patch, for `20xx build-core`.
MESEN2_REPO = "https://github.com/SourMesen/Mesen2.git"
MESEN2_PATCH = os.path.join(ROOT, "tools", "mesen_host", "mesen2_capture.patch")
MESEN2_DEFAULT_DIR = os.path.expanduser("~/.cache/mmlc-mesen/Mesen2")
# Places to look for a user-supplied Mesen2 core if mmlc.ini has no mesen_core.
CORE_SEARCH = [
    os.path.expanduser("~/.cache/mmlc-mesen/Mesen2/InteropDLL/obj.linux-x64/MesenCore.so"),
    os.path.expanduser("~/.cache/mmlc-mesen/MesenCore.so"),
    os.path.join(ROOT, "build", "MesenCore.so"),
    os.path.join(ROOT, "MesenCore.so"),
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
    return os.environ.get("MMLC_GAME", DEFAULT_GAME_DIR)


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
# `.bind`), so every scan misses and 20XX does nothing. Setup/Repair unpacks it.
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
                      romhacks: Optional[List[str]] = None,
                      ips_adjust: int = 0) -> bytes:
    """Return a patched iNES image: randomizer/palette (per game) + ROM hacks.

    The PRG is randomized first, then ROM-hack patches are applied to the whole
    .nes (so a hack sees the final tables). BPS hacks may resize the image."""
    nes = bytearray(patcher.extract_ines(pe, game))
    prg_off, prg_size = 16, game.prg.size

    if seed is not None:
        if game.key not in RANDO_GAMES:
            print(f"20xx: no randomizer for {game.key} yet; ignoring --seed")
        else:
            prg = bytearray(nes[prg_off:prg_off + prg_size])
            spoiler = randomizer.randomize(
                game.key, prg, seed, weakness=weakness, weapons=weapons,
                visualizer=visualizer, palette=palette)
            nes[prg_off:prg_off + prg_size] = prg
            print(f"20xx: randomized {game.key} seed={seed!r} "
                  f"weakness={weakness} weapons={weapons} palette={palette} "
                  f"visualizer={visualizer}")
            for line in spoiler.lines()[1:]:
                print("   " + line)

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
                             visualizer=visualizer, romhacks=hacks,
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
    if os.path.exists(HOST_BUILD):
        return HOST_BUILD
    if not os.path.exists(HOST_SRC):
        print(f"20xx: host source missing: {HOST_SRC}")
        return None
    os.makedirs(os.path.dirname(HOST_BUILD), exist_ok=True)
    print(f"20xx: building host -> {HOST_BUILD}")
    rc = subprocess.call(["cc", "-O2", "-o", HOST_BUILD, HOST_SRC, "-ldl", "-lpthread"])
    return HOST_BUILD if rc == 0 else None


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
    dest = os.path.join(game_dir, "steam_api.dll")
    if os.path.exists(PROXY_BUILD):
        import shutil
        shutil.copy2(PROXY_BUILD, dest)
        print(f"20xx: installed proxy -> {dest}")
        return True
    if os.path.exists(PROXY_BUILD_SCRIPT):
        print("20xx: building proxy (needs i686-w64-mingw32-gcc)...")
        env = dict(os.environ)
        orig = os.path.join(game_dir, "steam_api_orig.dll")
        if os.path.exists(orig):
            env["STEAM_API"] = orig
        rc = subprocess.call(["bash", PROXY_BUILD_SCRIPT], env=env)
        if rc == 0 and os.path.exists(PROXY_BUILD):
            import shutil
            shutil.copy2(PROXY_BUILD, dest)
            print(f"20xx: installed proxy -> {dest}")
            return True
    print("20xx: no built proxy; run build/build_proxy.sh first")
    return False


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
    log = os.path.join(ROOT, "build", "mesen_host.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    lf = open(log, "ab")
    subprocess.Popen([host, "--core", core, "--port", str(port)],
                     stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                     start_new_session=True)
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
        print("  enable with: 20xx mesen --core /path/to/MesenCore.so")
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
    """Configure the Mesen save/load-state hotkeys (handy for ROM-hack testing).
    Defaults: LB+Y = save, LB+B = load, slot 0."""
    gd = args.game_dir
    vals: Dict[str, str] = {}
    off = {"off", "none", ""}
    if args.save_button is not None:
        vals["state_save_button"] = "0" if args.save_button.lower() in off else args.save_button
    if args.load_button is not None:
        vals["state_load_button"] = "0" if args.load_button.lower() in off else args.load_button
    if args.slot is not None:
        vals["state_slot"] = str(args.slot)
    if vals:
        ini_set(gd, vals)
    print("[20xx] save-state hotkeys (restart the game to apply):")
    for k in ("state_save_button", "state_load_button", "state_slot"):
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


def prepare_roms(game_dir: str, out: str, *, source: Optional[str] = None,
                 target: str = "all", seed: Optional[str] = None,
                 weakness: bool = True, weapons: bool = True,
                 palette: bool = True, visualizer: bool = False,
                 romhacks: Optional[List[str]] = None,
                 ips_adjust: int = 0, auto_romhacks: bool = True) -> None:
    """Patch `target` (a game key, or 'all') with the options and write every
    game's .nes (vanilla for the rest) so any pick in the collection works."""
    src = find_source(game_dir, source)
    pe = PE(src)
    os.makedirs(out, exist_ok=True)
    for k in GAME_ORDER:
        g = romtable.GAMES[k]
        opts = {}
        if target in ("all", k):
            hacks = list(romhacks or [])
            if auto_romhacks:
                hacks = discover_romhacks(game_dir, k) + hacks
            opts = dict(seed=(seed if k in RANDO_GAMES else None),
                        weakness=weakness, weapons=weapons, palette=palette,
                        visualizer=visualizer, romhacks=hacks,
                        ips_adjust=ips_adjust)
        write_file_atomic(os.path.join(out, f"{k}.nes"),
                          build_patched_nes(g, pe, **opts))
    print(f"[20xx] prepared ROMs in {out}")


# Friendly filenames for exporting to the user's own emulators.
REGION_TAG = {"US": "USA", "JP": "Japan"}


def export_roms(game_dir: str, dest: str, *, source: Optional[str] = None,
                target: str = "all", seed: Optional[str] = None,
                weakness: bool = True, weapons: bool = True,
                palette: bool = True, visualizer: bool = False,
                romhacks: Optional[List[str]] = None,
                ips_adjust: int = 0, auto_romhacks: bool = True,
                quiet: bool = False) -> Dict[str, str]:
    """Extract the 12 ROMs (patched where selected) to *dest* with friendly
    filenames, for use in the user's own emulators. Returns key -> path.

    Same options as :func:`prepare_roms`: `target` is a game key or 'all';
    games other than the target are exported vanilla."""
    src = find_source(game_dir, source)
    pe = PE(src)
    os.makedirs(dest, exist_ok=True)
    written: Dict[str, str] = {}
    for k in GAME_ORDER:
        g = romtable.GAMES[k]
        opts = {}
        if target in ("all", k):
            hacks = list(romhacks or [])
            if auto_romhacks:
                hacks = discover_romhacks(game_dir, k) + hacks
            opts = dict(seed=(seed if k in RANDO_GAMES else None),
                        weakness=weakness, weapons=weapons, palette=palette,
                        visualizer=visualizer, romhacks=hacks,
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
        print("[20xx] no Mesen core found; run: 20xx mesen --core /path/MesenCore.so")
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
    patch_game(args.game_dir, out, args.game, source=args.source, seed=seed,
               weakness=args.weakness, weapons=args.weapons, palette=args.palette,
               visualizer=args.visualizer, romhacks=args.romhack,
               ips_adjust=args.ips_adjust, auto_romhacks=not args.no_romhacks)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export the ROMs (patched where selected) for the user's own emulators."""
    dest = args.dest or os.path.expanduser("~/MMLC-ROMs")
    if args.vanilla:
        seed = None
        weakness = weapons = palette = visualizer = False
    else:
        _apply_palette_only(args)
        seed = _seed_arg(args)
        weakness, weapons, palette = args.weakness, args.weapons, args.palette
        visualizer = args.visualizer
    export_roms(args.game_dir, dest, source=args.source, target=args.game,
                seed=seed, weakness=weakness, weapons=weapons, palette=palette,
                visualizer=visualizer, romhacks=args.romhack,
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
    "Randomized (fresh seed)",
    "Randomized (custom seed)",
    "Palette shuffle only",
    "Randomized + weakness visualizer",
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


def write_input_map(game_dir: str, mapping: dict) -> None:
    """Persist btn_* values into mmlc.ini (the proxy reads them at load)."""
    ini_set(game_dir, {k: v for k, v in mapping.items() if v})


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

    cfg = gui_config_load()
    logq: "queue.Queue[str]" = queue.Queue()

    root = tk.Tk()
    root.title("20XX")
    root.geometry("800x620")
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass

    game_dir = tk.StringVar(value=cfg.get("game_dir") or args.game_dir or game_dir_default())
    core = tk.StringVar(value=cfg.get("core") or "")
    steamless_cli = tk.StringVar(value=cfg.get("steamless") or "")
    g0 = romtable.GAMES[GAME_ORDER[0]]
    game_label = tk.StringVar(value=f"{g0.title}  [{g0.region}]")
    mode = tk.StringVar(value=GUI_MODES[0])
    seed = tk.StringVar(value="")
    hack = tk.StringVar(value="<none>")
    game_labels = {f"{romtable.GAMES[k].title}  [{romtable.GAMES[k].region}]": k
                   for k in GAME_ORDER}

    ttk.Label(root, text="20XX", font=("Helvetica", 16, "bold")).pack(pady=(10, 0))
    ttk.Label(root, text="Mega Man Legacy Collection — Mesen launcher").pack()

    f1 = ttk.LabelFrame(root, text="1. Install location")
    f1.pack(fill="x", padx=10, pady=6)
    ttk.Entry(f1, textvariable=game_dir).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
    f1.columnconfigure(0, weight=1)

    def browse_dir():
        d = filedialog.askdirectory(initialdir=game_dir.get() or os.path.expanduser("~"))
        if d:
            game_dir.set(d)
            refresh_hacks()

    ttk.Button(f1, text="Browse", command=browse_dir).grid(row=0, column=1, padx=4)
    ttk.Button(f1, text="Auto-detect",
               command=lambda: (game_dir.set(game_dir_default()), refresh_hacks())
               ).grid(row=0, column=2, padx=4)

    ttk.Label(f1, text="Steamless.CLI.exe").grid(row=1, column=0, sticky="w",
                                                 padx=6, pady=(0, 6))
    ttk.Entry(f1, textvariable=steamless_cli).grid(row=2, column=0, sticky="ew",
                                                   padx=6, pady=(0, 6))

    def browse_steamless():
        f = filedialog.askopenfilename(
            initialdir=os.path.expanduser("~"),
            title="Select Steamless.CLI.exe",
            filetypes=[("Steamless CLI", "*.exe"), ("All files", "*")])
        if f:
            steamless_cli.set(f)

    ttk.Button(f1, text="Browse",
               command=browse_steamless).grid(row=2, column=1, padx=4, pady=(0, 6))
    ttk.Button(f1, text="Auto-detect",
               command=lambda: steamless_cli.set(_detect_steamless() or "")
               ).grid(row=2, column=2, padx=4, pady=(0, 6))

    f2 = ttk.LabelFrame(root, text="2. Mesen core (user-supplied; GPLv3, not bundled)")
    f2.pack(fill="x", padx=10, pady=6)
    ttk.Entry(f2, textvariable=core).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
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

    f3 = ttk.Frame(root)
    f3.pack(fill="x", padx=10, pady=2)
    ttk.Button(f3, text="Setup / Repair", command=lambda: run(setup_task)).pack(side="left")
    ttk.Button(f3, text="Verify install", command=lambda: run(verify_task)).pack(side="left", padx=6)

    f4 = ttk.LabelFrame(root, text="3. Options")
    f4.pack(fill="x", padx=10, pady=6)
    ttk.Label(f4, text="Game").grid(row=0, column=0, sticky="w", padx=6, pady=4)
    ttk.Combobox(f4, textvariable=game_label, values=list(game_labels),
                 state="readonly", width=26).grid(row=0, column=1, padx=6)
    ttk.Label(f4, text="Mode").grid(row=0, column=2, sticky="w", padx=6)
    ttk.Combobox(f4, textvariable=mode, values=GUI_MODES,
                 state="readonly", width=30).grid(row=0, column=3, padx=6)
    ttk.Label(f4, text="Seed (5 letters)").grid(row=1, column=0, sticky="w", padx=6, pady=4)
    vcmd = (root.register(lambda s: len(s) <= 5 and (s == "" or s.isalpha())), "%P")
    sf = ttk.Frame(f4)
    sf.grid(row=1, column=1, sticky="w", padx=6)
    ttk.Entry(sf, textvariable=seed, width=8, validate="key",
              validatecommand=vcmd).pack(side="left")
    ttk.Button(sf, text="Random", command=lambda: seed.set(random_seed())).pack(side="left", padx=4)
    ttk.Label(f4, text="ROM hack").grid(row=1, column=2, sticky="w", padx=6)
    hack_cb = ttk.Combobox(f4, textvariable=hack, values=["<none>"],
                           state="readonly", width=30)
    hack_cb.grid(row=1, column=3, padx=6)
    oc_follow = tk.BooleanVar(value=bool(cfg.get("oc_follow", True)))
    oc_turbo = tk.StringVar(value=str(cfg.get("oc_turbo", 262)))
    ttk.Checkbutton(f4, text="Overclock: follow in-game CPU SPEED",
                    variable=oc_follow).grid(row=2, column=0, columnspan=2,
                                             sticky="w", padx=6, pady=4)
    ttk.Label(f4, text="Turbo scanlines").grid(row=2, column=2, sticky="w", padx=6)
    ttk.Entry(f4, textvariable=oc_turbo, width=8).grid(row=2, column=3,
                                                       sticky="w", padx=6)

    f6 = ttk.LabelFrame(root, text="4. Controls (NES button -> Xbox button)")
    f6.pack(fill="x", padx=10, pady=6)
    input_vars: dict = {}
    saved_map = cfg.get("input_map", DEFAULT_INPUT_MAP)
    for i, key in enumerate(["btn_a", "btn_b", "btn_select", "btn_start",
                             "btn_up", "btn_down", "btn_left", "btn_right"]):
        r, c = divmod(i, 4)
        ttk.Label(f6, text=INPUT_LABELS[key]).grid(row=r, column=c * 2,
                                                   sticky="e", padx=4, pady=3)
        v = tk.StringVar(value=saved_map.get(key, DEFAULT_INPUT_MAP[key]))
        ttk.Combobox(f6, textvariable=v, values=INPUT_CHOICES, state="readonly",
                     width=10).grid(row=r, column=c * 2 + 1, sticky="w", padx=4)
        input_vars[key] = v
    ttk.Button(f6, text="Save controls", command=lambda: run(save_controls)
               ).grid(row=2, column=0, columnspan=2, pady=4, sticky="w")

    f5 = ttk.Frame(root)
    f5.pack(fill="x", padx=10, pady=4)
    ttk.Button(f5, text="Prepare & Launch", command=lambda: run(launch_task)).pack(side="left")
    ttk.Button(f5, text="Open romhacks folder",
               command=lambda: run(open_romhacks)
               ).pack(side="left", padx=6)
    ttk.Button(f5, text="Rescan hacks",
               command=lambda: (refresh_hacks(), logq.put("[20xx] romhack list refreshed"))
               ).pack(side="left", padx=6)
    ttk.Button(f5, text="Export ROMs...",
               command=lambda: run(export_task)
               ).pack(side="left", padx=6)

    log = ScrolledText(root, height=15, state="disabled", font=("monospace", 9))
    log.pack(fill="both", expand=True, padx=10, pady=(4, 10))

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

    def refresh_hacks(*_):
        key = game_labels.get(game_label.get(), GAME_ORDER[0])
        found = [os.path.basename(h) for h in discover_romhacks(game_dir.get(), key)]
        hack_cb["values"] = ["<none>"] + found + (["<all>"] if found else [])
        hack.set("<none>")

    game_label.trace_add("write", lambda *a: refresh_hacks())

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
        m = {k: v.get() for k, v in input_vars.items()}
        gd = game_dir.get().strip()
        if gd:
            write_input_map(gd, m)
        c = dict(gui_config_load())
        c.update({"game_dir": gd, "core": core.get().strip(),
                  "steamless": steamless_cli.get().strip(), "input_map": m})
        gui_config_save(c)
        logq.put("[20xx] controls saved: "
                 + ", ".join(f"{k[4:]}={v}" for k, v in m.items()))

    def setup_task():
        gd = game_dir.get().strip()
        if not gd:
            logq.put("Set the install folder first.")
            return
        logq.put(f"[20xx] install: {gd}")
        ensure_proteus_unpacked(gd, steamless_cli.get().strip() or None,
                                status=lambda m: logq.put(m))
        src = find_source(gd)
        logq.put(f"[20xx] ROM source: {os.path.basename(src)}")
        install_proxy(gd)
        c = core.get().strip()
        if c:
            ini_set(gd, {"mesen": "1", "mesen_host": "127.0.0.1",
                         "mesen_port": str(DEFAULT_PORT), "mesen_core": c,
                         "mesen_defer_load": "1", "mesen_null_rom": "1"})
            logq.put(f"[20xx] mesen core: {c}")
            if not check_core(c):
                logq.put("WARNING: core is missing MMLC hooks (see mesen2_capture.patch)")
        try:
            turbo = int(oc_turbo.get().strip() or "262")
        except ValueError:
            turbo = 262
        ini_set(gd, {"mesen_overclock_follow": "1" if oc_follow.get() else "0",
                     "mesen_overclock_turbo": str(turbo)})
        logq.put(f"[20xx] overclock: follow in-game CPU SPEED="
                 f"{oc_follow.get()} turbo={turbo} scanlines")
        logq.put(f"[20xx] romhacks folder: {ensure_romhacks_dir(gd)}")
        write_input_map(gd, {k: v.get() for k, v in input_vars.items()})
        host = ensure_host()
        if host:
            logq.put(f"[20xx] mesen_host: {host}")
        written = extract(gd, os.path.join(gd, "roms"), None, None, quiet=True)
        logq.put(f"[20xx] extracted {len(written)} ROM(s) -> {os.path.join(gd, 'roms')}")
        c2 = dict(gui_config_load())
        c2.update({"game_dir": gd, "core": c,
                   "steamless": steamless_cli.get().strip(),
                   "oc_follow": bool(oc_follow.get()), "oc_turbo": turbo,
                   "input_map": {k: v.get() for k, v in input_vars.items()}})
        gui_config_save(c2)
        logq.put("[20xx] setup complete.")

    def verify_task():
        gd = game_dir.get().strip()
        pe = PE(find_source(gd))
        bad = 0
        for k in GAME_ORDER:
            g = romtable.GAMES[k]
            crc = patcher.crc32(patcher.read_prg(pe, g))
            want = romtable.ORIGINAL_PRG_CRC.get(k)
            ok = want is None or crc == want
            bad += 0 if ok else 1
            logq.put(f"  {k:4} {g.title:14} crc={crc:#010x} "
                     f"{'ok' if ok else 'MISMATCH'}")
        logq.put(f"[20xx] verify: {len(GAME_ORDER) - bad}/{len(GAME_ORDER)} ok")

    def gui_patch_opts(gd):
        """Build the patch options from the GUI widgets for one game."""
        key = game_labels.get(game_label.get(), GAME_ORDER[0])
        m = mode.get()
        opts: dict = {"target": key, "auto_romhacks": False}
        if m == "Randomized (fresh seed)":
            opts["seed"] = random_seed()
        elif m == "Randomized (custom seed)":
            opts["seed"] = seed.get().strip() or random_seed()
        elif m == "Palette shuffle only":
            opts.update(seed=random_seed(), weakness=False, weapons=False, palette=True)
        elif m == "Randomized + weakness visualizer":
            opts.update(seed=random_seed(), visualizer=True)
        hs = hack.get()
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
                hs = "<all>"
            else:
                logq.put(f"[20xx] no patches in romhacks/{key}/ — drop .ips/.bps there")
                return key, m, None, hs
        return key, m, opts, hs

    def launch_task():
        gd = game_dir.get().strip()
        key, m, opts, hs = gui_patch_opts(gd)
        if opts is None:
            return
        write_input_map(gd, {k: v.get() for k, v in input_vars.items()})
        logq.put(f"[20xx] {key}: {m} seed={opts.get('seed', '-')} hack={hs}")
        prepare_roms(gd, os.path.join(gd, "roms"), **opts)
        logq.put("[20xx] launching collection...")
        try:
            ensure_mesen_host(gd)
        except Exception as ex:
            logq.put(f"[20xx] host start failed (continuing): {ex}")
        launch(args.appid)

    def export_task():
        gd = game_dir.get().strip()
        if not gd:
            logq.put("Set the install folder first.")
            return
        key, m, opts, hs = gui_patch_opts(gd)
        if opts is None:
            return
        dest = filedialog.askdirectory(
            initialdir=os.path.expanduser("~"),
            title="Export ROMs to folder")
        if not dest:
            logq.put("[20xx] export cancelled")
            return
        logq.put(f"[20xx] {key}: {m} seed={opts.get('seed', '-')} hack={hs}")
        dest = os.path.join(dest, "MMLC-ROMs")
        export_roms(gd, dest, **opts)
        logq.put(f"[20xx] open this folder in your emulator: {dest}")

    refresh_hacks()
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
    sp.add_argument("--visualizer", action="store_true",
                    help="apply the weakness visualizer IPS (MM1)")
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
                             "Mesen runtime hooks; also done by Setup/Repair)")
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
    st.add_argument("--slot", type=int, default=None,
                    help="save-state slot (default 0)")
    st.set_defaults(func=cmd_states)
    bc = sub.add_parser("build-core",
                        help="clone+patch+build the Mesen2 core (MesenCore.so)")
    bc.add_argument("--dir", default=None, help="where to clone/build Mesen2")
    bc.add_argument("--jobs", type=int, default=None)
    bc.set_defaults(func=cmd_build_core)
    sub.add_parser("gui", help="Tk launcher").set_defaults(func=cmd_gui)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None):
        return interactive(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
