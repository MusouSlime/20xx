"""Thin wrapper around Steamless v3.1.0.5 running under Mono.

Steamless is a user-supplied tool; this module can locate it, or fetch the
official release into the local cache on request.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STEAMLESS_VERSION = "3.1.0.5"
RELEASE_URL = (
    "https://github.com/atom0s/Steamless/releases/download/v"
    f"{STEAMLESS_VERSION}/Steamless.v{STEAMLESS_VERSION}.-.by.atom0s.zip"
)
CACHE_DIR = os.path.expanduser("~/.cache/mmlc-mesen/steamless")


class SteamlessError(RuntimeError):
    pass


def find_cli(explicit: Optional[str] = None) -> str:
    if explicit:
        if not os.path.exists(explicit):
            raise SteamlessError(f"Steamless CLI not found: {explicit}")
        return explicit
    for cand in (
        os.environ.get("STEAMLESS_CLI"),
        os.path.join(REPO_ROOT, "tools", "steamless", "Steamless.CLI.exe"),
        os.path.join(CACHE_DIR, "Steamless.CLI.exe"),
        "/tmp/opencode/steamless/Steamless.CLI.exe",
        "./tools/steamless/Steamless.CLI.exe",
    ):
        if cand and os.path.exists(cand):
            return cand
    raise SteamlessError(
        "Steamless.CLI.exe not found; set STEAMLESS_CLI or pass --steamless\n"
        "  Download: https://github.com/atom0s/Steamless/releases (Steamless.CLI.exe)\n"
        f"  Suggested location: {os.path.join(REPO_ROOT, 'tools', 'steamless', 'Steamless.CLI.exe')}"
    )


def fetch(dest: Optional[str] = None, url: Optional[str] = None,
          status=print) -> str:
    """Download the official Steamless release and extract it to *dest*.

    The CLI needs its plugin DLLs **next to** the exe, so `Plugins/*.dll` are
    copied up alongside `Steamless.CLI.exe`. Returns the CLI path.
    """
    dest = dest or CACHE_DIR
    url = url or RELEASE_URL
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, ".steamless-download.zip")
    status(f"[20xx] downloading Steamless v{STEAMLESS_VERSION} -> {dest}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception as ex:  # noqa: BLE001 - surface any network error
        raise SteamlessError(f"download failed: {ex}\n  url: {url}") from ex
    try:
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    plugins = os.path.join(dest, "Plugins")
    if os.path.isdir(plugins):
        for name in os.listdir(plugins):
            if name.lower().endswith(".dll"):
                shutil.copy2(os.path.join(plugins, name),
                             os.path.join(dest, name))
    cli = os.path.join(dest, "Steamless.CLI.exe")
    if not os.path.exists(cli):
        raise SteamlessError(f"Steamless.CLI.exe missing after extraction: {dest}")
    status(f"[20xx] Steamless ready: {cli}")
    return cli


def ensure_cli(explicit: Optional[str] = None, *, fetch_if_missing: bool = True,
               status=print) -> str:
    """Return a usable Steamless.CLI.exe, fetching the official build if needed."""
    try:
        return find_cli(explicit)
    except SteamlessError:
        if not fetch_if_missing:
            raise
    return fetch(status=status)


def unpack(
    exe: str,
    steamless_cli: Optional[str] = None,
    mono: Optional[str] = None,
    out: Optional[str] = None,
) -> str:
    """Unpack *exe* with Steamless. Returns path to the unpacked file.

    Steamless writes ``<exe>.unpacked.exe`` next to the input by default.
    """
    cli = find_cli(steamless_cli)
    launcher = [] if sys.platform.startswith("win") else [find_mono(mono)]
    cmd = launcher + [cli, "--unpack", os.path.abspath(exe)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    produced = exe + ".unpacked.exe"
    if not os.path.exists(produced):
        raise SteamlessError(
            f"Steamless produced no output.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if out:
        shutil.move(produced, out)
        return out
    return produced


def find_mono(explicit: Optional[str] = None) -> str:
    """Return an absolute path to mono (Steamless.CLI is a .NET assembly).

    Steam launches the prelaunch hook with a minimal PATH (no Homebrew), so
    ``mono`` must be resolved by absolute path.
    """
    if sys.platform.startswith("win"):
        return ""
    cands = [explicit, os.environ.get("MONO")]
    cands.append(shutil.which("mono"))
    cands += [
        "/usr/bin/mono",
        "/usr/local/bin/mono",
        "/home/linuxbrew/.linuxbrew/bin/mono",
        os.path.expanduser("~/.linuxbrew/bin/mono"),
        "/home/linuxbrew/.linuxbrew/opt/mono/bin/mono",
        "/snap/bin/mono",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise SteamlessError(
        "mono not found (Steamless.CLI.exe is a .NET assembly).\n"
        "  Install mono (e.g. `brew install mono` or your package manager)\n"
        "  or set $MONO to its absolute path."
    )
