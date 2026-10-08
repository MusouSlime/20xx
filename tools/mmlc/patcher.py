"""ROM extraction/insertion and the overlay NOP for MMLC1 Proteus.exe."""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Optional, Tuple

from . import romtable
from .pe import PE

# mov byte ptr [eax+ecx], 0xDF  -- the runtime callee-ret (0xDF) stub writer.
OVERLAY_STORE = bytes.fromhex("c60408df")
NOP4 = b"\x90\x90\x90\x90"


class PatcherError(RuntimeError):
    pass


def find_overlay_store(data: bytes) -> Tuple[int, ...]:
    out = []
    i = data.find(OVERLAY_STORE)
    while i != -1:
        out.append(i)
        i = data.find(OVERLAY_STORE, i + 1)
    return tuple(out)


def overlay_is_nop(data: bytes) -> bool:
    return len(find_overlay_store(data)) == 0


def nop_overlay(data: bytearray) -> int:
    """NOP every 0xDF stub store. Returns number of sites patched."""
    hits = find_overlay_store(bytes(data))
    for off in hits:
        data[off : off + 4] = NOP4
    return len(hits)


# -- ROM access --------------------------------------------------------------

def read_game(pe: PE, game: romtable.Game) -> bytes:
    """Return the headerless PRG(+CHR) body for *game*."""
    body = pe.read_at(game.prg.offset, game.prg.size)
    if game.chr is not None:
        body += pe.read_at(game.chr.offset, game.chr.size)
    return body


def read_prg(pe: PE, game: romtable.Game) -> bytes:
    return pe.read_at(game.prg.offset, game.prg.size)


def read_chr(pe: PE, game: romtable.Game) -> Optional[bytes]:
    if game.chr is None:
        return None
    return pe.read_at(game.chr.offset, game.chr.size)


def body_size(game: romtable.Game) -> int:
    return game.prg.size + (game.chr.size if game.chr else 0)


def extract_ines(pe: PE, game: romtable.Game) -> bytes:
    return game.ines_header + read_game(pe, game)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def write_game(data: bytearray, game: romtable.Game, body: bytes) -> None:
    """Overwrite a game body in an exe buffer. Size must match exactly."""
    want = body_size(game)
    if len(body) != want:
        raise PatcherError(
            f"{game.key}: body size {len(body):#x} != expected {want:#x} "
            "(ROM patches must be same-size)"
        )
    data[game.prg.offset : game.prg.end] = body[: game.prg.size]
    if game.chr is not None:
        data[game.chr.offset : game.chr.end] = body[game.prg.size :]


@dataclass
class CheckResult:
    key: str
    title: str
    region: str
    prg_crc: int
    chr_crc: Optional[int]
    body_size: int


def check_all(pe: PE) -> list:
    out = []
    for g in romtable.GAMES.values():
        prg = read_prg(pe, g)
        chr_ = read_chr(pe, g)
        out.append(
            CheckResult(
                g.key, g.title, g.region, crc32(prg),
                crc32(chr_) if chr_ else None, body_size(g),
            )
        )
    return out
