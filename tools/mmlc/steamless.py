"""Thin wrapper around Steamless v3.1.0.5 running under Mono.

Steamless is a user-supplied tool; this module only invokes it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


class SteamlessError(RuntimeError):
    pass


def find_cli(explicit: Optional[str] = None) -> str:
    if explicit:
        if not os.path.exists(explicit):
            raise SteamlessError(f"Steamless CLI not found: {explicit}")
        return explicit
    for cand in (
        os.environ.get("STEAMLESS_CLI"),
        "/tmp/opencode/steamless/Steamless.CLI.exe",
        "./tools/steamless/Steamless.CLI.exe",
    ):
        if cand and os.path.exists(cand):
            return cand
    raise SteamlessError(
        "Steamless.CLI.exe not found; set STEAMLESS_CLI or pass --steamless"
    )


def unpack(
    exe: str,
    steamless_cli: Optional[str] = None,
    mono: str = "mono",
    out: Optional[str] = None,
) -> str:
    """Unpack *exe* with Steamless. Returns path to the unpacked file.

    Steamless writes ``<exe>.unpacked.exe`` next to the input by default.
    """
    cli = find_cli(steamless_cli)
    cmd = [mono, cli, "--unpack", os.path.abspath(exe)]
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
