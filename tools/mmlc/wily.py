"""Wily Stages custom patches for MM1 (offline PRG patchers).

Features:
- Victory lap mode: max damage (14) for all weapons vs all bosses.
- Door color coding: tint each boss door with the colors of the robot
  master whose weapon is that boss's weakness (all 6 robot stages).

Everything operates on a headerless MM1 PRG ``bytearray`` in place.
See docs/RE_FACTS.md ("MM1 boss doors") for the verified offsets.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import patcher  # noqa: F401  (re-exported for callers)
from . import romtable  # noqa: F401

# MM1 PRG offsets (from randomizer.py and RE_FACTS.md)
MM1_DAMAGE_TABLE = 0x1FDEE      # 6 bosses x 8 weapons (P C I B F E G M)

# Boss door palettes: per-stage "after doors" BG palette rows.
#
# Verified against the mm1dasm disassembly (bank5.s `ScrollNextRoom`): when
# Mega Man scrolls into a stage's first-door screen, the game copies 16 bytes
# from `AfterDoorsPalette[stage * 16]` (CPU $9F0D) into the BG palette RAM and
# only then opens the door -- so the boss shutter is drawn with this row.
# The shutter tiles use BG palette slots {0, 2} (decoded from the shared
# `RoomShutterBlockPals` attribute bytes for the shutter entries used
# by all six robot-master boss doors).
#
# Stage order is Cut, Ice, Bomb, Fire, Elec, Guts (index 0-5). Verified three
# ways: (1) `FightingBossNum = CurrentStage` at PRG 0x1503A, so the damage
# table row order equals the stage order; (2) the vanilla weapon-reward table
# reads C,I,B,F,E,G in order, and each stage awards its own boss's weapon;
# (3) the stage-select portrait loop pairs the same order with
# `WeaponsOwnedBits`.
MM1_AFTER_DOORS_BASE = 0x15F0D  # PRG offset of stage 0 row; 6 rows x 16 bytes
MM1_AFTER_DOORS_STRIDE = 0x10
# BG palette slots used by the boss shutter tiles (0-based; each slot 4 bytes)
MM1_DOOR_PALETTE_SLOTS = (0, 2)

# Boss order (Database order DB-RM1-NAME-25..30)
# 0=Cut, 1=Ice, 2=Bomb, 3=Fire, 4=Elec, 5=Guts
BOSS_NAMES = ["Cut Man", "Ice Man", "Bomb Man", "Fire Man", "Elec Man", "Guts Man"]
WEAPON_NAMES = ["Buster", "Rolling Cutter", "Ice Slasher", "Hyper Bomb",
                "Fire Storm", "Thunder Beam", "Super Arm", "Magnet Beam"]


def get_boss_weakness(prg: bytes, boss_idx: int) -> int:
    """Return the weapon index (0-7) that does max damage to this boss."""
    base = MM1_DAMAGE_TABLE + boss_idx * 8
    damages = prg[base:base + 8]
    return damages.index(max(damages))


# Map weapon index -> robot master index whose weapon it is
# 0=P(Buster), 1=C(Rolling Cutter->Cut Man), 2=I(Ice Slasher->Ice Man)
# 3=B(Hyper Bomb->Bomb Man), 4=F(Fire Storm->Fire Man)
# 5=E(Thunder Beam->Elec Man), 6=G(Super Arm->Guts Man), 7=M(Magnet Beam->none)
WEAPON_TO_ROBOT_MASTER = {
    1: 0,  # Rolling Cutter -> Cut Man
    2: 1,  # Ice Slasher -> Ice Man
    3: 2,  # Hyper Bomb -> Bomb Man
    4: 3,  # Fire Storm -> Fire Man
    5: 4,  # Thunder Beam -> Elec Man
    6: 5,  # Super Arm -> Guts Man
}

# Robot master signature colors (light, dark) from their sprite palettes
ROBOT_MASTER_COLORS = [
    (0x16, 0x06),  # Cut Man - red
    (0x21, 0x11),  # Ice Man - light blue
    (0x2A, 0x1A),  # Bomb Man - green
    (0x16, 0x06),  # Fire Man - red/orange (same as Cut)
    (0x30, 0x20),  # Elec Man - yellow
    (0x27, 0x17),  # Guts Man - orange/brown
]

# Vanilla Wily-stage damage rows (stages 6-10: Wily 1-4 + weapon-get
# screen), byte-exact from the pristine US ROM (Proteus.exe.orig.bak,
# PRG CRC 0x1c47d202).
#
# Wily refights use their Wily stage's rows (7 for Wily 2, 9 for Wily 4),
# never the robot rows 0-5: no code remaps FightingBossNum for refights
# (verified: the only stores to $AC are stage init `STA $AC,CurrentStage`
# at PRG 0x1503A, the defeat fanfare, and Wily-7/transition code). The
# victory-lap (rows 0-5) and door (AfterDoors rows 0-5) patches therefore
# cannot touch Wily content; restoring these rows makes "Vanilla Wily"
# explicit and also repairs dirty sources. Disjoint from the other two
# patches, so all three combine freely.
MM1_WILY_ROWS_BASE = MM1_DAMAGE_TABLE + 6 * 8
MM1_WILY_ROWS_VANILLA = bytes([
    2, 2, 0, 0, 2, 4, 0, 0,  # Wily 1 (Yellow Devil)
    1, 1, 0, 2, 2, 2, 0, 0,  # Wily 2 (Copy Robot + Cut/Elec refights)
    0, 0, 0, 0, 0, 0, 0, 0,  # Wily 3 (CWU-01P uses enemy damage path)
    1, 1, 1, 1, 4, 1, 1, 0,  # Wily 4 (Wily Machine + refights)
    1, 1, 0, 1, 1, 1, 1, 0,  # weapon-get screen (transient $AC=10)
])
# Vanilla weaknesses by damage-table argmax (weapon index per boss row):
# Cut Man (0) -> Super Arm (Guts Man), Ice Man (1) -> Thunder Beam (Elec Man),
# Bomb Man (2) -> Fire Storm (Fire Man), Fire Man (3) -> Ice Slasher (Ice Man),
# Elec Man (4) -> Rolling Cutter (Cut Man), Guts Man (5) -> Hyper Bomb (Bomb Man).
# Note this is the per-hit damage data (the randomizer/spoiler convention);
# traditionally Ice Man is listed as Hyper Bomb-weak (multi-hit in practice).
VANILLA_WEAKNESS = [6, 5, 4, 2, 1, 3]


@dataclass
class WilyPatchConfig:
    """Configuration for Wily Stages patches.

    All three options are independent and combine freely (they touch
    disjoint PRG ranges): victory lap rewrites robot rows 0-5, vanilla
    Wily restores Wily rows 6-10, doors tint the AfterDoors rows.
    """
    victory_lap: bool = False
    colored_doors: bool = False
    vanilla_wily: bool = False


def apply_victory_lap(prg: bytearray) -> None:
    """Max out the Wily bosses' damage (14) for easy kills.

    Only the Wily rows (6-10) are touched; the six robot-master rows (0-5)
    keep whatever the randomizer/vanilla left them as, so the robot fights
    and their weakness colors are unaffected.
    """
    for boss in range(6, 11):
        base = MM1_DAMAGE_TABLE + boss * 8
        prg[base:base + 8] = bytes([14] * 8)
    print("  Applied victory lap: Wily boss damage (rows 6-10) set to 14 (max)")


def apply_vanilla_wily(prg: bytearray) -> None:
    """Restore the Wily-stage damage rows (6-10) to vanilla bytes."""
    end = MM1_WILY_ROWS_BASE + len(MM1_WILY_ROWS_VANILLA)
    if end > len(prg):
        print("  Warning: Wily rows out of range; skipped")
        return
    if bytes(prg[MM1_WILY_ROWS_BASE:end]) == MM1_WILY_ROWS_VANILLA:
        print("  Vanilla Wily: Wily rows already vanilla")
    else:
        prg[MM1_WILY_ROWS_BASE:end] = MM1_WILY_ROWS_VANILLA
        print("  Vanilla Wily: Wily rows restored to vanilla")


def apply_colored_doors(prg: bytearray, boss_weaknesses: list[int]) -> None:
    """Tint each boss door with its weakness robot master's colors.

    For every robot-master stage, patch BG palette slots {0, 2} of that
    stage's "after doors" row (the palette the boss shutter is drawn with)
    to the weakness robot master's (light, dark) pair. Byte 0 of each slot
    ($0F backdrop, shared screen-wide) is preserved; bytes 1-3 become
    [light, dark, light] so the shutter stripes keep their contrast.
    """
    patched = 0
    for boss_idx in range(6):
        weak_weapon = boss_weaknesses[boss_idx]
        if weak_weapon not in WEAPON_TO_ROBOT_MASTER:
            print(f"  Warning: boss {BOSS_NAMES[boss_idx]} weakness "
                  f"{WEAPON_NAMES[weak_weapon]} has no robot master; skipped")
            continue
        rm_idx = WEAPON_TO_ROBOT_MASTER[weak_weapon]
        light, dark = ROBOT_MASTER_COLORS[rm_idx]
        row = MM1_AFTER_DOORS_BASE + boss_idx * MM1_AFTER_DOORS_STRIDE
        if row + 16 > len(prg):
            print(f"  Warning: boss {BOSS_NAMES[boss_idx]} palette row out of range")
            continue
        for slot in MM1_DOOR_PALETTE_SLOTS:
            base = row + slot * 4
            assert prg[base] == 0x0F, (
                f"unexpected backdrop {prg[base]:#04x} at {base:#x}; aborting "
                f"rather than corrupting the shared backdrop color")
            prg[base + 1] = light
            prg[base + 2] = dark
            prg[base + 3] = light
        print(f"  {BOSS_NAMES[boss_idx]} door: weakness={WEAPON_NAMES[weak_weapon]} "
              f"-> {BOSS_NAMES[rm_idx]} color (light={light:02X}, dark={dark:02X})")
        patched += 1

    print(f"  Applied colored doors: {patched}/6 boss door(s) tinted to weakness colors")


def apply_patches(prg: bytearray, config: WilyPatchConfig) -> None:
    """Apply all selected patches to the MM1 PRG."""
    print("Applying Wily Stages patches...")

    # Read weaknesses from damage table BEFORE victory lap modifies it
    boss_weaknesses = []
    if config.colored_doors:
        for boss_idx in range(6):
            boss_weaknesses.append(get_boss_weakness(prg, boss_idx))

    if config.victory_lap:
        apply_victory_lap(prg)

    if config.vanilla_wily:
        apply_vanilla_wily(prg)

    if config.colored_doors:
        apply_colored_doors(prg, boss_weaknesses)

    print("Done.")
