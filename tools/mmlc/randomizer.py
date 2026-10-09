"""Seed-driven ROM randomizers.

Offsets were validated against the *sanitized* MMLC ROMs (the packed
``Proteus.exe.orig.bak`` keeps ``.rdata`` intact): the vanilla damage tables are
present byte-for-byte, so the community algorithms port directly.

* ``mm1`` -- full port of avvie/MegamanRandomizer (weakness, rewards, palette,
  visualizer).
* ``mm2`` -- robot-master weakness tables are byte-for-byte parity with
  squid-man/MegaMan2Randomizer2 (``RandomizeU``; PCG seed); reward/palette are
  20xx shuffles of the vanilla tables.
* ``mm3`` -- boss weaknesses are byte-for-byte parity with
  XenoStar54/Mega-Man-3-Randomizer (``random.seed(seed_string)``); weapon
  locations/palette are deterministic 20xx shuffles.
* ``mm4`` -- boss weaknesses (per-weapon damage-table column shuffle), reward
  and palette are 20xx shuffles (no upstream tool exists).
* ``mm5`` -- boss weaknesses are byte-for-byte parity with
  dmarchand/mm5randomizer (``System.Random(int_seed)``) and the weapon-get
  reward is a byte-for-byte port of the same tool's ``WeaponGetRandomizer``
  (offsets remapped to the MMLC ROM); palette is a 20xx shuffle.
* ``mm6`` -- not implemented yet (no upstream tool and its damage tables are
  not yet located).

Everything operates on a headerless PRG ``bytearray`` in place and is fully
deterministic for a given ``seed``.
"""
from __future__ import annotations

import os
import random
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
    order: Optional[List[str]] = None

    def lines(self) -> List[str]:
        out = [f"game {self.game} seed {self.seed!r}"]
        if self.order:
            entries = self.order
        elif self.game == "mm1":
            entries = MM1_BOSS_ENTRIES
        else:
            entries = []
        for e in entries:
            w = self.weaknesses.get(e)
            r = self.rewards.get(e)
            if isinstance(r, (list, tuple)):
                r = "+".join(r)
            if r is not None:
                out.append(f"  {e}: weakness={w or '-':<14} reward={r or '-'}")
            else:
                out.append(f"  {e}: weakness={w or '-'}")
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


# ===========================================================================
# Reference parity
# ---------------------------------------------------------------------------
# MM3 and MM5 weaknesses are byte-for-byte ports of the upstream community
# randomizers, so a given seed reproduces their output:
#   * MM3: XenoStar54/Mega-Man-3-Randomizer (Python; ``random.seed(str)``)
#   * MM5: dmarchand/mm5randomizer        (C#; ``System.Random(int)``)
# Only the boss-weakness tables are ported (the upstream tools randomize many
# other systems). Upstream offsets are *file* offsets into a headered iNES
# image, so the headerless PRG index is ``offset - 0x10``.
# ===========================================================================

_PRG_HDR = 0x10


def _seed_int(seed) -> int:
    """Map a seed to a positive 31-bit int (for the C# reference tools)."""
    if isinstance(seed, int):
        return seed
    return (_fnv1a(str(seed)) & 0x7FFFFFFF) or 1


class _DotNetRandom:
    """The legacy .NET ``System.Random`` (subtractive generator).

    This is the algorithm .NET Framework/.NET Core use for a *seeded*
    ``Random``; it is what dmarchand/mm5randomizer uses, so porting it lets the
    Python tool reproduce the upstream MM5 weakness bytes for an int seed.
    """

    MBIG = 2147483647
    MSEED = 161803398

    def __init__(self, seed: int):
        subtraction = 2147483647 if seed == -2147483648 else abs(int(seed))
        self._sa = [0] * 56
        mj = self.MSEED - subtraction
        self._sa[55] = mj
        mk = 1
        for i in range(1, 55):
            ii = (21 * i) % 55
            self._sa[ii] = mk
            mk = mj - mk
            if mk < 0:
                mk += self.MBIG
            mj = self._sa[ii]
        for _ in range(1, 5):
            for i in range(1, 56):
                self._sa[i] -= self._sa[1 + (i + 30) % 55]
                if self._sa[i] < 0:
                    self._sa[i] += self.MBIG
        self._inext = 0
        self._inextp = 21

    def _internal(self) -> int:
        self._inext += 1
        if self._inext >= 56:
            self._inext = 1
        self._inextp += 1
        if self._inextp >= 56:
            self._inextp = 1
        ret = self._sa[self._inext] - self._sa[self._inextp]
        if ret == self.MBIG:
            ret -= 1
        if ret < 0:
            ret += self.MBIG
        self._sa[self._inext] = ret
        return ret

    def _sample(self) -> float:
        return self._internal() * (1.0 / self.MBIG)

    def next(self, a: Optional[int] = None,
             b: Optional[int] = None) -> int:
        """``Next()`` / ``Next(max)`` / ``Next(min, max)`` (C# argument order)."""
        if b is not None:
            return int(self._sample() * (b - a)) + a
        if a is not None:
            return int(self._sample() * a)
        return self._internal()


# --- MM5: boss weakness tables (dmarchand/mm5randomizer) --------------------
MM5_PRG_SIZE = 0x40000
MM5_WEAPON_TABLES = [                    # (file offset, weapon name)
    (0x2810, "Water Wave"), (0x4810, "Gyro Attack"), (0x6810, "Crystal Eye"),
    (0x8810, "Napalm Bomb"), (0xA810, "Super Arrow"), (0xC810, "Power Stone"),
    (0xE810, "Gravity Hold"), (0x10810, "Charge Kick"), (0x12810, "Star Crash"),
    (0x14810, "Rush Coil"), (0x16810, "Rush Jet"), (0x18810, "Beat"),
]
MM5_ROBOT_MASTERS = [                    # (offset from table base, name)
    (0x69, "Stone Man"), (0x6B, "Charge Man"), (0x6E, "Gyro Man"),
    (0x81, "Gravity Man"), (0x83, "Crystal Man"), (0x86, "Wave Man"),
    (0x89, "Napalm Man"), (0x8D, "Star Man"),
]
MM5_WILY_BOSSES = [
    (0x91, "Dark Man 2"), (0x93, "Dark Man 3"), (0x96, "Dark Man 1"),
    (0x98, "Dark Man 4"), (0xA0, "Wily Press"), (0x4F, "Big Pets"),
    (0x71, "Big Pets Body"), (0x7A, "Big Pets Fuselage"), (0x4F, "Big Pets"),
    (0x7C, "Q9"),
]


def _mm5_shuffle_weakness(prg: bytearray, rng) -> Dict[str, str]:
    usable = [w for w in MM5_WEAPON_TABLES
              if w[1] not in ("Rush Coil", "Rush Jet")]
    masters = list(MM5_ROBOT_MASTERS)
    other = list(MM5_ROBOT_MASTERS)
    spoiler: Dict[str, str] = {}
    for off, weapon in usable:
        base = off - _PRG_HDR
        weak = None
        if masters:
            weak = masters[rng.next(len(masters))]
            prg[base + weak[0]] = 0x04
            spoiler[weak[1]] = weapon
            masters = [m for m in masters if m[1] != weak[1]]
        for boss in other:
            if weak is None or boss[1] != weak[1]:
                prg[base + boss[0]] = rng.next(0, 2)
    for off, _weapon in usable:
        base = off - _PRG_HDR
        for boss in MM5_WILY_BOSSES:
            roll = rng.next(0, 3)
            prg[base + boss[0]] = 1 if roll == 1 else (4 if roll == 2 else 0)
    return spoiler


def randomize_mm5(prg: bytearray, seed, *, weakness: bool = True,
                  weapons: bool = True, palette: bool = True,
                  visualizer: bool = False) -> Spoiler:
    """Randomize an MM5 PRG.

    ``weakness`` is byte-for-byte parity with dmarchand/mm5randomizer and
    ``weapons`` (weapon-get screen) is a byte-for-byte port of the same tool's
    ``WeaponGetRandomizer`` for the same integer seed (offsets remapped to the
    MMLC ROM). ``palette`` is a 20xx shuffle of the MM5 palette table (the
    upstream tool has no palette module).
    """
    if len(prg) != MM5_PRG_SIZE:
        raise RandomizerError(
            f"MM5 PRG must be {MM5_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    sp = Spoiler("mm5", seed)
    if weapons:
        rewards = _mm5_shuffle_reward(prg, _DotNetRandom(_seed_int(seed)))
        sp.rewards = rewards
        sp.order = list(rewards)
    if weakness:
        sp.weaknesses = _mm5_shuffle_weakness(prg, _DotNetRandom(_seed_int(seed)))
        if not sp.order:
            sp.order = list(sp.weaknesses)
    if palette:
        _mm5_shuffle_palette(prg, _rng(f"{seed}:mm5:palette"))
    return sp


# MM5 weapon-get reward (dmarchand/mm5randomizer), offsets remapped to MMLC:
# the reference's map/text block sits 3 bytes earlier in the MMLC ROM.
MM5_REWARD_ADDR = 0x3AF19          # PRG (ref file 0x3AF29)
MM5_MENU_ADDR = 0x2489             # PRG (ref file 0x02499)
MM5_MAP_BASE = 0x2EEFF             # PRG (ref file 0x2EF0C)
MM5_OFFSET_BASE = 0x2EF07          # PRG (ref file 0x2EF14)
MM5_WEAPONS = [
    ("Water Wave", 0x01), ("Gyro Attack", 0x02), ("Crystal Eye", 0x03),
    ("Napalm Bomb", 0x04), ("Super Arrow", 0x05), ("Power Stone", 0x06),
    ("Gravity Hold", 0x07), ("Charge Kick", 0x08), ("Star Crash", 0x09),
    ("Rush Jet", 0x0B), ("Beat", 0x0C),
]
MM5_INMEM = {
    "Water Wave": 0xB1, "Gyro Attack": 0xB2, "Crystal Eye": 0xB3,
    "Napalm Bomb": 0xB4, "Super Arrow": 0xB5, "Power Stone": 0xB6,
    "Gravity Hold": 0xB7, "Charge Kick": 0xB8, "Star Crash": 0xB9,
    "Rush Coil": 0xBA, "Rush Jet": 0xBB, "Beat": 0xBC,
}
MM5_LEVELS = [
    ("Gravity Man", 0), ("Wave Man", 1), ("Stone Man", 2), ("Gyro Man", 3),
    ("Star Man", 4), ("Charge Man", 5), ("Napalm Man", 6), ("Crystal Man", 7),
]
_MM5_TEXT = {c: 0x41 + (ord(c) - ord("A"))
             for c in "ABCDEFGHIJKLMNOPQRSTUVWXY"}
_MM5_TEXT["Z"] = 0x60
_MM5_TEXT.update({" ": 0x20, ".": 0x2E, "+": 0x5C, ";": 0x2B, ":": 0x2E,
                  "[": 0x4B})
# MM5 palette table (4-byte entries, first two bytes = light/dark colour).
MM5_PALETTE_ENTRIES = [0x2538 + i * 4 for i in range(21)]


def _mm5_shuffle_reward(prg: bytearray, rng) -> Dict[str, List[str]]:
    remaining = list(MM5_WEAPONS)
    letter = remaining[rng.next(len(remaining))]
    remaining.remove(letter)

    stages = list(MM5_LEVELS)
    keyed = [(rng.next(), i, w) for i, w in enumerate(remaining)]
    keyed.sort(key=lambda t: t[0])
    eight = [w for _, _, w in keyed][:8]

    rewards: "Dict[int, List[tuple]]" = {}
    for weapon in eight:
        stage = stages[rng.next(len(stages))]
        rewards.setdefault(stage[1], []).append(weapon)
        stages.remove(stage)
    # bonus weapons use a fresh stage list, as upstream does
    stages = list(MM5_LEVELS)
    for weapon in [w for w in remaining if w not in eight]:
        stage = stages[rng.next(len(stages))]
        rewards.setdefault(stage[1], []).append(weapon)
        stages.remove(stage)

    prg[MM5_REWARD_ADDR] = MM5_INMEM[letter[0]]
    prg[MM5_MENU_ADDR] = MM5_INMEM[letter[0]]

    total = 0
    for stage_idx in rewards:
        prg[MM5_MAP_BASE + stage_idx] = total & 0xFF
        rw = rewards[stage_idx]
        if len(rw) == 1:
            text = "YOU GOT " + rw[0][0].upper() + "."
        else:
            text = ("YOU GOT " + rw[0][0].upper() + "+;:AND+;[ "
                    + rw[1][0].upper() + ".")
        for ch in text:
            prg[MM5_OFFSET_BASE + total] = _MM5_TEXT[ch]
            total += 1
        prg[MM5_OFFSET_BASE + total] = rw[0][1]
        total += 1
        prg[MM5_OFFSET_BASE + total] = rw[1][1] if len(rw) == 2 else 0x00
        total += 1
    return {MM5_LEVELS[si][0]: [w[0] for w in rw]
            for si, rw in rewards.items()}


def _mm5_shuffle_palette(prg: bytearray, rng: "_Rng") -> None:
    entries = [e for e in MM5_PALETTE_ENTRIES if prg[e + 2] == 0x0F]
    pairs = [bytes(prg[e:e + 2]) for e in entries]
    perm = list(range(len(pairs)))
    for i in range(len(perm) - 1, 0, -1):
        j = rng.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    for e, src in zip(entries, perm):
        prg[e:e + 2] = pairs[src]


# --- MM3: boss weakness tables (XenoStar54/Mega-Man-3-Randomizer) -----------
MM3_PRG_SIZE = 0x40000
MM3_WEAPON_NAMES = [
    "Needle Cannon", "Magnet Missile", "Gemini Laser", "Hard Knuckle",
    "Top Spin", "Search Snake", "Spark Shock", "Shadow Blade",
]
MM3_BOSS_NAMES = [
    "Needle Man", "Magnet Man", "Top Man", "Shadow Man",
    "Hard Man", "Spark Man", "Snake Man", "Gemini Man",
]


def _mm3_assign_weaknesses(rng: random.Random, mode: int, *args):
    if mode == 1:
        eff = [[1] * 8 for _ in range(8)]
        w1 = list(range(8))
        rng.shuffle(w1)
        w2 = list(range(8))
        rng.shuffle(w2)
        while True:
            rng.shuffle(w2)
            if all(w1[i] != w2[i] for i in range(8)):
                break
        for i in range(8):
            for j in range(i):
                eff[i][j] = rng.choice([0x00, 0x00, 0x01, 0x01, 0x01, 0x02])
            eff[i][w1[i]] = 0x04
            eff[i][w2[i]] = rng.choice(args[0])
        return eff
    eff = [
        [0x05, 0x05, 0x02, 0x01, 0x01, 0x01, 0x01, 0x00],
        [rng.randint(0x04, 0x07), 0x03, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00],
        [rng.randint(0x04, 0x07), rng.randint(0x04, 0x07), 0x03, 0x02, 0x01, 0x01, 0x00, 0x00],
        [rng.randint(0x04, 0x07), rng.randint(0x04, 0x07), 0x04, 0x02, 0x01, 0x00, 0x00, 0x00],
        [rng.randint(0x04, 0x07), 0x04, 0x02, 0x02, 0x01, 0x01, 0x00, 0x00],
        [rng.randint(0x02, 0x04), 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        [0x02, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    ]
    for row in eff:
        rng.shuffle(row)
    return eff


def _mm3_shuffle_weakness(prg: bytearray, rng: random.Random) -> Dict[str, str]:
    rm = _mm3_assign_weaknesses(
        rng, 1, [0x04, 0x04, 0x04, 0x05, 0x07, 0x07, 0x07, 0x07])
    doc = _mm3_assign_weaknesses(
        rng, 1, [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x07, 0x07])
    fw = _mm3_assign_weaknesses(rng, 2)
    bst = [1, 1, 1, 1, 1, 1, 2, 2]
    rng.shuffle(bst)
    dbst = [1, 1, 1, 1, 1, 1, 2, 2]
    rng.shuffle(dbst)

    def put(file_off: int, value: int) -> None:
        prg[file_off - _PRG_HDR] = value & 0xFF

    counter = 0
    for seg in range(0x14100, 0x14A00, 0x100):
        if seg == 0x14100:
            for k in range(4):
                put(seg + 0xB0 + k, dbst[k])
                put(seg + 0xC0 + k, dbst[4 + k])
            for o, v in [(0xD0, bst[0]), (0xD7, bst[0]), (0xD1, bst[1]),
                         (0xD2, bst[2]), (0xD3, bst[3]), (0xD8, bst[3]),
                         (0xE0, bst[4]), (0xE2, bst[5]), (0xE4, bst[6]),
                         (0xE6, bst[7]), (0xE7, bst[7])]:
                put(seg + o, v)
        else:
            for k in range(4):
                put(seg + 0xB0 + k, doc[counter][k])
                put(seg + 0xC0 + k, doc[counter][4 + k])
            for o, v in [(0xD0, rm[counter][0]), (0xD7, rm[counter][0]),
                         (0xD1, rm[counter][1]), (0xD2, rm[counter][2]),
                         (0xD3, rm[counter][3]), (0xD8, rm[counter][3]),
                         (0xE0, rm[counter][4]), (0xE2, rm[counter][5]),
                         (0xE4, rm[counter][6]), (0xCA, rm[counter][6]),
                         (0xE6, rm[counter][7]), (0xE7, rm[counter][7])]:
                put(seg + o, v)
            for o, v in [(0x101, fw[0][counter]), (0xF0, fw[1][counter]),
                         (0x105, fw[2][counter]), (0xF5, fw[3][counter]),
                         (0xF3, fw[4][counter]), (0xF8, fw[5][counter]),
                         (0xF9, fw[6][counter])]:
                put(seg + o, v)
            counter += 1

    # weapon i deals 4 (its primary weakness) to robot master w1[i]
    spoiler: Dict[str, str] = {}
    for wi, weapon in enumerate(MM3_WEAPON_NAMES):
        for bi, boss in enumerate(MM3_BOSS_NAMES):
            if rm[wi][bi] == 0x04:
                spoiler[boss] = weapon
    return spoiler


# MM3 weapon-location (reward) + palette tables (XenoStar54 offsets are file
# offsets; PRG = offset - 0x10). The upstream shuffles its module-level
# WEAPON_POSITIONS before the seed is set, so these are 20xx deterministic
# shuffles of the same tables rather than upstream parity.
MM3_STAGE_ORDER = ["Needle Man", "Magnet Man", "Gemini Man", "Hard Man",
                   "Top Man", "Snake Man", "Spark Man", "Shadow Man"]
MM3_WEAPONLOC_TABLE = 0x3DD04         # PRG: pos[8] then page[8]
MM3_GETPAL_BASE = 0x31BB8             # PRG: + stage*8, matches file 0x31BC8
MM3_WEAPONPAL = [0x4646, 0x464A, 0x464E, 0x4652, 0x4656, 0x465A, 0x4662,
                 0x466A]
MM3_WEAPONPAL_ORDER = [2, 0, 3, 1, 4, 5, 6, 7]
MM3_RUSH_PAL = 0x465E
MM3_MENU_RUSH = 0x4627
MM3_MENU_SEARCH = 0x4633


def _mm3_weapons(prg: bytearray, rng: "_Rng", do_reward: bool,
                 do_palette: bool) -> Dict[str, str]:
    entries = [(prg[MM3_WEAPONLOC_TABLE + s],
                prg[MM3_WEAPONLOC_TABLE + 8 + s]) for s in range(8)]
    colors = [(_rand_color(rng), _rand_color(rng, dark=True)) for _ in range(8)]
    perm = list(range(8))
    for i in range(7, 0, -1):
        j = rng.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    if do_reward:
        for s in range(8):
            src = perm[s]
            prg[MM3_WEAPONLOC_TABLE + s] = entries[src][0]
            prg[MM3_WEAPONLOC_TABLE + 8 + s] = entries[src][1]
            c1, c2 = colors[src]
            base = MM3_GETPAL_BASE + s * 8
            prg[base + 0] = 0x30
            prg[base + 1] = (c1 + 0x10) & 0xFF
            prg[base + 2] = c2
            prg[base + 4] = (c2 - 0x10) & 0xFF
            prg[base + 5] = c1
            prg[base + 6] = c2
    if do_palette:
        # in-game weapon palettes (slot order -> weapon index)
        for slot, wi in enumerate(MM3_WEAPONPAL_ORDER):
            c1, c2 = colors[wi]
            prg[MM3_WEAPONPAL[slot]] = c1
            prg[MM3_WEAPONPAL[slot] + 1] = c2
        rush1, rush2 = _rand_color(rng), _rand_color(rng, dark=True)
        prg[MM3_RUSH_PAL] = rush1
        prg[MM3_RUSH_PAL + 1] = rush2
        prg[MM3_MENU_RUSH] = rush2
        prg[MM3_MENU_SEARCH] = colors[5][1]
    return {MM3_STAGE_ORDER[s]: MM3_WEAPON_NAMES[perm[s]] for s in range(8)}


def _rand_color(rng: "_Rng", dark: bool = False) -> int:
    if dark:
        return 0x10 + rng.below(0x0E)      # 0x10..0x1D
    return 0x20 + rng.below(0x0D)          # 0x20..0x2C


def randomize_mm3(prg: bytearray, seed, *, weakness: bool = True,
                  weapons: bool = True, palette: bool = True,
                  visualizer: bool = False) -> Spoiler:
    """Randomize an MM3 PRG.

    ``weakness`` is byte-for-byte parity with XenoStar54/Mega-Man-3-Randomizer
    for the same string seed. ``weapons`` (weapon locations) and ``palette``
    are deterministic 20xx shuffles of the same tables (the upstream shuffles
    those tables before its seed is applied, so they are not seed-reproducible
    upstream).
    """
    if len(prg) != MM3_PRG_SIZE:
        raise RandomizerError(
            f"MM3 PRG must be {MM3_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    sp = Spoiler("mm3", seed)
    if weakness:
        rng = random.Random()
        rng.seed(str(seed))
        sp.weaknesses = _mm3_shuffle_weakness(prg, rng)
        sp.order = list(MM3_BOSS_NAMES)
    if weapons or palette:
        wrng = _rng(f"{seed}:mm3:weapon")
        rewards = _mm3_weapons(prg, wrng, weapons, palette)
        if weapons:
            sp.rewards = rewards
            sp.order = sp.order or list(MM3_STAGE_ORDER)
    return sp


# --- MM2: robot-master weakness tables -------------------------------------
# Byte-for-byte port of squid-man/MegaMan2Randomizer2's ``RandomizeU`` (the
# robot-master weakness pass) for the same seed string. The reference tool
# runs on a ROM it first expands to >=512 KB (its Wily/enemy tables live above
# 0x40000), so only this pass -- which touches the vanilla 256 KB PRG -- is
# portable to MMLC. The reference seeds each module with
# ``PcgSeed(rootIdentifier + "RWeaknesses")``.
MM2_PRG_SIZE = 0x40000
_MM2_WEAPON_TABLES = [              # (weapon, headered file offset)
    ("Heat", 0x2E960), ("Air", 0x2E96E), ("Wood", 0x2E97C),
    ("Bubble", 0x2E98A), ("Quick", 0x2E998), ("Flash", 0x2C049),
    ("Metal", 0x2E9B4), ("Crash", 0x2E9A6),
]
_MM2_BUSTER = 0x2E952
_MM2_BOSSES = ["Heat", "Air", "Wood", "Bubble", "Quick", "Flash", "Metal",
               "Crash"]
_MM2_BOSS_NAMES = ["Heat Man", "Air Man", "Wood Man", "Bubble Man",
                   "Quick Man", "Flash Man", "Metal Man", "Crash Man"]
_MM2_WEAPON_NAMES = {"Heat": "Atomic Fire", "Air": "Air Shooter",
                     "Wood": "Leaf Shield", "Bubble": "Bubble Lead",
                     "Quick": "Quick Boomerang", "Flash": "Time Stopper",
                     "Metal": "Metal Blade", "Crash": "Crash Bomber"}
_MM2_AMMO = {"Buster": 0.0, "Heat": 10.0, "Air": 2.0, "Wood": 3.0,
             "Bubble": 0.5, "Quick": 0.125, "Metal": 0.25, "Crash": 4.0}

_CRC64_TABLE: Optional[List[int]] = None


def _crc64_ecma(data: bytes) -> int:
    """CRC-64/ECMA-182 (``System.IO.Hashing.Crc64`` default parameter set)."""
    global _CRC64_TABLE
    if _CRC64_TABLE is None:
        poly = 0x42F0E1EBA9EA3693
        table = []
        for i in range(256):
            c = i << 56
            for _ in range(8):
                c = ((c << 1) ^ poly) if (c & 0x8000000000000000) else (c << 1)
                c &= 0xFFFFFFFFFFFFFFFF
            table.append(c)
        _CRC64_TABLE = table
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFFFFFFFFFFFFFF) ^ _CRC64_TABLE[((c >> 56) ^ b) & 0xFF]
    return c


def _alpha_base26(value: int) -> str:
    digits = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value == 0:
        return digits[0]
    out = ""
    while value > 0:
        out = digits[value % 26] + out
        value //= 26
    return out


class _PcgSeed:
    """PCG-based seed used by squid-man/MegaMan2Randomizer2."""

    MUL = 6364136223846793005
    INC = 1442695040888963407
    MASK = (1 << 64) - 1

    def __init__(self, seed_string: str):
        self._seed = _crc64_ecma(seed_string.encode("utf-16-le"))
        self.identifier = _alpha_base26(self._seed)
        self._state = 0
        self.reset()

    def reset(self) -> None:
        self._state = (self._seed + self.INC) & self.MASK
        self._next()

    def _next(self) -> int:
        x = self._state
        shift = (x >> 59) & 31
        self._state = (x * self.MUL + self.INC) & self.MASK
        x ^= x >> 18
        v = (x >> 27) & 0xFFFFFFFF
        return ((v >> shift) | (v << ((32 - shift) & 31))) & 0xFFFFFFFF

    def next_u64(self) -> int:
        return ((self._next() << 32) + self._next()) & self.MASK

    def next_double(self) -> float:
        return (self.next_u64() >> 11) / float(1 << 53)

    def next_int32(self, max_value: int) -> int:
        return self.next_u64() % max_value if max_value else 0

    def shuffle(self, seq):
        keyed = [(self._next(), i, v) for i, v in enumerate(seq)]
        keyed.sort(key=lambda t: t[0])
        return [v for _, _, v in keyed]


def _mm2_robo_damage_primary(rng: "_PcgSeed", weapon: str) -> int:
    damage = 0
    if rng.next_double() > 0.75:
        damage = 2
    if weapon == "Heat":
        damage += int(_MM2_AMMO["Heat"] + 1)
    elif weapon == "Air":
        damage += int(_MM2_AMMO["Air"] + 1)
    elif weapon == "Wood":
        damage += int(_MM2_AMMO["Wood"] + 1)
    elif weapon == "Flash":
        return 1
    elif weapon == "Crash":
        damage += int(_MM2_AMMO["Crash"] + 1)
    if rng.next_double() > 0.5:
        if damage < 4:
            damage = 4
    elif damage < 3:
        damage = 3
    return damage & 0xFF


def _mm2_shuffle_weakness(prg: bytearray, rng: "_PcgSeed") -> Dict[str, str]:
    def put(file_off: int, value: int) -> None:
        prg[file_off - _PRG_HDR] = value & 0xFF

    values = rng.shuffle([w for w, _ in _MM2_WEAPON_TABLES])
    boss_weapon = dict(zip(_MM2_BOSSES, values))

    put(0x02E66D, 0xFF)                          # disable Atomic Fire healing

    buster_list = rng.shuffle(list(_MM2_BOSSES))[:4]
    very_weak = rng.shuffle(list(_MM2_BOSSES))[:2]
    great, ultimate = very_weak[0], very_weak[1]
    great_weapons = rng.shuffle(
        ["Heat", "Air", "Wood", "Bubble", "Quick", "Metal", "Crash"])[:2]

    addr = dict(_MM2_WEAPON_TABLES)
    addr["Buster"] = _MM2_BUSTER
    for boss in _MM2_BOSSES:
        boff = _MM2_BOSSES.index(boss)
        for weapon, _ in _MM2_WEAPON_TABLES:
            damage = 0
            if rng.next_double() > 0.5:
                if weapon == "Heat":
                    damage = int(_MM2_AMMO["Heat"] / 2)
                elif weapon == "Flash":
                    damage = 0
                else:
                    damage = 1
            put(addr[weapon] + boff, damage)
        put(addr[boss_weapon[boss]] + boff,
            _mm2_robo_damage_primary(rng, boss_weapon[boss]))
        next_boss = _MM2_BOSSES[(boff + 1) % len(_MM2_BOSSES)]
        weak2 = boss_weapon[next_boss]
        secondary = 2
        if weak2 == "Heat":
            secondary = 4
        elif weak2 == "Flash":
            secondary = 0
            put(0x02C08F, boff)
        put(addr[weak2] + boff, secondary)
        put(addr["Buster"] + boff, 2 if boss in buster_list else 1)
        if boss == great:
            put(addr[great_weapons[0]] + boff, 0x07)
        elif boss == ultimate:
            put(addr[great_weapons[1]] + boff, 0x0A)

    return {name: _MM2_WEAPON_NAMES[boss_weapon[key]]
            for name, key in zip(_MM2_BOSS_NAMES, _MM2_BOSSES)}


def randomize_mm2(prg: bytearray, seed, *, weakness: bool = True,
                  weapons: bool = True, palette: bool = True,
                  visualizer: bool = False) -> Spoiler:
    """Randomize an MM2 PRG.

    ``weakness`` is byte-for-byte parity with squid-man/MegaMan2Randomizer2's
    robot-master weakness pass for the same seed string. That tool's
    Wily/enemy tables need its >512 KB expanded ROM, so only the vanilla-PRG
    pass is ported here; ``weapons``/``palette`` are accepted but not ported.
    """
    if len(prg) != MM2_PRG_SIZE:
        raise RandomizerError(
            f"MM2 PRG must be {MM2_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    sp = Spoiler("mm2", seed)
    if weakness:
        root = _PcgSeed(str(seed))
        rng = _PcgSeed(root.identifier + "RWeaknesses")
        sp.weaknesses = _mm2_shuffle_weakness(prg, rng)
        sp.order = list(_MM2_BOSS_NAMES)
    if weapons or palette:
        wrng = _rng(f"{seed}:mm2:weapon")
        rewards = _mm2_weapons(prg, wrng, weapons, palette)
        if weapons:
            sp.rewards = rewards
            sp.order = sp.order or list(_MM2_BOSS_NAMES)
    return sp


# MM2 reward (weapon-award bit table) + weapon palettes. The upstream
# RWeaponGet/RColors target a relocated/expanded ROM, so these are 20xx
# shuffles of the vanilla tables (PRG offsets).
MM2_REWARD_TABLE = 0x3C279            # weapon bit awarded per stage (H A W B Q F M C)
MM2_REWARD_BITS = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
MM2_WEAPON_BY_INDEX = ["Atomic Fire", "Air Shooter", "Leaf Shield",
                       "Bubble Lead", "Quick Boomerang", "Time Stopper",
                       "Metal Blade", "Crash Bomber"]
MM2_PALETTE_SLOTS = [0x3D304 + i * 4 for i in range(9)]


def _mm2_weapons(prg: bytearray, rng: "_Rng", do_reward: bool,
                 do_palette: bool) -> Dict[str, str]:
    perm = list(range(8))
    for i in range(7, 0, -1):
        j = rng.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    if do_reward:
        for s in range(8):
            prg[MM2_REWARD_TABLE + s] = MM2_REWARD_BITS[perm[s]]
    if do_palette:
        pairs = [bytes(prg[slot:slot + 2]) for slot in MM2_PALETTE_SLOTS]
        sp = list(range(len(pairs)))
        for i in range(len(sp) - 1, 0, -1):
            j = rng.below(i + 1)
            sp[i], sp[j] = sp[j], sp[i]
        for slot, src in zip(MM2_PALETTE_SLOTS, sp):
            prg[slot:slot + 2] = pairs[src]
    return {_MM2_BOSS_NAMES[s]: MM2_WEAPON_BY_INDEX[perm[s]] for s in range(8)}


# --- MM4: robot-master weakness + reward + palette (own design) -------------
# No open reference randomizer exists for MM4. The vanilla boss damage table is
# weapon-major: 14 weapon banks at file 0x40000 + w*0x2000, indexed by each
# boss's enemy id (PRG offset = file - 0x10). Data Crystal (Infidelity /
# Insectduel). Weaknesses are shuffled by permuting the 8 robot-master columns.
MM4_PRG_SIZE = 0x80000
MM4_WEAPON_NAMES = [
    "Mega Buster", "Rush Coil", "Rush Jet", "Rush Marine", "Rain Flush",
    "Wire", "Balloon", "Dive Missile", "Ring Boomerang", "Drill Bomb",
    "Dust Crusher", "Pharaoh Shot", "Flash Stopper", "Skull Barrier",
]
MM4_BOSS_OFFSETS = {
    "Bright Man": 0x179B, "Toad Man": 0x179D, "Drill Man": 0x178E,
    "Pharaoh Man": 0x1794, "Ring Man": 0x1785, "Dust Man": 0x1789,
    "Dive Man": 0x178C, "Skull Man": 0x1781,
}
MM4_BOSSES = list(MM4_BOSS_OFFSETS)
MM4_WEAPON_BASE = 0x40000              # file offset of weapon bank 0
MM4_REWARD_TABLE = 0x73A1E             # PRG: stage -> weapon id (8 robot stages)
MM4_REWARD_WEAPON = {0x04: "Rain Flush", 0x07: "Dive Missile",
                     0x08: "Ring Boomerang", 0x09: "Drill Bomb",
                     0x0A: "Dust Crusher", 0x0B: "Pharaoh Shot",
                     0x0C: "Flash Stopper", 0x0D: "Skull Barrier"}


def _mm4_shuffle_weakness(prg: bytearray, rng: "_Rng") -> Dict[str, str]:
    perm = list(range(8))
    for i in range(7, 0, -1):
        j = rng.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    offsets = [MM4_BOSS_OFFSETS[b] for b in MM4_BOSSES]
    for w in range(14):
        base = MM4_WEAPON_BASE + w * 0x2000
        vals = [prg[base + o - _PRG_HDR] for o in offsets]
        for dst, src in enumerate(perm):
            prg[base + offsets[dst] - _PRG_HDR] = vals[src]
    spoiler = {}
    for dst, boss in enumerate(MM4_BOSSES):
        col = [prg[MM4_WEAPON_BASE + w * 0x2000 + offsets[dst] - _PRG_HDR]
               for w in range(14)]
        spoiler[boss] = MM4_WEAPON_NAMES[max(range(14), key=lambda w: col[w])]
    return spoiler


def _mm4_weapons(prg: bytearray, rng: "_Rng", do_reward: bool,
                 do_palette: bool) -> Dict[str, str]:
    if do_reward:
        vals = [prg[MM4_REWARD_TABLE + s] for s in range(8)]
        perm = list(range(8))
        for i in range(7, 0, -1):
            j = rng.below(i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        for s in range(8):
            prg[MM4_REWARD_TABLE + s] = vals[perm[s]]
    if do_palette:
        light = 0x20 + rng.below(0x1D)          # 0x20..0x3C
        dark = (light - 0x10) & 0xFF            # 0x10..0x2C
        for i in range(13):
            prg[0x7928D - _PRG_HDR + i] = light  # charging secondary
            prg[0x7929D - _PRG_HDR + i] = dark   # charging main
        for pair in (0x792CA, 0x79B14, 0x7C864):
            prg[pair - _PRG_HDR + 1] = light
            prg[pair - _PRG_HDR + 2] = dark
    if not do_reward:
        return {}
    return {MM4_BOSSES[s]: MM4_REWARD_WEAPON.get(prg[MM4_REWARD_TABLE + s],
                                                 "?") for s in range(8)}


def randomize_mm4(prg: bytearray, seed, *, weakness: bool = True,
                  weapons: bool = True, palette: bool = True,
                  visualizer: bool = False) -> Spoiler:
    """Randomize an MM4 PRG (deterministic 20xx shuffles; no upstream tool)."""
    if len(prg) != MM4_PRG_SIZE:
        raise RandomizerError(
            f"MM4 PRG must be {MM4_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    sp = Spoiler("mm4", seed)
    if weakness:
        sp.weaknesses = _mm4_shuffle_weakness(prg, _rng(f"{seed}:mm4:weakness"))
        sp.order = list(MM4_BOSSES)
    if weapons or palette:
        rewards = _mm4_weapons(prg, _rng(f"{seed}:mm4:weapon"), weapons, palette)
        if weapons:
            sp.rewards = rewards
            sp.order = sp.order or list(MM4_BOSSES)
    return sp


# --- registry -------------------------------------------------------------- #

GAMES = {
    "mm1": {"prg_size": MM1_PRG_SIZE, "fn": randomize_mm1},
    "mm2": {"prg_size": MM2_PRG_SIZE, "fn": randomize_mm2},
    "mm3": {"prg_size": MM3_PRG_SIZE, "fn": randomize_mm3},
    "mm4": {"prg_size": MM4_PRG_SIZE, "fn": randomize_mm4},
    "mm5": {"prg_size": MM5_PRG_SIZE, "fn": randomize_mm5},
}


def resolve(key: str) -> str:
    k = key.strip().lower()
    if k not in GAMES:
        raise RandomizerError(f"no randomizer for {key!r}; known: {', '.join(GAMES)}")
    return k


def randomize(key: str, prg: bytearray, seed: str, **kw) -> Spoiler:
    return GAMES[resolve(key)]["fn"](prg, seed, **kw)


# database.xml game id per randomizer key
GAME_DB_ID = {"mm1": "1", "mm2": "2", "mm3": "3", "mm4": "4", "mm5": "5",
              "mm6": "6"}


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
