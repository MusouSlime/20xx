"""Verified ROM layout table for MMLC1 Proteus.exe.

Offsets are **file offsets** into the unpacked exe. Values were re-verified by
locating the exact extracted ROM bytes in the binary (see docs/RE_FACTS.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

NES_MAGIC = b"NES\x1a"


def ines(prg16k: int, chr8k: int, flags6: int, flags7: int = 0) -> bytes:
    return NES_MAGIC + bytes([prg16k, chr8k, flags6, flags7]) + b"\x00" * 8


@dataclass(frozen=True)
class Blob:
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True)
class Game:
    key: str
    title: str
    region: str  # "US" | "JP"
    prg: Blob
    chr: Optional[Blob]
    ines_header: bytes

    @property
    def has_chr(self) -> bool:
        return self.chr is not None


# Exact iNES headers matching the community extractor output.
_H_MM1 = ines(0x08, 0x00, 0x21)
_H_MM2 = ines(0x10, 0x00, 0x10)
_H_MM3 = ines(0x10, 0x10, 0x40)
_H_MM4 = ines(0x20, 0x00, 0x40)
_H_MM5 = ines(0x10, 0x20, 0x40)
_H_MM6 = ines(0x20, 0x00, 0x40)

GAMES: Dict[str, Game] = {
    "mm1": Game("mm1", "Mega Man 1", "US", Blob(0x2AF2B0, 0x20000), None, _H_MM1),
    "mm2": Game("mm2", "Mega Man 2", "US", Blob(0x08F170, 0x40000), None, _H_MM2),
    "mm3": Game("mm3", "Mega Man 3", "US", Blob(0x0CF1B0, 0x40000), Blob(0x10F1B0, 0x20000), _H_MM3),
    "mm4": Game("mm4", "Mega Man 4", "US", Blob(0x12F1F0, 0x80000), None, _H_MM4),
    "mm5": Game("mm5", "Mega Man 5", "US", Blob(0x1AF230, 0x40000), Blob(0x1EF230, 0x40000), _H_MM5),
    "mm6": Game("mm6", "Mega Man 6", "US", Blob(0x22F270, 0x80000), None, _H_MM6),
    # JP offsets are the ctor source pointers (PRG file offset + 0x1200 RVA);
    # an earlier revision had them 0x18 too low (the brief was right). See
    # docs/RE_FACTS.md "JP offset correction".
    "rk1": Game("rk1", "Rockman 1", "JP", Blob(0x512648, 0x20000), None, _H_MM1),
    "rk2": Game("rk2", "Rockman 2", "JP", Blob(0x2F2508, 0x40000), None, _H_MM2),
    "rk3": Game("rk3", "Rockman 3", "JP", Blob(0x332548, 0x40000), Blob(0x372548, 0x20000), _H_MM3),
    "rk4": Game("rk4", "Rockman 4", "JP", Blob(0x392588, 0x80000), None, _H_MM4),
    "rk5": Game("rk5", "Rockman 5", "JP", Blob(0x4125C8, 0x40000), Blob(0x4525C8, 0x40000), _H_MM5),
    "rk6": Game("rk6", "Rockman 6", "JP", Blob(0x492608, 0x80000), None, _H_MM6),
}

# CRC32 anchors measured on the pristine binary (packed or unpacked; identical).
# A game whose PRG CRC differs is considered locally modified/randomized.
ORIGINAL_PRG_CRC = {
    "mm1": 0x1C47D202, "mm2": 0xCF5DE2BC, "mm3": 0xB9B33733,
    "mm4": 0x0FAA8F73, "mm5": 0x0674145D, "mm6": 0xDCC74EF2,
    "rk1": 0xD31DC910, "rk2": 0x6150517C, "rk3": 0x1D2E5018,
    "rk4": 0xF161A5D8, "rk5": 0x05CF9EB0, "rk6": 0x2D664D99,
}
ORIGINAL_CHR_CRC = {
    "mm3": 0x4028916E, "mm5": 0x25E0AE72, "rk3": 0x36F3CE63,
    "rk5": 0x9CB89B85,
}

# Games that pair a JP/US identity for a given installed language.
ALIASES = {
    "megaman1": "mm1", "megaman2": "mm2", "megaman3": "mm3",
    "megaman4": "mm4", "megaman5": "mm5", "megaman6": "mm6",
    "rockman1": "rk1", "rockman2": "rk2", "rockman3": "rk3",
    "rockman4": "rk4", "rockman5": "rk5", "rockman6": "rk6",
}


def resolve(key: str) -> Game:
    k = key.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    k = ALIASES.get(k, k)
    if k not in GAMES:
        raise KeyError(f"unknown game {key!r}; known: {', '.join(GAMES)}")
    return GAMES[k]
