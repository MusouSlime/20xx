"""Tests for the MM1 Wily Stages patches (synthetic PRG, no game assets)."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import wily as W

# vanilla MM1 damage charts (P C I B F E G M), one per boss
VANILLA_CHARTS = [
    [3, 1, 0, 2, 3, 1, 14, 0],
    [1, 2, 0, 4, 1, 10, 0, 0],
    [2, 2, 0, 1, 4, 2, 0, 0],
    [2, 2, 4, 1, 1, 1, 0, 0],
    [1, 10, 0, 2, 1, 1, 4, 0],
    [2, 1, 0, 10, 2, 1, 1, 0],
]

# vanilla "after doors" rows (PRG 0x15F0D + 16*i)
VANILLA_ROWS = [
    bytes.fromhex("0F302B06 0F302B2C 0F201000 0F20102C"),
    bytes.fromhex("0F202111 0F203C2C 0F20212C 0F20091C"),
    bytes.fromhex("0F201011 0F201021 0F291907 0F270621"),
    bytes.fromhex("0F301006 0F1B0B00 0F271606 0F261606"),
    bytes.fromhex("0F202717 0F202721 0F0F0F0C 0F282918"),
    bytes.fromhex("0F261707 0F301002 0F261702 0F372717"),
]


def make_prg() -> bytearray:
    p = bytearray(0x20000)
    for b, chart in enumerate(VANILLA_CHARTS):
        base = W.MM1_DAMAGE_TABLE + b * 8
        p[base:base + 8] = bytes(chart)
    for i, row in enumerate(VANILLA_ROWS):
        base = W.MM1_AFTER_DOORS_BASE + i * W.MM1_AFTER_DOORS_STRIDE
        p[base:base + 16] = row
    return p


class WilyTests(unittest.TestCase):
    def test_victory_lap_maxes_wily_only(self):
        p = make_prg()
        before = bytes(p)
        W.apply_victory_lap(p)
        # Wily rows 6-10 maxed ...
        for b in range(6, 11):
            base = W.MM1_DAMAGE_TABLE + b * 8
            self.assertEqual(list(p[base:base + 8]), [14] * 8)
        # ... robot rows 0-5 untouched
        for b in range(6):
            base = W.MM1_DAMAGE_TABLE + b * 8
            self.assertEqual(bytes(p[base:base + 8]), before[base:base + 8])

    def test_weakness_argmax_matches_vanilla(self):
        p = make_prg()
        got = [W.get_boss_weakness(p, b) for b in range(6)]
        self.assertEqual(got, W.VANILLA_WEAKNESS)

    def test_doors_tint_slots_keep_backdrop(self):
        p = make_prg()
        weak = [W.get_boss_weakness(p, b) for b in range(6)]
        W.apply_colored_doors(p, weak)
        for i in range(6):
            row = W.MM1_AFTER_DOORS_BASE + i * W.MM1_AFTER_DOORS_STRIDE
            rm = W.WEAPON_TO_ROBOT_MASTER[weak[i]]
            light, dark = W.ROBOT_MASTER_COLORS[rm]
            for slot in W.MM1_DOOR_PALETTE_SLOTS:
                base = row + slot * 4
                # $0F backdrop preserved, chromatic bytes tinted
                self.assertEqual(p[base], 0x0F)
                self.assertEqual((p[base + 1], p[base + 2], p[base + 3]),
                                 (light, dark, light))
            # untouched slots stay vanilla
            for slot in (1, 3):
                base = row + slot * 4
                self.assertEqual(bytes(p[base:base + 4]),
                                 VANILLA_ROWS[i][slot * 4:slot * 4 + 4])

    def test_doors_unaffected_by_victory_lap(self):
        # victory lap only touches the Wily rows, so the door colors still
        # come from the (vanilla) robot weaknesses.
        p = make_prg()
        W.apply_patches(p, W.WilyPatchConfig(victory_lap=True, colored_doors=True))
        # Cut door got Guts colors (Super Arm)
        row = W.MM1_AFTER_DOORS_BASE
        self.assertEqual((p[row + 1], p[row + 2]), (0x27, 0x17))

    def test_door_cut_equals_guts_colors(self):
        p = make_prg()
        W.apply_colored_doors(p, [6, 5, 4, 2, 1, 3])
        names = ["Cut", "Ice", "Bomb", "Fire", "Elec", "Guts"]
        want_rm = [5, 4, 3, 1, 0, 2]  # Guts, Elec, Fire, Ice, Cut, Bomb
        for i, (n, rm) in enumerate(zip(names, want_rm)):
            row = W.MM1_AFTER_DOORS_BASE + i * W.MM1_AFTER_DOORS_STRIDE
            light, dark = W.ROBOT_MASTER_COLORS[rm]
            self.assertEqual((p[row + 1], p[row + 2]), (light, dark),
                             f"{n} door")

    def test_backdrop_guard(self):
        p = make_prg()
        p[W.MM1_AFTER_DOORS_BASE] = 0x00  # corrupt shared backdrop
        with self.assertRaises(AssertionError):
            W.apply_colored_doors(p, [6, 5, 4, 2, 1, 3])

    def test_buster_weakness_skipped(self):
        p = make_prg()
        before = bytes(p)
        W.apply_colored_doors(p, [0, 5, 4, 2, 1, 3])  # boss 0 -> Buster
        # Cut row untouched; the rest tinted
        row0 = W.MM1_AFTER_DOORS_BASE
        self.assertEqual(bytes(p[row0:row0 + 16]), VANILLA_ROWS[0])
        self.assertNotEqual(bytes(p), before)

    def test_vanilla_wily_restores_rows(self):
        p = make_prg()
        # dirty the Wily rows plus a robot row
        for off in (6 * 8, 7 * 8, 9 * 8):
            base = W.MM1_DAMAGE_TABLE + off
            p[base:base + 8] = bytes([9] * 8)
        p[W.MM1_DAMAGE_TABLE:W.MM1_DAMAGE_TABLE + 8] = bytes([14] * 8)
        W.apply_vanilla_wily(p)
        # Wily rows 6-10 byte-exact vanilla ...
        base = W.MM1_WILY_ROWS_BASE
        self.assertEqual(bytes(p[base:base + 40]), W.MM1_WILY_ROWS_VANILLA)
        # ... robot row 0 untouched by the restore
        self.assertEqual(list(p[W.MM1_DAMAGE_TABLE:W.MM1_DAMAGE_TABLE + 8]),
                         [14] * 8)

    def test_vanilla_wily_clean_is_noop(self):
        p = make_prg()
        before = bytes(p)
        # robot rows are zeroed in the synthetic PRG outside charts/rows;
        # vanilla restore only touches rows 6-10 (already zero here) --
        # emulate pristine by writing the vanilla rows first
        base = W.MM1_WILY_ROWS_BASE
        p[base:base + 40] = W.MM1_WILY_ROWS_VANILLA
        before = bytes(p)
        W.apply_vanilla_wily(p)
        self.assertEqual(bytes(p), before)

    def test_all_three_combine(self):
        p = make_prg()
        W.apply_patches(p, W.WilyPatchConfig(
            victory_lap=True, colored_doors=True, vanilla_wily=True))
        # victory lap only touches Wily rows; robot rows stay vanilla ...
        for b in range(6):
            base = W.MM1_DAMAGE_TABLE + b * 8
            self.assertEqual(list(p[base:base + 8]), VANILLA_CHARTS[b])
        # ... and vanilla Wily then restores those Wily rows
        base = W.MM1_WILY_ROWS_BASE
        self.assertEqual(bytes(p[base:base + 40]), W.MM1_WILY_ROWS_VANILLA)
        # doors: Cut door got Guts colors (from the robot weaknesses)
        row = W.MM1_AFTER_DOORS_BASE
        self.assertEqual((p[row + 1], p[row + 2]), (0x27, 0x17))


if __name__ == "__main__":
    unittest.main(verbosity=2)
