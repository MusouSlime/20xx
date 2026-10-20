#!/usr/bin/env python3
"""Command-line front end for the MM1 Wily Stages patches (patch-only).

Examples:
  wily_patches.py Suzy/Proteus.exe --victory-lap --colored-doors
  wily_patches.py Suzy/Proteus.exe.orig.bak --victory-lap --apply
  wily_patches.py Suzy/Proteus.exe --colored-doors --out mm1_doors.nes

Operates on the user's own legally-owned install; every write is
same-size and backed up first.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mmlc import patcher, romtable, wily
from mmlc.backup import BackupManager
from mmlc.pe import PE, write_file_atomic


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply Wily Stages patches to MM1 in MMLC1")
    parser.add_argument("exe", help="Path to Proteus.exe (or .orig.bak)")
    parser.add_argument("--victory-lap", action="store_true",
                        help="Victory lap mode: max damage (easy kills)")
    parser.add_argument("--colored-doors", action="store_true",
                        help="Color each boss room door to the weakness robot master's color")
    parser.add_argument("--vanilla-wily", action="store_true",
                        help="Restore the Wily-stage damage rows (6-10) to vanilla")
    parser.add_argument("--apply", action="store_true", help="Write changes to exe")
    parser.add_argument("--out", help="Write patched PRG as iNES here")
    args = parser.parse_args(argv)

    config = wily.WilyPatchConfig(
        victory_lap=args.victory_lap,
        colored_doors=args.colored_doors,
        vanilla_wily=args.vanilla_wily,
    )
    if not (config.victory_lap or config.colored_doors or config.vanilla_wily):
        parser.error("At least one patch option required")

    pe = PE(args.exe)
    g = romtable.resolve("mm1")
    prg = bytearray(patcher.read_prg(pe, g))

    print(f"Loaded MM1 PRG ({len(prg):#x} bytes) from {args.exe}")
    print(f"Original PRG CRC32: {patcher.crc32(bytes(prg)):#010x}")

    wily.apply_patches(prg, config)

    print(f"New PRG CRC32: {patcher.crc32(bytes(prg)):#010x}")

    if args.out:
        write_file_atomic(args.out, g.ines_header + bytes(prg))
        print(f"Wrote iNES ROM to {args.out}")

    if args.apply:
        backup = BackupManager(args.exe)
        entry = backup.backup("pre-wily-patches")
        print(f"Backed up -> {entry.backup_file} ({entry.sha256[:12]})")
        data = bytearray(pe.data)
        patcher.write_game(data, g, bytes(prg))
        write_file_atomic(args.exe, bytes(data))
        print(f"Wrote patched MM1 to {args.exe}")
    else:
        print("Dry run complete. Use --apply to write to exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
