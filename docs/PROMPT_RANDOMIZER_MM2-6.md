# Prompt for a new chat — implement the MM2–MM6 randomizers for 20XX

Copy the block below into a fresh chat.

---

**Project: 20XX — randomizer for Mega Man 2–6**

Repo: https://github.com/MusouSlime/20xx — local checkout: `/var/home/user/Projects/mmlc-mod`.
20XX is a **patch-only** Mesen2 launcher/mod host for Mega Man Legacy Collection 1
(Steam appid 363440, install `Suzy`, binary `Proteus.exe`). It bundles no ROMs,
game data, or emulator cores.

**Read first:** `docs/HANDOFF.md` (live state + build commands), `docs/RE_FACTS.md`
(static ROM/offset facts), `docs/MESEN.md`, `docs/PLAN.md`.

**Current state (working):**
- MM1's offline randomizer is done and verified byte-for-byte:
  `tools/mmlc/randomizer.py` (`GAMES = {"mm1": …}`, `randomize_mm1`: weakness
  shuffle, weapon-reward shuffle, palette shuffle, weakness visualizer) and
  20XX's registry `RANDO_GAMES = {"mm1"}` in `tools/20xx/20xx.py` (CLI + GUI).
- The MMLC **US** ROMs are sanitized but keep the vanilla data tables, so the
  community algorithm (avvie/MegamanRandomizer, GPLv3) ports directly (that's
  how MM1 was done).

**Goal:** implement the randomizer for **MM2, MM3, MM4, MM5, MM6** (US first;
JP Rockman 2–6 where the tables match), keeping it deterministic per seed and
reproducible offline, wired into both the CLI (`20xx patch/play GAME`) and the
GUI (`tools/20xx/20xx.py`, `GUI_MODES`/`RANDO_GAMES`).

**What each game needs:**
1. Locate the PRG data tables in the user's own ROM (extract with `20xx extract`,
   PRG = `nes[16:16+prg_size]`): boss damage/weakness chart, boss-defeated/reward
   table, weapon-reward table, palette offsets, and any get/pickup fix bytes.
   ROM blob file offsets + `.rdata` layout are in `tools/mmlc/romtable.py` and
   `docs/RE_FACTS.md`.
2. Port the community shuffle rules per game (avvie/MegamanRandomizer): the
   weakness permutation, weapon-reward shuffle (with the "no self-weakness"
   rule), and palette shuffle. MM2+ differ (8 bosses, MMC1/MMC3 mappers,
   e-tanks/items, etc.).
3. Add a `randomize_mmN(prg, seed, …)` to `tools/mmlc/randomizer.py` and register
   it in `GAMES`; add the key to `RANDO_GAMES` in `20xx`.
4. Add parity tests (Python reference vs the C header, like
   `test/mm1_rando_test.c` / `test/test_randomizer.py`) and CRC/byte anchors.

**How to work:**
- Extract: `20xx extract` (writes `roms/*.nes`). Source is
  `Proteus.exe.orig.bak` (pristine `.rdata`).
- Existing template: `MM1_DAMAGE_TABLE=0x1FDEE`, `MM1_WEAPON_REWARD=0x1C148`,
  `MM1_WEAKNESS_TABLE=0x1BFCC`, palette tables in `tools/mmlc/randomizer.py`.
- Validate: `python3 -m unittest discover -s test -p "test_*.py"`.
- Don't break MM1. Update `README.md`/`docs/` as you go.
- Patch-only: never write the exe/ROM blobs into the repo; operate on the user's
  install. MIT for mod code; Mesen2 is GPLv3 (user-built via `20xx build-core`).

**Useful commands:**
```
20xx extract                 # roms/*.nes from your Proteus.exe
20xx patch mm2 --seed hello  # offline randomizer (once implemented)
20xx gui                     # launcher (Setup, Options, ROM hacks, Controls)
20xx build-core              # clone+patch+build the Mesen2 core
STEAM_API=.../Suzy/steam_api_orig.dll bash build/build_proxy.sh
```

**Definition of done:** MM2–6 randomize offline, seeds reproduce byte-for-byte
between the Python tool and the in-proxy path (where applicable), tests pass,
and the CLI/GUI expose the new games.

---
