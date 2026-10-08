"""Seed-driven ROM randomizers.

Currently MM1 only. Offsets were validated against the *sanitized* MMLC ROM
(the packed ``Proteus.exe.orig.bak`` keeps ``.rdata`` intact): the vanilla
damage charts at PRG ``0x1FDEE`` and the reward/weakness tables match the
original US dump byte-for-byte, so the community algorithm (avvie's
MegamanRandomizer) ports directly.

Everything operates on a headerless PRG ``bytearray`` in place and is fully
deterministic for a given ``seed``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_PATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")

# --- MM1 layout (PRG offsets) --------------------------------------------- #
MM1_PRG_SIZE = 0x20000
MM1_DAMAGE_TABLE = 0x1FDEE      # 6 bosses x 8 weapons, order P C I B F E G M
MM1_WEAKNESS_TABLE = 0x1BFCC    # boss-defeated / weakness table (6 bytes)
MM1_WEAPON_REWARD = 0x1C148     # weapon reward table (6 bytes)
MM1_GUTSMAN_FIX = 0x1B69E       # Guts Man level-select sprite byte
MM1_WEAPON_SELECT = 0x1C130     # stage-clear weapon select (stageclear patch)

# Mega Man sprite/weapon palette (avvie PaletteGenerator). The default two
# bytes are the NES palette indices for Mega Man's light/dark blue.
MM1_PAL_MAIN = (0x2C, 0x11)     # default light/dark colour
MM1_PAL_BOSSROOM = (0x1C29B, 0x1C2A0)  # hardcoded boss-room palette loads
MM1_WEAPON_PAL = 0x1D487        # 7 weapon palettes (C I B F E G M), 2 bytes each
MM1_PAL_OFFSETS = [             # every place the default blue is loaded
    0x0CB1, 0x0CE1, 0x0DDD, 0x4CB1, 0x4CE1, 0x4D9B,
    0x8CB1, 0x8CE1, 0xCCB1, 0xCCE1, 0x10CB1, 0x10CE1,
    0x14CB1, 0x1D485, 0x1D493, 0x14CE1,
]
# reference light/dark colour per weapon (P C I B F E G M)
MM1_PRIMARY_PAL = [
    0x2C, 0x11, 0x30, 0x00, 0x30, 0x12, 0x30, 0x19,
    0x28, 0x16, 0x38, 0x00, 0x30, 0x17, 0x2C, 0x11,
]

WEAPON_ORDER = "PCIBFEGM"
# weakness value per weapon index (P C I B F E G M); P/M have none
WEAKNESS_VALUE = {1: 0x20, 2: 0x10, 3: 0x02, 4: 0x40, 5: 0x04, 6: 0x08}
WEAPON_SPRITE = {
    0: "rockbuster", 1: "rollingcutter", 2: "iceslasher", 3: "hyperbomb",
    4: "firestorm", 5: "thunderbeam", 6: "superarm", 7: "magnetbeam",
}
# reward byte per weapon index (C I B F E G; P/M never drop)
REWARD_BY_INDEX = {1: 0x20, 2: 0x10, 3: 0x02, 4: 0x40, 5: 0x04, 6: 0x08}
REWARD_SPRITE = {
    0x20: "rollingcutter", 0x10: "iceslasher", 0x02: "hyperbomb",
    0x40: "firestorm", 0x04: "thunderbeam", 0x08: "superarm",
}
REWARD_TO_SELECT = {0x20: 1, 0x10: 2, 0x02: 3, 0x40: 4, 0x04: 5, 0x08: 6}

# boss index order == database entry order (DB-RM1-NAME-25..30)
MM1_BOSS_ENTRIES = [
    "DB-RM1-NAME-25", "DB-RM1-NAME-26", "DB-RM1-NAME-27",
    "DB-RM1-NAME-28", "DB-RM1-NAME-29", "DB-RM1-NAME-30",
]
# Cut/Elec/Guts have throwable blocks (Super Arm must land on one of these)
MM1_THROWABLE = [0, 4, 5]


class RandomizerError(RuntimeError):
    pass


@dataclass
class Spoiler:
    game: str
    seed: str
    weaknesses: Dict[str, str] = field(default_factory=dict)
    rewards: Dict[str, str] = field(default_factory=dict)

    def lines(self) -> List[str]:
        out = [f"game {self.game} seed {self.seed!r}"]
        for e in MM1_BOSS_ENTRIES:
            w = self.weaknesses.get(e)
            r = self.rewards.get(e)
            out.append(f"  {e}: weakness={w or '-':<14} reward={r or '-'}")
        return out


def _fnv1a(s: str) -> int:
    h = 2166136261
    for c in s.encode("utf-8"):
        h ^= c
        h = (h * 16777619) & 0xFFFFFFFF
    return h


class _Rng:
    """xorshift32 seeded by FNV-1a -- identical to mm1_rando.h so offline
    spoilers match the in-engine result for the same seed."""

    def __init__(self, seed: str):
        self.state = _fnv1a(seed) or 1

    def next(self) -> int:
        x = self.state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        x &= 0xFFFFFFFF
        self.state = x or 0x1234567
        return self.state

    def below(self, n: int) -> int:
        return self.next() % n if n else 0


def _rng(seed: str) -> "_Rng":
    return _Rng(seed)


def _read_charts(prg: bytes) -> List[List[int]]:
    return [
        list(prg[MM1_DAMAGE_TABLE + b * 8 : MM1_DAMAGE_TABLE + b * 8 + 8])
        for b in range(6)
    ]


def _write_charts(prg: bytearray, charts: List[List[int]]) -> None:
    for b, chart in enumerate(charts):
        prg[MM1_DAMAGE_TABLE + b * 8 : MM1_DAMAGE_TABLE + b * 8 + 8] = bytes(chart)


def _argmax(chart: List[int]) -> int:
    return chart.index(max(chart))


def _apply_ips(buf: bytearray, ips: bytes, adjust: int) -> None:
    if ips[:5] != b"PATCH":
        raise RandomizerError("not an IPS file")
    p = 5
    while p + 3 <= len(ips):
        if ips[p : p + 3] == b"EOF":
            break
        off = int.from_bytes(ips[p : p + 3], "big") + adjust
        p += 3
        size = int.from_bytes(ips[p : p + 2], "big")
        p += 2
        if size:
            buf[off : off + size] = ips[p : p + size]
            p += size
        else:
            rle = int.from_bytes(ips[p : p + 2], "big")
            val = ips[p + 2]
            p += 3
            buf[off : off + rle] = bytes([val]) * rle


def apply_visualizer(prg: bytearray) -> None:
    """Apply the weakness-visualizer IPS (stage-select weakness colors)."""
    with open(os.path.join(_PATCH_DIR, "WEAKNESSVISUALIZER.ips"), "rb") as fh:
        _apply_ips(prg, fh.read(), -0x10)


def _shuffle_weakness(prg: bytearray, rng: "_Rng") -> List[List[int]]:
    charts = _read_charts(prg)
    for i in range(5, 0, -1):
        j = rng.below(i + 1)
        charts[i], charts[j] = charts[j], charts[i]
    # Super Arm (index 6, 14 dmg) must land on a boss with throwable blocks.
    for i, chart in enumerate(charts):
        if chart[6] == 14:
            j = MM1_THROWABLE[rng.below(3)]
            charts[i], charts[j] = charts[j], charts[i]
            break
    _write_charts(prg, charts)
    return charts


def _shuffle_rewards(
    prg: bytearray, charts: List[List[int]], rng: "_Rng", visualizer: bool = False
) -> List[int]:
    pool = [0x20, 0x10, 0x02, 0x40, 0x04, 0x08]
    table: List[int] = []
    for chart in charts:
        rewardlist = pool[:]
        excl_index = _argmax(chart)
        excl = REWARD_BY_INDEX.get(excl_index)
        if excl is not None and excl in rewardlist:
            rewardlist.remove(excl)
        if not rewardlist:
            prev = table[4]
            table[4] = excl
            table.append(prev)
        else:
            reward = rewardlist[rng.below(len(rewardlist))]
            pool.remove(reward)
            table.append(reward)
    prg[MM1_WEAPON_REWARD : MM1_WEAPON_REWARD + 6] = bytes(table)
    if not visualizer:
        prg[MM1_WEAKNESS_TABLE : MM1_WEAKNESS_TABLE + 6] = bytes(table)
        prg[MM1_GUTSMAN_FIX] = table[5]
    return table


def _palette_pair(rng: "_Rng", c1: int, c2: int) -> List[int]:
    """One randomised ``(light, dark)`` palette pair, matching avvie's app."""
    offset = c1 - c2
    high = 1 + rng.below(2)          # 0x1_ or 0x2_
    low = rng.below(13)              # 0x_0 .. 0x_C
    new_high = high * 16 + low
    new_low = new_high - offset
    if new_low < 0:
        new_low ^= -56               # two's-complement wrap into 0x18..0x37
    if new_low == new_high:
        new_high -= 16
    return [new_high & 0xFF, new_low & 0xFF]


def _shuffle_palette(prg: bytearray, rng: "_Rng") -> None:
    """Randomise Mega Man's sprite + per-weapon palettes from the seed."""
    high, low = _palette_pair(rng, *MM1_PAL_MAIN)
    prg[MM1_PAL_BOSSROOM[0]] = high
    prg[MM1_PAL_BOSSROOM[1]] = low
    for off in MM1_PAL_OFFSETS:
        prg[off] = high
        prg[off + 1] = low
    x = 2
    while x < 15:
        hi, lo = _palette_pair(rng, MM1_PRIMARY_PAL[x], MM1_PRIMARY_PAL[x + 1])
        prg[MM1_WEAPON_PAL + (x - 2)] = hi
        prg[MM1_WEAPON_PAL + (x - 2) + 1] = lo
        x += 2


def randomize_mm1(
    prg: bytearray,
    seed: str,
    *,
    weakness: bool = True,
    weapons: bool = True,
    visualizer: bool = False,
    palette: bool = True,
) -> Spoiler:
    """Randomize an MM1 PRG in place; return the spoiler."""
    if len(prg) != MM1_PRG_SIZE:
        raise RandomizerError(
            f"MM1 PRG must be {MM1_PRG_SIZE:#x} bytes, got {len(prg):#x}"
        )
    rng = _rng(seed)
    charts = _read_charts(prg)
    if weakness:
        charts = _shuffle_weakness(prg, rng)
    spoiler = Spoiler("mm1", seed)
    for i, chart in enumerate(charts):
        spoiler.weaknesses[MM1_BOSS_ENTRIES[i]] = WEAPON_SPRITE[_argmax(chart)]
    if visualizer:
        apply_visualizer(prg)
        for i, chart in enumerate(charts):
            prg[MM1_WEAKNESS_TABLE + i] = WEAKNESS_VALUE[_argmax(chart)]
    if weapons:
        table = _shuffle_rewards(prg, charts, rng, visualizer=visualizer)
        for i, reward in enumerate(table):
            spoiler.rewards[MM1_BOSS_ENTRIES[i]] = REWARD_SPRITE[reward]
    if palette:
        _shuffle_palette(prg, rng)
    return spoiler


# --- registry -------------------------------------------------------------- #

GAMES = {
    "mm1": {"prg_size": MM1_PRG_SIZE, "fn": randomize_mm1},
}


def resolve(key: str) -> str:
    k = key.strip().lower()
    if k not in GAMES:
        raise RandomizerError(f"no randomizer for {key!r}; known: {', '.join(GAMES)}")
    return k


def randomize(key: str, prg: bytearray, seed: str, **kw) -> Spoiler:
    return GAMES[resolve(key)]["fn"](prg, seed, **kw)


# database.xml game id per randomizer key
GAME_DB_ID = {"mm1": "1"}


def load_prg_from(path: str, key: str) -> bytearray:
    """Read a pristine headerless PRG from a Proteus.exe or .orig.bak.

    ``PE.read_at`` is a raw file read, so the packed ``.orig.bak`` (which keeps
    ``.rdata`` at the same offsets) works directly.
    """
    from . import romtable
    from .pe import PE

    game = romtable.resolve(key)
    pe = PE(path)
    return bytearray(pe.read_at(game.prg.offset, game.prg.size))
