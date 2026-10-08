"""MMLC1 (Mega Man Legacy Collection 1) patch-only mod toolkit.

Never bundles or redistributes the game binary, ROMs, or assets; every
operation acts on the user's own legally-owned install.
"""
from __future__ import annotations

from typing import List

from . import patcher, romtable
from .pe import PE

__version__ = "0.1.0"


class CanaryError(RuntimeError):
    pass


def canary(pe: PE, require_pristine: bool = False) -> List[str]:
    """Check the ROM anchors and return per-game findings.

    ROM CRCs differing from the pristine build means the game was locally
    patched/randomized; this does not invalidate the offsets. With
    *require_pristine* set, any mismatch raises :class:`CanaryError`.
    """
    findings: List[str] = []
    for g in romtable.GAMES.values():
        prg = patcher.read_prg(pe, g)
        crc = patcher.crc32(prg)
        want = romtable.ORIGINAL_PRG_CRC[g.key]
        if crc == want:
            findings.append(f"OK   {g.key}: PRG crc {crc:#010x}")
        else:
            findings.append(
                f"MOD  {g.key}: PRG crc {crc:#010x} (pristine {want:#010x})"
            )
    if require_pristine and any(f.startswith("MOD") for f in findings):
        raise CanaryError("binary is not a pristine recognized build:\n" + "\n".join(findings))
    return findings
