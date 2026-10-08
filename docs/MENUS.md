# Museum / Database menu replacement

MMLC's Museum and Database screens are **data-driven**: no menu binary format
exists. Everything lives in `data.pie` (a WinZip AES-256 zip) as XML + PNG +
locale tables. This document covers the `mmlc` commands that read and edit
those files.

> Patch-only: all commands act on *your* install and never bundle or ship game
> assets. Mutating commands snapshot the target into `.mmlc-backup/` first.

## Data model

| file | schema |
|---|---|
| `xml/database.xml` | `<database><game id sprite_folder><entry name description sprite hp ap weakness_sprite save_state/></game>` — 6 games, 315 entries |
| `xml/museum_cc.xml` | PC Museum: `<game sprite_folder><category name/><art name sprite/></game>` |
| `xml/museum_nx.xml` | Switch Museum (same schema, more art) |
| `sprites/Database/RMx/*.png` | portraits (`entry/@sprite`) and weapon icons (`@weakness_sprite`) |
| `sprites/Museum/*` | museum art (`art/@sprite`) |
| `locale/Locale.bin` + `locale/Strings-<lang>.bin` | localized text |

`entry/@name`, `entry/@description`, `art/@name`, and `category/@name` are
**locale keys**, not literal text.

## Localization

`Locale.bin` stores one 32-bit **Jenkins one-at-a-time** hash per string key,
index-aligned with every `Strings-<lang>.bin`. Resolve a key by hashing it,
finding the index, and reading that index from a language table:

```
key --oat--> index in Locale.bin --index--> Strings-<lang>.bin[index]
```

Example: `oat("DB-RM1-NAME-25")` → index 54 → `"Cut Man"`.

## Workflow

```bash
# 1. extract the archive once (xml/, locale/, sprites/, ...)
mmlc pie-extract /path/Suzy/data.pie work/

# 2. edit
mmlc strings-set work/ DB-RM1-NAME-25 "CUTMAN-X" --apply      # all languages
mmlc db-set work/ --game 1 --entry DB-RM1-NAME-25 --hp 42 --weakness firestorm --apply
mmlc db-randomize-weakness work/ --seed my-seed --apply
mmlc museum-list work/ --game "Mega Man"                       # inspect art

# 3. push the changed files back into data.pie
mmlc pie-update /path/Suzy/data.pie work/ xml/database.xml \
    locale/Strings-en.bin locale/Strings-ja.bin --apply

# or rebuild the whole archive (slower)
mmlc pie-repack /path/Suzy/data.pie work/ --apply
```

The password defaults to the public Steam constant; override with
`--password`, `$MMLC_PIE_PASSWORD`, or `pie_password` in `mmlc.tool.ini`
(see `config/mmlc.tool.ini.example`).

## Commands

| command | purpose |
|---|---|
| `pie-extract PIE DIR [--names ...]` | extract archive (all or selected members) |
| `pie-update PIE DIR FILE... [--apply]` | replace edited files inside the archive |
| `pie-repack PIE DIR [--apply]` | rebuild the archive from `DIR` |
| `strings-get DIR KEY [--lang]` | resolve a locale key |
| `strings-set DIR KEY VALUE [--lang all] [--apply]` | replace localized text |
| `db-list DIR [--game] [--json]` | list database entries |
| `db-set DIR --game G --entry E [--hp --ap --weakness --sprite --desc-key --name-key --save-state] [--apply]` | edit one entry |
| `db-randomize-weakness DIR --seed S [--game G] [--apply]` | deterministic weakness shuffle |
| `db-apply-weakness DIR --map FILE [--apply]` | apply an explicit `{game:{entry:weapon}}` JSON |
| `museum-list DIR [--file] [--game]` | list museum games / art |

All mutating commands are dry-run unless `--apply` is given.

## Weakness sync and the randomizer

`db-randomize-weakness` permutes the existing `weakness_sprite` values among a
game's entries (deterministic per `seed`+game). It is a **stand-in**: to make
the in-game Database agree with a ROM randomizer you must feed the randomizer's
own mapping:

```bash
mmlc db-apply-weakness work/ --map spoiler.json --apply
```

`spoiler.json`:

```json
{ "1": { "DB-RM1-NAME-25": "firestorm", "DB-RM1-NAME-26": "iceslasher" } }
```

If the randomizer does not emit a spoiler, the Database and the ROM will
disagree — treat `--seed` as cosmetic only.

## Risk vs. ease

```
 risk (to install / save data)
  ↑
10│
 9│
 8│                                   ○ ROM write
 7│                          ○ NOP-overlay (binary patch)
 6│            ● weakness-sync
 5│
 4│                ● pie pack/unpack
 3│
 2│                    ● db-editor     ○ runtime proxy
 1│        ● strings-editor
 0└────┬─────┬─────┬─────┬─────┬─────┬─────┬────→ ease for user
      0     1     2     3     4     5     6     7
```

| feature | ease | risk | notes |
|---|---|---|---|
| runtime proxy | 9 | 2 | in-memory, no files changed |
| database editor | 7 | 2 | XML only, validated, reversible |
| strings editor | 6 | 2 | text only, reversible |
| pie pack/unpack | 6 | 4 | 250 MB rewrite; keep the `.mmlc-backup` snapshot |
| weakness-sync | 4 | 6 | must match the ROM randomizer or it lies |
| NOP-overlay | 6 | 7 | binary patch; restore from backup |
| ROM write | 5 | 8 | same-size only; can desync a save |
