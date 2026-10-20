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
    # fixtures for the visualizer/green/cursor-hook paths (real-ROM values)
    p[R.MM1_HOOK_JSR_SITE:R.MM1_HOOK_JSR_SITE + 3] = bytes([0x85, 0x1C, 0x60])
    p[R.MM1_STAGESEL_PAL:R.MM1_STAGESEL_PAL + 32] = bytes.fromhex(
        "0f3c21110f3035110f280f0f0f2815270f20210f0f20380f0f20150f0f27150f")
    p[R.MM1_VIS_ROUTINE_STA:R.MM1_VIS_ROUTINE_STA + 3] = bytes(
        [0x8D, 0x07, 0x20])
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
        # weapons 1-7 are a permutation of vanilla; buster (col 0) is
        # flattened separately (see test_buster_flattened).
        before = sorted(tuple(r[1:]) for r in VANILLA)
        after = sorted(tuple(r[1:]) for r in R._read_charts(p))
        self.assertEqual(before, after)

    def test_buster_flattened(self):
        # the Guts-weak boss keeps buster 3; every other boss drops to 1
        for seed in ("x", "alpha", "bravo", "zulu"):
            p = make_prg()
            R.randomize_mm1(p, seed, weapons=False, palette=False)
            charts = R._read_charts(p)
            bcol = [r[0] for r in charts]
            self.assertEqual(sorted(bcol), [1, 1, 1, 1, 1, 3])
            guts = [b for b, r in enumerate(charts) if r[6] == 14]
            self.assertEqual(len(guts), 1)
            self.assertEqual(charts[guts[0]][0], 3)

    def test_green_points_to_guts_weak(self):
        for seed in ("x", "alpha", "bravo"):
            p = make_prg()
            R.randomize_mm1(p, seed, weapons=False, palette=False,
                            visualizer=True)
            charts = R._read_charts(p)
            guts = [b for b, r in enumerate(charts) if r[6] == 14][0]
            self.assertEqual(p[R.MM1_GREEN_BOSS], guts)

    def test_wily_rows_shuffled(self):
        # Wily rows (6-10) are permuted among themselves; the all-zero
        # CWU-01P row (index 8) stays put.
        p = make_prg()
        base = R.MM1_WILY_ROWS_BASE
        p[base:base + 40] = bytes([
            2, 2, 0, 0, 2, 4, 0, 0,
            1, 1, 0, 2, 2, 2, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0,
            1, 1, 1, 1, 4, 1, 1, 0,
            1, 1, 0, 1, 1, 1, 1, 0])
        R.randomize_mm1(p, "wily", weapons=False, palette=False)
        rows = [list(p[base + i * 8:base + i * 8 + 8]) for i in range(5)]
        self.assertEqual(rows[2], [0] * 8)  # CWU row fixed
        before = sorted([
            (2, 2, 0, 0, 2, 4, 0, 0), (1, 1, 0, 2, 2, 2, 0, 0),
            (1, 1, 1, 1, 4, 1, 1, 0), (1, 1, 0, 1, 1, 1, 1, 0)])
        after = sorted([tuple(rows[i]) for i in (0, 1, 3, 4)])
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

    def test_describe_mm1_reports_current_charts(self):
        p = make_prg()
        before = bytes(p)
        s = R.describe_mm1(p)
        # read-only
        self.assertEqual(bytes(p), before)
        # vanilla argmax mapping (damage-table convention)
        self.assertEqual(s.weaknesses, {
            "DB-RM1-NAME-25": "superarm",      # Cut <- Super Arm
            "DB-RM1-NAME-26": "thunderbeam",   # Ice <- Thunder Beam (10 > Hyper 4)
            "DB-RM1-NAME-27": "firestorm",     # Bomb <- Fire Storm
            "DB-RM1-NAME-28": "iceslasher",    # Fire <- Ice Slasher
            "DB-RM1-NAME-29": "rollingcutter",  # Elec <- Rolling Cutter
            "DB-RM1-NAME-30": "hyperbomb",     # Guts <- Hyper Bomb
        })

    def test_describe_mm1_matches_randomize_spoiler(self):
        p = make_prg()
        s = R.randomize_mm1(p, "check-seed", weapons=False, palette=False)
        d = R.describe_mm1(p)
        self.assertEqual(d.weaknesses, s.weaknesses)

    def test_describe_mm1_size_guard(self):
        with self.assertRaises(R.RandomizerError):
            R.describe_mm1(bytearray(16))

    def test_green_marks_top_buster(self):
        # vanilla buster column is [3,1,2,2,1,2] -> boss 0 wins
        p = make_prg()
        R.randomize_mm1(p, "x", weakness=False, weapons=False,
                        palette=False, visualizer=True)
        self.assertEqual(p[R.MM1_VIS_ROUTINE_STA:R.MM1_VIS_ROUTINE_STA + 3],
                         bytes([0x4C, 0x35, 0xFF]))
        self.assertEqual(
            bytes(p[R.MM1_GREEN_CODE:R.MM1_GREEN_CODE + 13]),
            bytes([0xCC, 0x43, 0xFF, 0xD0, 0x02, 0xA9, 0x2A,
                   0x8D, 0x07, 0x20, 0x4C, 0x2E, 0xFF]))
        self.assertEqual(p[R.MM1_GREEN_BOSS], 0)

    def test_green_follows_shuffled_charts(self):
        for seed in ("alpha", "bravo", "charlie"):
            p = make_prg()
            R.randomize_mm1(p, seed, weapons=False, palette=False,
                            visualizer=True)
            charts = R._read_charts(p)
            top = max(range(6), key=lambda b: charts[b][0])
            self.assertEqual(p[R.MM1_GREEN_BOSS], top)

    def test_green_needs_visualizer(self):
        p = make_prg()  # no IPS applied
        p[R.MM1_VIS_ROUTINE_STA:R.MM1_VIS_ROUTINE_STA + 3] = bytes(3)
        with self.assertRaises(R.RandomizerError):
            R.apply_green_top_buster(p, R._read_charts(p))

    def test_visualizer_flat_charts_no_crash(self):
        # victory-lap-style charts (all 14): argmax is Buster (index 0),
        # which has no weakness value -> default 0x00, green goes to boss 0.
        p = make_prg()
        for b in range(6):
            base = R.MM1_DAMAGE_TABLE + b * 8
            p[base:base + 8] = bytes([14] * 8)
        R.randomize_mm1(p, "x", weakness=False, weapons=False,
                        palette=False, visualizer=True)
        self.assertEqual(list(p[R.MM1_WEAKNESS_TABLE:R.MM1_WEAKNESS_TABLE + 6]),
                         [0x00] * 6)
        self.assertEqual(p[R.MM1_GREEN_BOSS], 0)


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


class Hook6502:
    """Minimal 6502 for the cursor-hook subset (test-only simulator)."""

    def __init__(self):
        self.mem = bytearray(0x10000)
        self.a = self.x = self.y = 0
        self.n = self.z = self.c = False
        self.pc = 0

    def _nz(self, v):
        self.n = bool(v & 0x80)
        self.z = (v & 0xFF) == 0

    def run(self, pc, a, limit=2000):
        self.pc, self.a = pc, a & 0xFF
        for _ in range(limit):
            op = self.mem[self.pc]
            if op == 0x85:  # sta zp
                self.mem[self.mem[self.pc + 1]] = self.a
                self.pc += 2
            elif op == 0xC9:  # cmp #imm
                v = self.mem[self.pc + 1]
                self.c = self.a >= v
                self._nz(self.a - v)
                self.pc += 2
            elif op == 0xA5:  # lda zp
                self.a = self.mem[self.mem[self.pc + 1]]
                self._nz(self.a)
                self.pc += 2
            elif op == 0xA9:  # lda #imm
                self.a = self.mem[self.pc + 1]
                self._nz(self.a)
                self.pc += 2
            elif op == 0xAD:  # lda abs
                ad = self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8)
                self.a = self.mem[ad]
                self._nz(self.a)
                self.pc += 3
            elif op == 0xBD:  # lda abs,x
                ad = (self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8))
                self.a = self.mem[(ad + self.x) & 0xFFFF]
                self._nz(self.a)
                self.pc += 3
            elif op == 0xB1:  # lda (zp),y
                zp = self.mem[self.pc + 1]
                ad = (self.mem[zp] | (self.mem[(zp + 1) & 0xFF] << 8))
                self.a = self.mem[(ad + self.y) & 0xFFFF]
                self._nz(self.a)
                self.pc += 2
            elif op == 0xA6:  # ldx zp
                self.x = self.mem[self.mem[self.pc + 1]]
                self._nz(self.x)
                self.pc += 2
            elif op == 0xA2:  # ldx #imm
                self.x = self.mem[self.pc + 1]
                self._nz(self.x)
                self.pc += 2
            elif op == 0xA0:  # ldy #imm
                self.y = self.mem[self.pc + 1]
                self._nz(self.y)
                self.pc += 2
            elif op == 0x8D:  # sta abs
                ad = self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8)
                self.mem[ad] = self.a
                self.pc += 3
            elif op == 0x9D:  # sta abs,x
                ad = self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8)
                self.mem[(ad + self.x) & 0xFFFF] = self.a
                self.pc += 3
            elif op == 0x91:  # sta (zp),y
                zp = self.mem[self.pc + 1]
                ad = (self.mem[zp] | (self.mem[(zp + 1) & 0xFF] << 8))
                self.mem[(ad + self.y) & 0xFFFF] = self.a
                self.pc += 2
            elif op == 0x29:  # and #imm
                self.a &= self.mem[self.pc + 1]
                self._nz(self.a)
                self.pc += 2
            elif op == 0x05:  # ora zp
                self.a |= self.mem[self.mem[self.pc + 1]]
                self._nz(self.a)
                self.pc += 2
            elif op == 0x0A:  # asl a
                self.c = bool(self.a & 0x80)
                self.a = (self.a << 1) & 0xFF
                self._nz(self.a)
                self.pc += 1
            elif op == 0x8A:  # txa
                self.a = self.x
                self._nz(self.a)
                self.pc += 1
            elif op == 0xAA:  # tax
                self.x = self.a
                self._nz(self.x)
                self.pc += 1
            elif op == 0x98:  # tya
                self.a = self.y
                self._nz(self.a)
                self.pc += 1
            elif op == 0x88:  # dey
                self.y = (self.y - 1) & 0xFF
                self._nz(self.y)
                self.pc += 1
            elif op == 0xCA:  # dex
                self.x = (self.x - 1) & 0xFF
                self._nz(self.x)
                self.pc += 1
            elif op == 0xE0:  # cpx #imm
                v = self.mem[self.pc + 1]
                self.c = self.x >= v
                self._nz(self.x - v)
                self.pc += 2
            elif op == 0xE4:  # cpx zp
                v = self.mem[self.mem[self.pc + 1]]
                self.c = self.x >= v
                self._nz(self.x - v)
                self.pc += 2
            elif op == 0xEC:  # cpx abs
                ad = self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8)
                v = self.mem[ad]
                self.c = self.x >= v
                self._nz(self.x - v)
                self.pc += 3
            elif op in (0xD0, 0xF0, 0x10, 0x90, 0xB0):  # branches
                v = self.mem[self.pc + 1]
                off = v - 256 if v > 127 else v
                take = {0xD0: not self.z, 0xF0: self.z, 0x10: not self.n,
                        0x90: not self.c, 0xB0: self.c}[op]
                self.pc = (self.pc + 2 + off) & 0xFFFF if take else self.pc + 2
            elif op == 0x4C:  # jmp abs
                self.pc = self.mem[self.pc + 1] | (self.mem[self.pc + 2] << 8)
            elif op == 0x60:  # rts
                return
            else:
                raise AssertionError(f"sim: unhandled opcode {op:#04x}")
        raise AssertionError("sim: step limit exceeded")


def make_hooked_prg():
    """Synthetic PRG carrying the fixtures the hook needs (JSR site,
    stage-select palette, visualizer STA) for install tests."""
    return make_prg()


# vanilla argmax weapons per boss row -> robot-master (light, dark) pairs
VANILLA_WCOL = bytes([0x27, 0x17, 0x30, 0x20, 0x16, 0x06,
                      0x21, 0x11, 0x16, 0x06, 0x2A, 0x1A])


class CursorHookTests(unittest.TestCase):
    def test_code_fits_cave(self):
        code = R._cursor_hook_code()
        self.assertLessEqual(len(code), R.MM1_HOOK_CODE_LEN)

    def test_data_fits_cave(self):
        p = make_hooked_prg()
        data = R._cursor_hook_data(p, R._read_charts(p))
        self.assertEqual(len(data), 54)
        self.assertLessEqual(len(data), R.MM1_HOOK_DATA_LEN)

    def test_c_parity(self):
        import re
        code = R._cursor_hook_code()
        hdr = open(os.path.join(HERE, "..", "src", "proxy",
                               "mm1_rando.h"), "rb").read().decode()
        m = re.search(r"MM1_HOOK_CODE_BYTES\[\] = \{(.*?)\};", hdr, re.S)
        self.assertIsNotNone(m, "C hook array missing")
        cbytes = bytes(int(t, 16) for t in re.findall(r"0x([0-9A-Fa-f]{2})",
                                                      m.group(1)))
        self.assertEqual(cbytes, code)
        self.assertLessEqual(len(cbytes), R.MM1_HOOK_CODE_LEN)

    def test_wcol_vanilla(self):
        p = make_hooked_prg()
        self.assertEqual(R._cursor_hook_weakness_colors(R._read_charts(p)),
                         VANILLA_WCOL)

    def test_install(self):
        p = make_hooked_prg()
        charts = R._read_charts(p)
        R.apply_green_top_buster(p, charts)
        R.apply_cursor_hook(p, charts)
        self.assertEqual(p[R.MM1_HOOK_JSR_SITE:R.MM1_HOOK_JSR_SITE + 3],
                         bytes([0x4C, 0x44, 0xFF]))
        self.assertEqual(bytes(p[R.MM1_HOOK_CODE:R.MM1_HOOK_CODE
                                + len(R._cursor_hook_code())]),
                         R._cursor_hook_code())
        data = p[R.MM1_HOOK_DATA:R.MM1_HOOK_DATA + 54]
        self.assertEqual(data[32:44], VANILLA_WCOL)
        self.assertEqual(data[44:49], bytes(R.MM1_HOOK_FACE_BASES))
        self.assertEqual(data[49:54], bytes(R.MM1_HOOK_FACE_SLOTS))
        self.assertEqual(p[R.MM1_GREEN_BOSS], 0)  # vanilla buster col wins

    def test_install_guards(self):
        p = make_hooked_prg()
        charts = R._read_charts(p)
        q = bytearray(p)
        q[R.MM1_HOOK_JSR_SITE] = 0x00
        with self.assertRaises(R.RandomizerError):
            R.apply_cursor_hook(q, charts)
        q = bytearray(p)
        R.apply_green_top_buster(q, charts)
        R.apply_cursor_hook(q, charts)
        with self.assertRaises(R.RandomizerError):  # double install
            R.apply_cursor_hook(q, charts)

    def test_no_hook_without_visualizer(self):
        p = bytearray(R.MM1_PRG_SIZE)
        for b, chart in enumerate(VANILLA):
            p[R.MM1_DAMAGE_TABLE + b * 8:R.MM1_DAMAGE_TABLE + b * 8 + 8] = (
                bytes(chart))
        R.randomize_mm1(p, "x", visualizer=False)
        self.assertEqual(p[R.MM1_HOOK_JSR_SITE:R.MM1_HOOK_JSR_SITE + 3],
                         bytes(3))
        self.assertFalse(any(p[R.MM1_HOOK_CODE:R.MM1_HOOK_CODE + 156]))
        self.assertFalse(any(p[R.MM1_HOOK_DATA:R.MM1_HOOK_DATA + 59]))

    def test_cursor_tint_without_visualizer(self):
        # hook-only: installed without the visualizer IPS; $FF43 records
        # the top-buster index for the runtime green check, base image is
        # the vanilla L1BDAB, and no visualizer bytes are present.
        p = make_prg()
        R.randomize_mm1(p, "x", weakness=False, weapons=False,
                        palette=False, visualizer=False, cursor_tint=True)
        self.assertEqual(p[R.MM1_HOOK_JSR_SITE:R.MM1_HOOK_JSR_SITE + 3],
                         bytes([0x4C, 0x44, 0xFF]))
        self.assertEqual(bytes(p[R.MM1_HOOK_CODE:R.MM1_HOOK_CODE
                                + len(R._cursor_hook_code())]),
                         R._cursor_hook_code())
        self.assertEqual(p[R.MM1_GREEN_BOSS], 0)  # vanilla buster col wins
        self.assertEqual(bytes(p[R.MM1_HOOK_DATA:R.MM1_HOOK_DATA + 32]),
                         bytes.fromhex(
                             "0f3c21110f3035110f280f0f0f281527"
                             "0f20210f0f20380f0f20150f0f27150f"))
        # visualizer IPS absent: palette hunk + routine untouched
        self.assertEqual(p[R.MM1_STAGESEL_PAL + 9], 0x28)
        self.assertEqual(p[R.MM1_VIS_ROUTINE_STA:R.MM1_VIS_ROUTINE_STA + 3],
                         bytes([0x8D, 0x07, 0x20]))

    def test_cursor_tint_implied_by_visualizer(self):
        # backward compatibility: visualizer alone still installs the hook
        p = make_prg()
        R.randomize_mm1(p, "x", weakness=False, weapons=False,
                        palette=False, visualizer=True)
        self.assertEqual(p[R.MM1_HOOK_JSR_SITE:R.MM1_HOOK_JSR_SITE + 3],
                         bytes([0x4C, 0x44, 0xFF]))
        # ... alongside the green marker trampoline
        self.assertEqual(p[R.MM1_VIS_ROUTINE_STA:R.MM1_VIS_ROUTINE_STA + 3],
                         bytes([0x4C, 0x35, 0xFF]))

    def _sim_state(self, boss, top, hl=0x55, oam_slots=(2, 0, 3, 2, 3)):
        sim = Hook6502()
        code = R._cursor_hook_code()
        sim.mem[0xFF44:0xFF44 + len(code)] = code
        p = make_hooked_prg()
        charts = R._read_charts(p)
        data = bytes(p[R.MM1_STAGESEL_PAL:R.MM1_STAGESEL_PAL + 32])
        data += R._cursor_hook_weakness_colors(charts)
        data += bytes(R.MM1_HOOK_FACE_BASES) + bytes(R.MM1_HOOK_FACE_SLOTS)
        sim.mem[0xFCC7:0xFCC7 + 54] = data
        sim.mem[0xFF43] = top
        sim.mem[0x31] = boss
        sim.mem[0x032D] = hl  # F1BB02 wrote entry A here earlier in the call
        for f, (base, slot) in enumerate(zip(R.MM1_HOOK_FACE_BASES,
                                             oam_slots)):
            for i in range(9):  # flip bit set on Fire's face (like vanilla)
                sim.mem[0x0200 + base + i * 4] = (slot & 3) | (0x40 if f == 3
                                                              else 0)
        return sim

    def _face_slots(self, sim):
        out = []
        for base in R.MM1_HOOK_FACE_BASES:
            out.append(sorted({sim.mem[0x0200 + base + i * 4] & 3
                               for i in range(9)}))
        return out

    def test_sim_tints_selected(self):
        # boss 1 (Ice, weakness Elec -> 30,20); top-buster is boss 0
        sim = self._sim_state(boss=1, top=0)
        sim.run(0xFF44, a=0x8A)  # A at tail is $86/$8A, never $55
        self.assertEqual(self._face_slots(sim), [[2], [1], [3], [2], [3]])
        self.assertEqual(sim.mem[0x1C], 0x8A)  # tail A preserved via $1C
        self.assertEqual(bytes(sim.mem[0x03E5:0x03E8]), bytes([0x30, 0x20,
                                                              0x30]))
        self.assertEqual((sim.mem[0x3A], sim.mem[0x3B], sim.mem[0x34],
                          sim.mem[0x37]), (0xD0, 0x03, 0x20, 0x01))
        self.assertEqual(bytes(sim.mem[0x03D0:0x03E0]),
                         bytes.fromhex("0f3c21110f3035110f280f0f0f281527"))

    def test_sim_green_top_buster(self):
        sim = self._sim_state(boss=0, top=0)  # Cut is top -> solid green
        sim.run(0xFF44, a=0x8A)  # A at tail is $86/$8A, never $55
        self.assertEqual(self._face_slots(sim), [[1], [0], [3], [2], [3]])
        self.assertEqual(bytes(sim.mem[0x03E5:0x03E8]), bytes([0x2A] * 3))

    def test_sim_guts_mirrors_bg_slot(self):
        # boss 5 (Guts, weakness Bomb -> 2A,1A), top elsewhere
        sim = self._sim_state(boss=5, top=0)
        sim.run(0xFF44, a=0x8A)  # A at tail is $86/$8A, never $55
        self.assertEqual(self._face_slots(sim), [[2], [0], [3], [2], [3]])
        self.assertEqual(bytes(sim.mem[0x03E5:0x03E8]), bytes([0x2A, 0x1A,
                                                              0x2A]))
        self.assertEqual(bytes(sim.mem[0x03DD:0x03E0]), bytes([0x2A, 0x1A,
                                                              0x2A]))

    def test_sim_off_and_guard_skip(self):
        # gate reads $032D (F1BB02's entry A), not the tail A
        for hl, boss in ((0x00, 1), (0x55, 6)):
            sim = self._sim_state(boss=boss, top=0, hl=hl)
            sim.mem[0x1C] = 0xFF  # dirtied: sta $1C must restore it to A
            before = bytes(sim.mem)
            sim.run(0xFF44, a=0x8A)
            self.assertEqual(sim.mem[0x1C], 0x8A)
            # only $1C may change (sta $1C runs before the guards)
            after = bytes(sim.mem)
            diff = [i for i in range(0x10000) if before[i] != after[i]]
            self.assertEqual(diff, [0x1C])

    def test_refresh(self):
        p = make_hooked_prg()
        charts = R._read_charts(p)
        R.apply_green_top_buster(p, charts)
        R.apply_cursor_hook(p, charts)
        for b in range(6):  # victory-lap flats AFTER install
            p[R.MM1_DAMAGE_TABLE + b * 8:R.MM1_DAMAGE_TABLE + b * 8 + 8] = (
                bytes([14] * 8))
        R.refresh_cursor_hook(p)
        # all-Buster argmax -> own colors; top-buster tie -> boss 0
        self.assertEqual(bytes(p[R.MM1_HOOK_DATA + 32:R.MM1_HOOK_DATA + 44]),
                         bytes([0x16, 0x06, 0x21, 0x11, 0x2A, 0x1A,
                                0x16, 0x06, 0x30, 0x20, 0x27, 0x17]))
        self.assertEqual(p[R.MM1_GREEN_BOSS], 0)
        q = make_hooked_prg()
        with self.assertRaises(R.RandomizerError):
            R.refresh_cursor_hook(q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
