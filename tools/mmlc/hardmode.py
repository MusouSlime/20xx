"""MM1 Hard Mode: 2x damage received, ~2/3 damage dealt.

Verified against the mm1dasm disassembly (bank5/bank7) and the MMLC ROM:

* Damage **received** (enemy/boss -> Mega Man) is read from two static
  tables and subtracted from the player's meter at PRG ``0x1623A``
  (``SEC; LDA Meters; SBC $0C``):
  - ``EnemyHitTable``   ($FE89, 50 bytes, indexed by ``ObjectType``)
  - ``L1FEEF``          ($FEBB, 11 bytes, indexed by ``FightingBossNum``)
* Damage **dealt** (Mega Man -> boss/enemy):
  - ``WeaponDamageOnBoss`` ($FDEE, 12 rows x 8: robot + Wily + get rows)
  - ``WeaponDamageP/C/B/F/E/G`` ($FC51.., 59 bytes each, indexed by
    ``ObjectType``). ``WeaponDamageI`` ($FCC7) is all-zero (Ice Slasher's
    freeze path is special) and is left untouched.

Because every value lives in a static table, hard mode is a pure data
patch -- no 6502 injection -- so it composes with the randomizer, victory
lap, palette shuffle, etc. It is applied *after* those (so it scales the
final numbers) and preserves each row's argmax, keeping the weakness /
visualizer / cursor-tint colors correct.

Scaling: received = ``min(255, v*2)``; dealt = ``0 if v == 0 else
max(1, (v*2+1)//3)`` (i.e. /1.5, round-half-up, never turning a no-damage
entry into damage).
"""
from __future__ import annotations

# --- MM1 PRG offsets (validated against Proteus.exe.orig.bak) --------------
MM1_ENEMY_HIT_TABLE = 0x1FE89      # enemy/projectile -> player
MM1_ENEMY_HIT_LEN = 50
MM1_BOSS_CONTACT = 0x1FEBB         # boss contact -> player
MM1_BOSS_CONTACT_LEN = 11
MM1_WEAPON_ON_BOSS = 0x1FDEE       # player weapon -> boss (12 rows x 8)
MM1_WEAPON_ON_BOSS_ROWS = 12
MM1_WEAPON_ENEMY = [0x1FC51, 0x1FC8C, 0x1FD02, 0x1FD3D, 0x1FD78, 0x1FDB3]
MM1_WEAPON_ENEMY_LEN = 59
MM1_PRG_SIZE = 0x20000
MM1_INSTANT = 0xFF         # any value >= target HP = one hit kill


class HardModeError(RuntimeError):
    pass


def scale_received(v: int) -> int:
    """Damage the player takes: doubled (clamped to a byte)."""
    return min(0xFF, v * 2)


def scale_dealt(v: int) -> int:
    """Damage the player deals: divided by 1.5, round-half-up, 0 stays 0."""
    return 0 if v == 0 else max(1, (v * 2 + 1) // 3)


def _scale_region(prg: bytearray, off: int, length: int, fn) -> None:
    for i in range(off, off + length):
        prg[i] = fn(prg[i])


def apply_hard_mode(prg: bytearray) -> None:
    """Apply MM1 hard mode in place (2x received, /1.5 dealt)."""
    if len(prg) != MM1_PRG_SIZE:
        raise HardModeError(
            f"MM1 PRG must be {MM1_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    _scale_region(prg, MM1_ENEMY_HIT_TABLE, MM1_ENEMY_HIT_LEN, scale_received)
    _scale_region(prg, MM1_BOSS_CONTACT, MM1_BOSS_CONTACT_LEN, scale_received)
    _scale_region(prg, MM1_WEAPON_ON_BOSS,
                  MM1_WEAPON_ON_BOSS_ROWS * 8, scale_dealt)
    for off in MM1_WEAPON_ENEMY:
        _scale_region(prg, off, MM1_WEAPON_ENEMY_LEN, scale_dealt)


def apply_ohko(prg: bytearray) -> None:
    """MM1 OHKO: any hit is instant death (spike-like), both directions.

    Every received-damage entry becomes 0xFF (>= the 28-unit player meter,
    so any enemy/boss touch kills) and every dealt-damage entry becomes
    0xFF (>= any enemy/boss HP, so any weapon one-shots). ``WeaponDamageI``
    (the all-zero Ice freeze table) is left untouched.

    This flattens the boss table, so the weakness argmax (and thus the
    visualizer / cursor-tint colors) is meaningless afterward -- callers
    should apply OHKO *after* the randomizer and skip the cursor-hook
    refresh so the pre-OHKO weakness colors are preserved.
    """
    if len(prg) != MM1_PRG_SIZE:
        raise HardModeError(
            f"MM1 PRG must be {MM1_PRG_SIZE:#x} bytes, got {len(prg):#x}")
    for off, ln in ((MM1_ENEMY_HIT_TABLE, MM1_ENEMY_HIT_LEN),
                    (MM1_BOSS_CONTACT, MM1_BOSS_CONTACT_LEN)):
        prg[off:off + ln] = bytes([MM1_INSTANT]) * ln
    n = MM1_WEAPON_ON_BOSS_ROWS * 8
    prg[MM1_WEAPON_ON_BOSS:MM1_WEAPON_ON_BOSS + n] = bytes([MM1_INSTANT]) * n
    for off in MM1_WEAPON_ENEMY:
        prg[off:off + MM1_WEAPON_ENEMY_LEN] = (
            bytes([MM1_INSTANT]) * MM1_WEAPON_ENEMY_LEN)
