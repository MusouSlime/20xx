"""Environment discovery for 20xx setup/doctor.

The game is installed by Steam into ``steamapps/common/Suzy``. Steam can keep
that library on any drive, so setup reads ``libraryfolders.vdf`` instead of
assuming the default path. Pure helpers only (no Tk, no game access) so they
are easy to test.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Iterable, List, Optional

INSTALL_DIR_NAME = "Suzy"
GAME_FILES = ("Proteus.exe.orig.bak", "Proteus.exe")

_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')
_LIB_RE = re.compile(r'"libraryfolders"', re.I)


def _decode_vdf_path(path: str) -> str:
    # VDF escapes backslashes (C:\\Games\\Steam); forward slashes need no work.
    return path.replace("\\\\", "\\")


def library_paths(vdf_file: str) -> List[str]:
    """Return library roots listed in a ``libraryfolders.vdf`` (may be empty)."""
    try:
        with open(vdf_file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    if not _LIB_RE.search(text):
        return []
    return [_decode_vdf_path(m.group(1)) for m in _PATH_RE.finditer(text)]


def _registry_steam_path() -> Optional[str]:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    for key, value in (
        (r"Software\Valve\Steam", "SteamPath"),
        (r"Software\Valve\Steam", "InstallPath"),
        (r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                p, _ = winreg.QueryValueEx(k, value)
                if p:
                    return str(p)
        except OSError:
            continue
    return None


def steam_roots() -> List[str]:
    """Known Steam root folders for this platform (deduplicated)."""
    roots: List[str] = []

    def add(p: Optional[str]) -> None:
        if not p:
            return
        p = os.path.abspath(os.path.expanduser(p))
        if p not in roots:
            roots.append(p)

    add(os.environ.get("MMLC_STEAM"))
    add(os.environ.get("STEAM_ROOT"))
    if sys.platform.startswith("win"):
        add(_registry_steam_path())
        for var in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(var)
            if base:
                add(os.path.join(base, "Steam"))
    else:
        add(os.path.expanduser("~/.steam/steam"))
        add(os.path.expanduser("~/.steam/root"))
        add(os.path.expanduser("~/.local/share/Steam"))
        add(os.path.expanduser("~/Library/Application Support/Steam"))
    return [p for p in roots if os.path.isdir(p)]


def install_candidates(roots: Optional[Iterable[str]] = None) -> List[str]:
    """Candidate ``.../steamapps/common/Suzy`` folders, including libraries."""
    root_list = list(roots) if roots is not None else steam_roots()
    libs: List[str] = []
    for root in root_list:
        if not root:
            continue
        if root not in libs:
            libs.append(root)
        for vdf in (os.path.join(root, "steamapps", "libraryfolders.vdf"),
                    os.path.join(root, "config", "libraryfolders.vdf")):
            for lib in library_paths(vdf):
                if lib not in libs:
                    libs.append(lib)
    out: List[str] = []
    for lib in libs:
        for rel in (("steamapps", "common", INSTALL_DIR_NAME),
                    ("common", INSTALL_DIR_NAME),
                    ("SteamApps", "common", INSTALL_DIR_NAME)):
            p = os.path.join(lib, *rel)
            if p not in out:
                out.append(p)
    return out


def has_game_files(game_dir: str) -> bool:
    return any(os.path.exists(os.path.join(game_dir, n)) for n in GAME_FILES)


def find_install(roots: Optional[Iterable[str]] = None) -> Optional[str]:
    """Return the first candidate that actually contains Proteus.exe."""
    for p in install_candidates(roots):
        if has_game_files(p):
            return p
    return None


def proxy_installed(game_dir: str) -> bool:
    """True when the renamed original + our proxy are both present.

    The proxy embeds ``[mmlc]`` log strings; checking one is a cheap way to
    avoid mistaking the untouched Valve DLL for the proxy.
    """
    orig = os.path.join(game_dir, "steam_api_orig.dll")
    proxy = os.path.join(game_dir, "steam_api.dll")
    if not (os.path.exists(orig) and os.path.exists(proxy)):
        return False
    try:
        with open(proxy, "rb") as fh:
            return b"[mmlc]" in fh.read(2 * 1024 * 1024)
    except OSError:
        return False
