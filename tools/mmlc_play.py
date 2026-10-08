#!/usr/bin/env python3
"""Per-launch randomizer launcher for MMLC1.

Picks a seed (or takes one), writes ``roms/<game>.bin`` for the proxy to inject
at runtime, syncs the in-game Database weakness text, writes a spoiler, then
launches the game via Steam.

    mmlc_play.py --game mm1 --seed hello
    mmlc_play.py --game mm1 --seed random --no-launch

The game directory defaults to $MMLC_GAME, then the common Steam path.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmlc import menudb, randomizer, zipaes
from mmlc.backup import BackupManager
from mmlc.pe import write_file_atomic

DEFAULT_GAME = "/var/home/user/.local/share/Steam/steamapps/common/Suzy"


def find_pristine(game_dir: str) -> str:
    for name in ("Proteus.exe.orig.bak", "Proteus.exe"):
        p = os.path.join(game_dir, name)
        if os.path.exists(p):
            return p
    raise SystemExit(f"no Proteus.exe(.orig.bak) in {game_dir}")


def random_seed() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(10))


def sync_db(pie: str, gid: str, weaknesses: dict, password: str) -> None:
    with tempfile.TemporaryDirectory(prefix="mmlc-play-") as td:
        zipaes.extract(pie, td, password=password, names=["xml/database.xml"])
        dbpath = os.path.join(td, "xml", "database.xml")
        db = menudb.Database.load(dbpath)
        menudb.apply_weakness_map(db, {gid: weaknesses})
        db.save(dbpath)
        bm = BackupManager(pie)
        entry = bm.backup("pre-play-db")
        zipaes.update(pie, td, ["xml/database.xml"], password=password)
        print(f"[db] {pie} <- weaknesses ({entry.backup_file})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--game", default="mm1")
    p.add_argument("--seed", default="random", help="seed string or 'random'")
    p.add_argument("--game-dir", default=os.environ.get("MMLC_GAME", DEFAULT_GAME))
    p.add_argument("--password", default=zipaes.DEFAULT_PASSWORD)
    p.add_argument("--no-db", action="store_true", help="skip database.xml sync")
    p.add_argument("--no-visualizer", action="store_true")
    p.add_argument("--no-launch", action="store_true")
    p.add_argument("--appid", default="363440")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    key = randomizer.resolve(args.game)
    game_dir = os.path.abspath(args.game_dir)
    seed = random_seed() if args.seed == "random" else args.seed

    src = find_pristine(game_dir)
    prg = randomizer.load_prg_from(src, key)
    spoiler = randomizer.randomize(key, prg, seed,
                                   visualizer=not args.no_visualizer)

    print(f"[seed] {seed}  (game {key}, src {os.path.basename(src)})")
    for line in spoiler.lines()[1:]:
        print(line)

    out = os.path.join(game_dir, "roms", f"{key}.bin")
    spoiler_path = os.path.join(game_dir, "mmlc_spoiler.json")
    if args.dry_run:
        print(f"[dry-run] would write {out} and {spoiler_path}")
        return 0

    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_file_atomic(out, bytes(prg))
    print(f"[rom] {out} ({len(prg)} bytes)")

    with open(spoiler_path, "w", encoding="utf-8") as fh:
        json.dump({"game": key, "seed": seed,
                   "weaknesses": spoiler.weaknesses, "rewards": spoiler.rewards},
                  fh, indent=2)

    if not args.no_db:
        pie = os.path.join(game_dir, "data.pie")
        if os.path.exists(pie):
            sync_db(pie, randomizer.GAME_DB_ID[key], spoiler.weaknesses, args.password)
        else:
            print(f"[db] {pie} not found; skipped")

    if not args.no_launch:
        print(f"[launch] steam -applaunch {args.appid}")
        subprocess.Popen(["steam", "-applaunch", args.appid])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
