#!/usr/bin/env python3
"""Command-line front end for the MMLC1 patch-only toolkit.

Examples:
  mmlc_cli.py info    Suzy/Proteus.exe
  mmlc_cli.py extract Suzy/Proteus.exe mm1 out/mm1.nes
  mmlc_cli.py nop-overlay Suzy/Proteus.exe --apply
  mmlc_cli.py write-rom   Suzy/Proteus.exe mm1 out/mm1.nes --apply
  mmlc_cli.py restore Suzy/Proteus.exe

  mmlc_cli.py pie-extract Suzy/data.pie work
  mmlc_cli.py strings-set work DB-RM1-NAME-25 CUTMAN --apply
  mmlc_cli.py db-list     work --game 1
  mmlc_cli.py pie-update  Suzy/data.pie work xml/database.xml --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmlc import (
    __version__,
    canary,
    config,
    locale as locale_mod,
    menudb,
    patcher,
    randomizer,
    romtable,
    steamless,
    zipaes,
)
from mmlc.backup import BackupManager
from mmlc.pe import PE, write_file_atomic


def _open(path: str) -> PE:
    return PE(path)


def cmd_info(args: argparse.Namespace) -> int:
    pe = _open(args.exe)
    print(pe.notes())
    print()
    print("ROM anchors:")
    for line in canary(pe, require_pristine=False):
        print("  " + line)
    print()
    data = pe.data
    hits = patcher.find_overlay_store(data)
    print(f"overlay 0xDF store sites: {[hex(h) for h in hits] or 'none (already NOPed)'}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    pe = _open(args.exe)
    games = [romtable.resolve(args.game)] if args.game.lower() != "all" else list(romtable.GAMES.values())
    os.makedirs(args.outdir, exist_ok=True)
    for g in games:
        out = os.path.join(args.outdir, f"{g.key}.nes")
        write_file_atomic(out, patcher.extract_ines(pe, g))
        print(f"wrote {out} ({os.path.getsize(out)} bytes)")
    return 0


def cmd_nop_overlay(args: argparse.Namespace) -> int:
    pe = _open(args.exe)
    hits = patcher.find_overlay_store(pe.data)
    if not hits:
        print("overlay already NOPed; nothing to do")
        return 0
    print(f"found overlay store at {[hex(h) for h in hits]}")
    if not args.apply:
        print("dry run; pass --apply to write")
        return 0
    backup = BackupManager(args.exe)
    entry = backup.backup("pre-nop-overlay")
    print(f"backed up -> {entry.backup_file} ({entry.sha256[:12]})")
    data = bytearray(pe.data)
    n = patcher.nop_overlay(data)
    write_file_atomic(args.exe, bytes(data))
    print(f"NOPed {n} site(s); wrote {args.exe}")
    return 0


def cmd_write_rom(args: argparse.Namespace) -> int:
    pe = _open(args.exe)
    g = romtable.resolve(args.game)
    with open(args.rom, "rb") as fh:
        blob = fh.read()
    if blob[:4] == romtable.NES_MAGIC:
        blob = blob[16:]
    want = patcher.body_size(g)
    if len(blob) != want:
        print(
            f"error: {g.key} body is {len(blob):#x}, expected {want:#x}",
            file=sys.stderr,
        )
        return 2
    if not args.apply:
        print(f"dry run: would write {len(blob):#x} bytes to {g.key} @ {g.prg.offset:#x}")
        return 0
    backup = BackupManager(args.exe)
    entry = backup.backup(f"pre-write-{g.key}")
    print(f"backed up -> {entry.backup_file} ({entry.sha256[:12]})")
    data = bytearray(pe.data)
    patcher.write_game(data, g, blob)
    write_file_atomic(args.exe, bytes(data))
    print(f"wrote {g.key} ROM; new PRG crc {patcher.crc32(patcher.read_prg(PE(args.exe), g)):#010x}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    bm = BackupManager(args.exe)
    e = bm.backup(args.label or "manual")
    print(f"backup: {e.backup_file} sha256={e.sha256} size={e.size}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    bm = BackupManager(args.exe)
    print("available backups:")
    for e in bm.entries():
        print(f"  {e.label:24} {e.backup_file} {e.sha256[:12]}")
    if not args.label:
        print("(restoring latest; use --label to pick)")
    name = bm.restore(args.label)
    print(f"restored {args.exe} from {name}")
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    out = steamless.unpack(args.exe, args.steamless, mono=args.mono, out=args.out)
    print(f"unpacked -> {out}")
    return 0


# -- data.pie / menu commands -------------------------------------------------

def _password(args: argparse.Namespace) -> str:
    return (
        getattr(args, "password", None)
        or os.environ.get("MMLC_PIE_PASSWORD")
        or config.get("pie_password")
        or zipaes.DEFAULT_PASSWORD
    )


def _need_dir(path: str) -> str:
    if not os.path.isdir(os.path.join(path, "xml")) and not os.path.isdir(
        os.path.join(path, "locale")
    ):
        raise SystemExit(f"error: {path} does not look like an extracted data.pie (no xml/ or locale/)")
    return path


def _backup_and_write(target: str, data: bytes) -> None:
    bm = BackupManager(target)
    entry = bm.backup("pre-menu-edit")
    print(f"backed up -> {entry.backup_file} ({entry.sha256[:12]})")
    write_file_atomic(target, data)


def cmd_pie_extract(args: argparse.Namespace) -> int:
    zipaes.extract(args.pie, args.dir, password=_password(args), names=args.names,
                   sevenz=args.sevenz)
    print(f"extracted {args.pie} -> {args.dir}")
    return 0


def cmd_pie_update(args: argparse.Namespace) -> int:
    if not args.apply:
        print(f"dry run: would update {len(args.files)} file(s) in {args.pie}")
        for f in args.files:
            print("  " + f)
        return 0
    bm = BackupManager(args.pie)
    entry = bm.backup("pre-pie-update")
    print(f"backed up -> {entry.backup_file} ({entry.sha256[:12]})")
    zipaes.update(args.pie, args.dir, args.files, password=_password(args),
                  sevenz=args.sevenz)
    print(f"updated {args.pie}")
    return 0


def cmd_pie_repack(args: argparse.Namespace) -> int:
    if not args.apply:
        print(f"dry run: would rebuild {args.pie} from {args.dir}")
        return 0
    bm = BackupManager(args.pie)
    entry = bm.backup("pre-pie-repack")
    print(f"backed up -> {entry.backup_file} ({entry.sha256[:12]})")
    zipaes.repack(args.pie, args.dir, password=_password(args), sevenz=args.sevenz)
    print(f"rebuilt {args.pie}")
    return 0


def _locale_paths(d: str):
    loc = os.path.join(d, "locale", "Locale.bin")
    if not os.path.exists(loc):
        raise SystemExit(f"error: {loc} not found (run pie-extract first)")
    return loc


def cmd_strings_get(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    table = locale_mod.parse_locale(open(_locale_paths(d), "rb").read())
    idx = table.index_of(args.key)
    if idx is None:
        raise SystemExit(f"error: key {args.key!r} not found in Locale.bin")
    langs = table.langs if args.lang == "all" else [args.lang]
    print(f"key {args.key!r} -> index {idx}")
    for lang in langs:
        p = os.path.join(d, "locale", f"Strings-{lang}.bin")
        s = locale_mod.parse_strings(open(p, "rb").read(), lang)
        print(f"  [{lang}] {s.get(idx)!r}")
    return 0


def cmd_strings_set(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    table = locale_mod.parse_locale(open(_locale_paths(d), "rb").read())
    idx = table.index_of(args.key)
    if idx is None:
        raise SystemExit(f"error: key {args.key!r} not found in Locale.bin")
    langs = table.langs if args.lang == "all" else [args.lang]
    print(f"key {args.key!r} -> index {idx}; languages: {', '.join(langs)}")
    for lang in langs:
        p = os.path.join(d, "locale", f"Strings-{lang}.bin")
        s = locale_mod.parse_strings(open(p, "rb").read(), lang)
        old = s.get(idx)
        s.set(idx, args.value)
        print(f"  [{lang}] {old!r} -> {args.value!r}")
        if args.apply:
            _backup_and_write(p, s.to_bytes())
    if not args.apply:
        print("dry run; pass --apply to write")
    return 0


def cmd_strings_set_idx(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    table = locale_mod.parse_locale(open(_locale_paths(d), "rb").read())
    langs = table.langs if args.lang == "all" else [args.lang]
    for idx in args.index:
        for lang in langs:
            p = os.path.join(d, "locale", f"Strings-{lang}.bin")
            s = locale_mod.parse_strings(open(p, "rb").read(), lang)
            old = s.get(idx)
            s.set(idx, args.value)
            print(f"  [{lang}] idx {idx}: {old!r} -> {args.value!r}")
            if args.apply:
                _backup_and_write(p, s.to_bytes())
    if not args.apply:
        print("dry run; pass --apply to write")
    return 0


def cmd_db_list(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    db = menudb.Database.load(os.path.join(d, "xml", "database.xml"))
    games = db.games if args.game is None else [db.game(args.game)]
    for g in games:
        print(f"game {g.id} {g.name!r} ({g.sprite_folder})")
        for i, e in enumerate(g.entries):
            if args.json:
                import json as _json
                print("  " + _json.dumps({"index": i, **e.as_dict()}))
            else:
                print(f"  [{i:3}] {e.name:20} hp={e.hp or '-':>3} "
                      f"ap={e.ap or '-':>3} weak={e.weakness_sprite or '-'}")
    return 0


def cmd_db_set(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    path = os.path.join(d, "xml", "database.xml")
    db = menudb.Database.load(path)
    e = db.entry(args.game, args.entry)
    fields = {
        "hp": args.hp,
        "ap": args.ap,
        "weakness_sprite": args.weakness,
        "description": args.desc_key,
        "sprite": args.sprite,
        "name": args.name_key,
        "save_state": args.save_state,
    }
    for k, v in fields.items():
        if v is not None:
            print(f"  {e.name}.{k}: {e.el.get(k)!r} -> {v!r}")
    if not args.apply:
        print("dry run; pass --apply to write")
        return 0
    e.set(**fields)
    _backup_and_write(path, db.to_bytes())
    print(f"updated {path}")
    return 0


def cmd_db_randomize_weakness(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    path = os.path.join(d, "xml", "database.xml")
    db = menudb.Database.load(path)
    games = args.game or None
    changes = menudb.randomize_weakness(db, args.seed, games)
    for c in changes:
        print("  " + c)
    print(f"{len(changes)} change(s)")
    if not args.apply:
        print("dry run; pass --apply to write")
        return 0
    _backup_and_write(path, db.to_bytes())
    print(f"updated {path}")
    return 0


def cmd_db_apply_weakness(args: argparse.Namespace) -> int:
    import json as _json
    d = _need_dir(args.dir)
    path = os.path.join(d, "xml", "database.xml")
    db = menudb.Database.load(path)
    with open(args.map, "r", encoding="utf-8") as fh:
        mapping = _json.load(fh)
    changes = menudb.apply_weakness_map(db, mapping)
    for c in changes:
        print("  " + c)
    if not args.apply:
        print("dry run; pass --apply to write")
        return 0
    _backup_and_write(path, db.to_bytes())
    print(f"updated {path}")
    return 0


def cmd_museum_list(args: argparse.Namespace) -> int:
    d = _need_dir(args.dir)
    path = os.path.join(d, "xml", args.file)
    mus = menudb.Museum.load(path)
    if args.game is None:
        for g in mus.games:
            arts = mus.art(g.name)
            print(f"{g.name!r} ({g.sprite_folder}): {len(arts)} art")
        return 0
    for a in mus.art(args.game):
        print(f"  {a.name:20} -> {a.sprite}")
    return 0


def cmd_randomize(args: argparse.Namespace) -> int:
    import json as _json
    key = randomizer.resolve(args.game)
    prg = randomizer.load_prg_from(args.src, key)
    spoiler = randomizer.randomize(
        key, prg, args.seed,
        weakness=not args.no_weakness,
        weapons=not args.no_weapons,
        visualizer=args.visualizer,
        palette=not args.no_palette,
    )
    for line in spoiler.lines():
        print(line)

    if args.spoiler:
        with open(args.spoiler, "w", encoding="utf-8") as fh:
            _json.dump(
                {"game": key, "seed": args.seed,
                 "weaknesses": spoiler.weaknesses, "rewards": spoiler.rewards},
                fh, indent=2,
            )
        print(f"spoiler -> {args.spoiler}")

    if args.database:
        db = menudb.Database.load(args.database)
        gid = randomizer.GAME_DB_ID[key]
        changes = menudb.apply_weakness_map(db, {gid: spoiler.weaknesses})
        for c in changes:
            print("  db " + c)
        if args.apply:
            _backup_and_write(args.database, db.to_bytes())
            print(f"updated {args.database}")
        else:
            print("db dry run; pass --apply to write")

    if args.pie:
        import tempfile
        gid = randomizer.GAME_DB_ID[key]
        pw = _password(args)
        with tempfile.TemporaryDirectory(prefix="mmlc-db-") as td:
            zipaes.extract(args.pie, td, password=pw, names=["xml/database.xml"],
                           sevenz=args.sevenz)
            dbpath = os.path.join(td, "xml", "database.xml")
            db = menudb.Database.load(dbpath)
            changes = menudb.apply_weakness_map(db, {gid: spoiler.weaknesses})
            for c in changes:
                print("  pie db " + c)
            if args.apply:
                db.save(dbpath)
                bm = BackupManager(args.pie)
                e = bm.backup("pre-randomize-db")
                print(f"backed up -> {e.backup_file} ({e.sha256[:12]})")
                zipaes.update(args.pie, td, ["xml/database.xml"], password=pw,
                              sevenz=args.sevenz)
                print(f"updated {args.pie} (database.xml)")
            else:
                print("pie db dry run; pass --apply to write")

    if args.out:
        if not args.apply:
            print(f"dry run: would write {args.out} ({len(prg):#x} bytes)")
        else:
            write_file_atomic(args.out, bytes(prg))
            print(f"wrote {args.out} ({len(prg)} bytes, crc {patcher.crc32(bytes(prg)):#010x})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mmlc", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_exe(sp):
        sp.add_argument("exe", help="path to Proteus.exe")

    sp = sub.add_parser("info", help="dump PE/anchors/overlay state")
    add_exe(sp); sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("extract", help="extract ROM(s) as iNES")
    add_exe(sp)
    sp.add_argument("game", help="game key (mm1..mm6, rk1..rk6) or 'all'")
    sp.add_argument("outdir", nargs="?", default="out")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("nop-overlay", help="disable the 0xDF callee-ret writer")
    add_exe(sp)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_nop_overlay)

    sp = sub.add_parser("write-rom", help="overwrite a game ROM (same size)")
    add_exe(sp)
    sp.add_argument("game")
    sp.add_argument("rom")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_write_rom)

    sp = sub.add_parser("backup", help="snapshot the exe")
    add_exe(sp)
    sp.add_argument("--label")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("restore", help="restore a snapshot")
    add_exe(sp)
    sp.add_argument("--label")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("unpack", help="unpack SteamStub with Steamless")
    add_exe(sp)
    sp.add_argument("--steamless", help="path to Steamless.CLI.exe")
    sp.add_argument("--mono", default="mono")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_unpack)

    def add_pie_common(sp):
        sp.add_argument("--password", help="data.pie password (default: config/public)")
        sp.add_argument("--sevenz", help="path to 7z executable")

    sp = sub.add_parser("pie-extract", help="extract data.pie into a work dir")
    sp.add_argument("pie")
    sp.add_argument("dir")
    sp.add_argument("--names", nargs="*", help="only these archive members")
    add_pie_common(sp)
    sp.set_defaults(func=cmd_pie_extract)

    sp = sub.add_parser("pie-update", help="replace files inside data.pie")
    sp.add_argument("pie")
    sp.add_argument("dir", help="work dir containing the edited files")
    sp.add_argument("files", nargs="+", help="paths relative to dir")
    sp.add_argument("--apply", action="store_true")
    add_pie_common(sp)
    sp.set_defaults(func=cmd_pie_update)

    sp = sub.add_parser("pie-repack", help="rebuild data.pie from a work dir")
    sp.add_argument("pie")
    sp.add_argument("dir")
    sp.add_argument("--apply", action="store_true")
    add_pie_common(sp)
    sp.set_defaults(func=cmd_pie_repack)

    sp = sub.add_parser("strings-get", help="resolve a locale key")
    sp.add_argument("dir")
    sp.add_argument("key")
    sp.add_argument("--lang", default="all")
    sp.set_defaults(func=cmd_strings_get)

    sp = sub.add_parser("strings-set", help="replace a locale string by key")
    sp.add_argument("dir")
    sp.add_argument("key")
    sp.add_argument("value")
    sp.add_argument("--lang", default="all")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_strings_set)

    sp = sub.add_parser("strings-set-idx", help="replace locale strings by index")
    sp.add_argument("dir")
    sp.add_argument("index", type=int, nargs="+")
    sp.add_argument("value")
    sp.add_argument("--lang", default="all")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_strings_set_idx)

    sp = sub.add_parser("db-list", help="list database.xml entries")
    sp.add_argument("dir")
    sp.add_argument("--game")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_db_list)

    sp = sub.add_parser("db-set", help="edit a database entry")
    sp.add_argument("dir")
    sp.add_argument("--game", required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--hp")
    sp.add_argument("--ap")
    sp.add_argument("--weakness", help="weakness_sprite weapon basename")
    sp.add_argument("--desc-key", help="description locale key")
    sp.add_argument("--name-key", help="name locale key")
    sp.add_argument("--sprite")
    sp.add_argument("--save-state")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_db_set)

    sp = sub.add_parser("db-randomize-weakness", help="shuffle database weaknesses")
    sp.add_argument("dir")
    sp.add_argument("--seed", required=True)
    sp.add_argument("--game", action="append", help="limit to game id (repeatable)")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_db_randomize_weakness)

    sp = sub.add_parser("db-apply-weakness", help="apply a spoiler JSON mapping")
    sp.add_argument("dir")
    sp.add_argument("--map", required=True)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_db_apply_weakness)

    sp = sub.add_parser("museum-list", help="list museum games/categories/art")
    sp.add_argument("dir")
    sp.add_argument("--file", default="museum_cc.xml")
    sp.add_argument("--game")
    sp.set_defaults(func=cmd_museum_list)

    sp = sub.add_parser("randomize", help="seeded ROM randomizer (+ db sync)")
    sp.add_argument("game")
    sp.add_argument("--seed", required=True)
    sp.add_argument("--src", required=True,
                    help="pristine Proteus.exe or Proteus.exe.orig.bak")
    sp.add_argument("--out", help="write headerless randomized PRG here")
    sp.add_argument("--database", help="database.xml to sync weaknesses into")
    sp.add_argument("--pie", help="data.pie: extract+sync+update database.xml")
    sp.add_argument("--spoiler", help="write JSON spoiler here")
    sp.add_argument("--no-weakness", action="store_true")
    sp.add_argument("--no-weapons", action="store_true")
    sp.add_argument("--no-palette", action="store_true",
                    help="do not randomise Mega Man's palette from the seed")
    sp.add_argument("--visualizer", action="store_true",
                    help="apply the stage-select weakness visualizer patch")
    sp.add_argument("--password", help="data.pie password")
    sp.add_argument("--sevenz", help="path to 7z executable")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_randomize)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
