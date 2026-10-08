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


if __name__ == "__main__":
    unittest.main(verbosity=2)
