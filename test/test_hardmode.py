"""Tests for MM1 Hard Mode (table scaling; no game assets needed)."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import hardmode as H

VANILLA_CHARTS = [
    [3, 1, 0, 2, 3, 1, 14, 0],
    [1, 2, 0, 4, 1, 10, 0, 0],
    [2, 2, 0, 1, 4, 2, 0, 0],
    [2, 2, 4, 1, 1, 1, 0, 0],
    [1, 10, 0, 2, 1, 1, 4, 0],
    [2, 1, 0, 10, 2, 1, 1, 0],
]


def make_prg() -> bytearray:
    p = bytearray(H.MM1_PRG_SIZE)
    for b, chart in enumerate(VANILLA_CHARTS):
        base = H.MM1_WEAPON_ON_BOSS + b * 8
        p[base:base + 8] = bytes(chart)
    return p


class ScaleTests(unittest.TestCase):
    def test_received(self):
        self.assertEqual(H.scale_received(0), 0)
        self.assertEqual(H.scale_received(1), 2)
        self.assertEqual(H.scale_received(4), 8)
        self.assertEqual(H.scale_received(10), 20)
        self.assertEqual(H.scale_received(200), 255)  # clamped to a byte

    def test_dealt(self):
        # /1.5, round-half-up, 0 stays 0, min 1 otherwise
        self.assertEqual([H.scale_dealt(v) for v in (0, 1, 2, 3, 4, 7, 10, 14, 20)],
                         [0, 1, 1, 2, 3, 5, 7, 9, 13])

    def test_size_guard(self):
        with self.assertRaises(H.HardModeError):
            H.apply_hard_mode(bytearray(16))


class ApplyTests(unittest.TestCase):
    def test_enemy_and_boss_received_doubled(self):
        p = make_prg()
        # seed the received tables with a known pattern
        for i in range(H.MM1_ENEMY_HIT_LEN):
            p[H.MM1_ENEMY_HIT_TABLE + i] = i % 5
        for i in range(H.MM1_BOSS_CONTACT_LEN):
            p[H.MM1_BOSS_CONTACT + i] = i % 6
        H.apply_hard_mode(p)
        self.assertEqual(list(p[H.MM1_ENEMY_HIT_TABLE:H.MM1_ENEMY_HIT_TABLE + 5]),
                         [0, 2, 4, 6, 8])
        self.assertEqual(list(p[H.MM1_BOSS_CONTACT:H.MM1_BOSS_CONTACT + 6]),
                         [0, 2, 4, 6, 8, 10])

    def test_boss_table_scaled(self):
        p = make_prg()
        H.apply_hard_mode(p)
        row0 = list(p[H.MM1_WEAPON_ON_BOSS:H.MM1_WEAPON_ON_BOSS + 8])
        self.assertEqual(row0, [2, 1, 0, 1, 2, 1, 9, 0])

    def test_argmax_preserved(self):
        p = make_prg()
        H.apply_hard_mode(p)
        for b, chart in enumerate(VANILLA_CHARTS):
            base = H.MM1_WEAPON_ON_BOSS + b * 8
            got = list(p[base:base + 8])
            self.assertEqual(got.index(max(got)), chart.index(max(chart)),
                             f"row {b} argmax changed")

    def test_enemy_weapon_tables_scaled(self):
        p = make_prg()
        for off in H.MM1_WEAPON_ENEMY:
            p[off] = 0x14   # 20 -> 13
            p[off + 1] = 0x04  # 4 -> 3
            p[off + 2] = 0x00  # 0 -> 0
        H.apply_hard_mode(p)
        for off in H.MM1_WEAPON_ENEMY:
            self.assertEqual((p[off], p[off + 1], p[off + 2]), (13, 3, 0))

    def test_ice_table_untouched(self):
        # WeaponDamageI (0x1FCC7) is all-zero and must stay that way
        p = make_prg()
        p[0x1FCC7:0x1FCC7 + H.MM1_WEAPON_ENEMY_LEN] = bytes(
            [0x14] * H.MM1_WEAPON_ENEMY_LEN)
        H.apply_hard_mode(p)
        self.assertEqual(list(p[0x1FCC7:0x1FCC7 + H.MM1_WEAPON_ENEMY_LEN]),
                         [0x14] * H.MM1_WEAPON_ENEMY_LEN)


class OhkoTests(unittest.TestCase):
    def test_size_guard(self):
        with self.assertRaises(H.HardModeError):
            H.apply_ohko(bytearray(16))

    def test_instant_death_both_ways(self):
        p = make_prg()
        H.apply_ohko(p)
        self.assertEqual(set(p[H.MM1_ENEMY_HIT_TABLE:
                             H.MM1_ENEMY_HIT_TABLE + H.MM1_ENEMY_HIT_LEN]),
                         {0xFF})
        self.assertEqual(set(p[H.MM1_BOSS_CONTACT:
                             H.MM1_BOSS_CONTACT + H.MM1_BOSS_CONTACT_LEN]),
                         {0xFF})
        self.assertEqual(set(p[H.MM1_WEAPON_ON_BOSS:
                             H.MM1_WEAPON_ON_BOSS + H.MM1_WEAPON_ON_BOSS_ROWS * 8]),
                         {0xFF})
        for off in H.MM1_WEAPON_ENEMY:
            self.assertEqual(set(p[off:off + H.MM1_WEAPON_ENEMY_LEN]), {0xFF})

    def test_ice_table_untouched(self):
        p = make_prg()
        H.apply_ohko(p)
        self.assertEqual(set(p[0x1FCC7:0x1FCC7 + H.MM1_WEAPON_ENEMY_LEN]), {0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
