"""Tests for the seed-driven ROM randomizer (synthetic MM1 PRG)."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import randomizer as R

# vanilla MM1 damage charts (P C I B F E G M), one per boss
VANILLA = [
    [3, 1, 0, 2, 3, 1, 14, 0],
    [1, 2, 0, 4, 1, 10, 0, 0],
    [2, 2, 0, 1, 4, 2, 0, 0],
    [2, 2, 4, 1, 1, 1, 0, 0],
    [1, 10, 0, 2, 1, 1, 4, 0],
    [2, 1, 0, 10, 2, 1, 1, 0],
]


def make_prg() -> bytearray:
    p = bytearray(R.MM1_PRG_SIZE)
    for b, chart in enumerate(VANILLA):
        p[R.MM1_DAMAGE_TABLE + b * 8 : R.MM1_DAMAGE_TABLE + b * 8 + 8] = bytes(chart)
    p[R.MM1_WEAPON_REWARD : R.MM1_WEAPON_REWARD + 6] = bytes([0x20, 0x10, 0x02, 0x40, 0x04, 0x08])
    p[R.MM1_WEAKNESS_TABLE : R.MM1_WEAKNESS_TABLE + 6] = bytes([0x20, 0x10, 0x02, 0x40, 0x04, 0x08])
    return p


class RandomizerTests(unittest.TestCase):
    def test_deterministic(self):
        pa = make_prg(); R.randomize_mm1(pa, "seed-a")
        pb = make_prg(); R.randomize_mm1(pb, "seed-a")
        pc = make_prg(); R.randomize_mm1(pc, "seed-b")
        self.assertEqual(bytes(pa), bytes(pb))
        self.assertNotEqual(bytes(pa), bytes(pc))
        self.assertNotEqual(bytes(pa), bytes(make_prg()))

    def test_weakness_charts_are_permutation(self):
        p = make_prg()
        R.randomize_mm1(p, "x", weapons=False)
        before = sorted(map(tuple, VANILLA))
        after = sorted(map(tuple, R._read_charts(p)))
        self.assertEqual(before, after)

    def test_rewards_are_permutation(self):
        p = make_prg()
        R.randomize_mm1(p, "x", weakness=False)
        table = list(p[R.MM1_WEAPON_REWARD : R.MM1_WEAPON_REWARD + 6])
        self.assertEqual(sorted(table), [0x02, 0x04, 0x08, 0x10, 0x20, 0x40])

    def test_superarm_lands_on_throwable(self):
        # run many seeds; whenever Super Arm (index 6, 14 dmg) is a major
        # weakness, it must be on Cut/Elec/Guts (bosses 0/4/5).
        for i in range(200):
            p = make_prg()
            s = R.randomize_mm1(p, f"seed-{i}", weapons=False)
            for b, chart in enumerate(R._read_charts(p)):
                if chart[6] == 14:
                    self.assertIn(b, R.MM1_THROWABLE)

    def test_spoiler_matches_charts(self):
        p = make_prg()
        s = R.randomize_mm1(p, "hello")
        charts = R._read_charts(p)
        for i in range(6):
            self.assertEqual(
                s.weaknesses[R.MM1_BOSS_ENTRIES[i]],
                R.WEAPON_SPRITE[charts[i].index(max(charts[i]))],
            )

    def test_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.randomize_mm1(bytearray(16), "x")

    def test_no_ops_preserve(self):
        p = make_prg()
        R.randomize_mm1(p, "x", weakness=False, weapons=False, palette=False)
        self.assertEqual(bytes(p), bytes(make_prg()))

    def test_palette_seeded_and_per_seed(self):
        a = make_prg(); R.randomize_mm1(a, "seed-a", palette=True)
        b = make_prg(); R.randomize_mm1(b, "seed-a", palette=True)
        c = make_prg(); R.randomize_mm1(c, "seed-b", palette=True)
        self.assertEqual(a[R.MM1_PAL_BOSSROOM[0]], b[R.MM1_PAL_BOSSROOM[0]])
        self.assertEqual(a[R.MM1_PAL_BOSSROOM[1]], b[R.MM1_PAL_BOSSROOM[1]])
        for off in R.MM1_PAL_OFFSETS:
            self.assertEqual(a[off], b[off])
            self.assertEqual(a[off + 1], b[off + 1])
        # at least one byte should differ across seeds
        self.assertNotEqual(bytes(a[R.MM1_WEAPON_PAL:R.MM1_WEAPON_PAL + 14]),
                            bytes(c[R.MM1_WEAPON_PAL:R.MM1_WEAPON_PAL + 14]))

    def test_no_palette_preserves_vanilla(self):
        p = make_prg()
        for off in R.MM1_PAL_OFFSETS:
            p[off] = 0x2C
            p[off + 1] = 0x11
        p[R.MM1_PAL_BOSSROOM[0]] = 0x2C
        p[R.MM1_PAL_BOSSROOM[1]] = 0x11
        R.randomize_mm1(p, "x", palette=False)
        for off in R.MM1_PAL_OFFSETS:
            self.assertEqual((p[off], p[off + 1]), (0x2C, 0x11))
        self.assertEqual((p[R.MM1_PAL_BOSSROOM[0]], p[R.MM1_PAL_BOSSROOM[1]]),
                         (0x2C, 0x11))


class MM2Tests(unittest.TestCase):
    # byte anchors from squid-man/MegaMan2Randomizer2 (RandomizeU), seed "hello"
    # (PRG offsets = upstream file offset - 0x10)
    ANCHORS = [(0x2E65D, 255), (0x2E942, 1), (0x2E943, 2), (0x2E950, 0),
               (0x2E95E, 0), (0x2E996, 0), (0x2C039, 0), (0x2C07F, 2)]

    def test_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.randomize_mm2(bytearray(16), "x")

    def test_deterministic(self):
        import hashlib
        def run(seed):
            p = bytearray(R.MM2_PRG_SIZE)
            R.randomize_mm2(p, seed)
            return hashlib.md5(bytes(p)).hexdigest()
        self.assertEqual(run("seed-a"), run("seed-a"))
        self.assertNotEqual(run("seed-a"), run("seed-b"))

    def test_upstream_parity_anchors(self):
        p = bytearray(R.MM2_PRG_SIZE)
        R.randomize_mm2(p, "hello")
        for off, val in self.ANCHORS:
            self.assertEqual(p[off], val, f"MM2 byte {off:#x}")

    def test_upstream_hash_vector(self):
        # System.IO.Hashing.Crc64 (ECMA-182) check value
        self.assertEqual(R._crc64_ecma(b"123456789"), 0x6C40DF5F0B497347)
        self.assertEqual(R._alpha_base26(0), "A")

    def _base(self):
        p = bytearray(R.MM2_PRG_SIZE)
        p[R.MM2_REWARD_TABLE:R.MM2_REWARD_TABLE + 8] = bytes(R.MM2_REWARD_BITS)
        for i, slot in enumerate(R.MM2_PALETTE_SLOTS):
            p[slot] = 0x20 + i
            p[slot + 1] = 0x10 + i
        return p

    def test_reward_is_permutation(self):
        p = self._base()
        R.randomize_mm2(p, "hello", weakness=False)
        got = bytes(p[R.MM2_REWARD_TABLE:R.MM2_REWARD_TABLE + 8])
        self.assertEqual(sorted(got), sorted(R.MM2_REWARD_BITS))

    def test_palette_is_permutation(self):
        p = self._base()
        R.randomize_mm2(p, "hello", weakness=False)
        got = [tuple(p[s:s + 2]) for s in R.MM2_PALETTE_SLOTS]
        before = [(0x20 + i, 0x10 + i) for i in range(len(got))]
        self.assertEqual(sorted(got), sorted(before))

    def test_reward_palette_deterministic(self):
        a = self._base(); R.randomize_mm2(a, "s", weakness=False)
        b = self._base(); R.randomize_mm2(b, "s", weakness=False)
        self.assertEqual(bytes(a), bytes(b))


class MM3Tests(unittest.TestCase):
    # byte anchors from XenoStar54/Mega-Man-3-Randomizer, seed "hello"
    # (PRG offsets = upstream file offset - 0x10)
    ANCHORS = [(0x141A0, 1), (0x141A1, 1), (0x141A2, 1), (0x141A3, 1),
               (0x141B0, 1), (0x141B1, 2), (0x141B2, 1), (0x141B3, 2)]

    def test_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.randomize_mm3(bytearray(16), "x")

    def test_deterministic(self):
        import hashlib
        def run(seed):
            p = bytearray(R.MM3_PRG_SIZE)
            R.randomize_mm3(p, seed)
            return hashlib.md5(bytes(p)).hexdigest()
        self.assertEqual(run("seed-a"), run("seed-a"))
        self.assertNotEqual(run("seed-a"), run("seed-b"))

    def test_upstream_parity_anchors(self):
        p = bytearray(R.MM3_PRG_SIZE)
        R.randomize_mm3(p, "hello")
        for off, val in self.ANCHORS:
            self.assertEqual(p[off], val, f"MM3 byte {off:#x}")

    def _base(self):
        p = bytearray(R.MM3_PRG_SIZE)
        p[0x3DD04:0x3DD0C] = bytes([2, 4, 1, 3, 5, 0, 2, 4])
        p[0x3DD0C:0x3DD14] = bytes([0, 0, 0, 0, 0, 6, 6, 6])
        return p

    def test_reward_is_permutation(self):
        p = self._base()
        R.randomize_mm3(p, "hello", weakness=False)
        self.assertEqual(sorted(p[0x3DD04:0x3DD0C]),
                         sorted([2, 4, 1, 3, 5, 0, 2, 4]))

    def test_reward_palette_deterministic(self):
        a = self._base(); R.randomize_mm3(a, "s", weakness=False)
        b = self._base(); R.randomize_mm3(b, "s", weakness=False)
        self.assertEqual(bytes(a), bytes(b))
        c = self._base(); R.randomize_mm3(c, "t", weakness=False)
        self.assertNotEqual(bytes(a), bytes(c))


class MM5Tests(unittest.TestCase):
    # byte anchors from dmarchand/mm5randomizer, int seed 12345
    ANCHORS = [(0x284F, 1), (0x2869, 4), (0x286B, 0), (0x2871, 4),
               (0x287C, 4), (0x2889, 0), (0x288D, 1), (0x2891, 0)]

    def test_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.randomize_mm5(bytearray(16), "x")

    def test_deterministic(self):
        import hashlib
        def run(seed):
            p = bytearray(R.MM5_PRG_SIZE)
            R.randomize_mm5(p, seed)
            return hashlib.md5(bytes(p)).hexdigest()
        self.assertEqual(run(12345), run(12345))
        self.assertNotEqual(run(12345), run(54321))

    def test_upstream_parity_anchors(self):
        p = bytearray(R.MM5_PRG_SIZE)
        R.randomize_mm5(p, 12345)
        for off, val in self.ANCHORS:
            self.assertEqual(p[off], val, f"MM5 byte {off:#x}")

    def test_dotnet_random_reference_vector(self):
        # legacy .NET System.Random(0).Next() sequence
        r = R._DotNetRandom(0)
        self.assertEqual([r.next() for _ in range(4)],
                         [1559595546, 1755192844, 1649316166, 1198642031])

    # weapon-get reward anchors, dmarchand/mm5randomizer, int seed 12345
    # (PRG offsets; map/text remapped from the reference ROM layout)
    REWARD_ANCHORS = [(0x2489, 177), (0x3AF19, 177), (0x2EEFF, 45),
                      (0x2EF00, 22), (0x2EF07, 89), (0x2EF08, 79),
                      (0x2EF09, 85)]

    def test_reward_upstream_anchors(self):
        p = bytearray(R.MM5_PRG_SIZE)
        R.randomize_mm5(p, 12345, weakness=False, palette=False)
        for off, val in self.REWARD_ANCHORS:
            self.assertEqual(p[off], val, f"MM5 reward byte {off:#x}")

    def test_palette_shuffle_deterministic(self):
        def base():
            p = bytearray(R.MM5_PRG_SIZE)
            for i, e in enumerate(R.MM5_PALETTE_ENTRIES):
                p[e] = 0x20 + (i % 8)
                p[e + 1] = 0x10 + (i % 6)
                p[e + 2] = 0x0F
                p[e + 3] = 0x0F
            return p
        a = base()
        R.randomize_mm5(a, 12345, weakness=False, weapons=False, palette=True)
        b = base()
        R.randomize_mm5(b, 12345, weakness=False, weapons=False, palette=True)
        self.assertEqual(bytes(a), bytes(b))
        # pairs are a permutation of the originals
        orig = sorted((0x20 + (i % 8), 0x10 + (i % 6))
                      for i in range(len(R.MM5_PALETTE_ENTRIES)))
        got = sorted(tuple(a[e:e + 2]) for e in R.MM5_PALETTE_ENTRIES)
        self.assertEqual(got, orig)


class MM4Tests(unittest.TestCase):
    def _base(self):
        p = bytearray(R.MM4_PRG_SIZE)
        # put a known per-weapon boss-damage pattern in the 14 weapon banks
        offsets = [R.MM4_BOSS_OFFSETS[b] for b in R.MM4_BOSSES]
        for w in range(14):
            base = R.MM4_WEAPON_BASE + w * 0x2000
            for i, o in enumerate(offsets):
                p[base + o - 0x10] = 1 if w != (i % 14) else 4
        for s in range(8):
            p[R.MM4_REWARD_TABLE + s] = [0x0C, 0x04, 0x09, 0x0B,
                                         0x08, 0x0A, 0x07, 0x0D][s]
        return p

    def test_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.randomize_mm4(bytearray(16), "x")

    def test_weakness_columns_preserved(self):
        p = self._base()
        before = bytes(p)
        R.randomize_mm4(p, "hello", weapons=False, palette=False)
        offsets = [R.MM4_BOSS_OFFSETS[b] for b in R.MM4_BOSSES]
        for w in range(14):
            base = R.MM4_WEAPON_BASE + w * 0x2000
            now = sorted(p[base + o - 0x10] for o in offsets)
            old = sorted(before[base + o - 0x10] for o in offsets)
            self.assertEqual(now, old)

    def test_reward_is_permutation(self):
        p = self._base()
        R.randomize_mm4(p, "hello", weakness=False, palette=False)
        got = sorted(p[R.MM4_REWARD_TABLE:R.MM4_REWARD_TABLE + 8])
        self.assertEqual(got, [0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D])

    def test_deterministic_and_palette(self):
        a = self._base(); R.randomize_mm4(a, "s")
        b = self._base(); R.randomize_mm4(b, "s")
        self.assertEqual(bytes(a), bytes(b))
        c = self._base(); R.randomize_mm4(c, "t")
        self.assertNotEqual(bytes(a), bytes(c))


class RegistryTests(unittest.TestCase):
    def test_registered_games(self):
        for k in ("mm1", "mm2", "mm3", "mm4", "mm5"):
            self.assertIn(k, R.GAMES)

    def test_dispatch(self):
        p = bytearray(R.MM5_PRG_SIZE)
        sp = R.randomize("mm5", p, 12345)
        self.assertEqual(sp.game, "mm5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
