# MMLC1 Reverse-Engineering Facts (verified)

Target: Steam appid **363440**, installdir **Suzy**, main binary **Proteus.exe**.
All values below were re-verified against the local install on 2026-10-06 by
content matching (CRC / byte search), not copied blindly from prior notes.
Where the original mission brief disagreed with the binary, the **verified**
value is listed and the discrepancy noted.

## Binary

| Property | Value |
|---|---|
| Format | PE32 i386, MSVC RTTI, ImageBase `0x400000`, ASLR on |
| Packed size | 6 071 328 bytes |
| Unpacked size | 5 885 008 bytes |
| Protector | SteamStub v3.1 (x86), executable `.bind`, entry in `.bind` |
| Unpack | Steamless v3.1.0.5 (runs under Mono) |

Unpacked section map (used for file<->VA math):

| Section | VA | VS | RAW | RS |
|---|---|---|---|---|
| `.textU` | 0x00001000 | 0x0008e94b | 0x00000400 | 0x0008ea00 |
| `.rdata` | 0x00090000 | 0x004b15fc | 0x0008ee00 | 0x004b1600 |
| `.dataU` | 0x00542000 | 0x0008d558 | 0x00540400 | 0x00011800 |
| `.gfids` | 0x005d0000 | 0x00000054 | 0x000551c00 | 0x00000200 |
| `.tls`   | 0x005d1000 | 0x00000009 | 0x000551e00 | 0x00000200 |
| `.rsrcU` | 0x005d2000 | 0x00041f30 | 0x00055200 | 0x00042000 |
| `.reloc` | 0x00614000 | 0x000062f8 | 0x000594000 | 0x00006400 |

> **Brief correction:** the brief's `VA->file offset = VA - 0x401200` does not
> hold for this binary. The correct mapping is per-section above; for `.rdata`
> it is `file = VA - 0x401200 + 0xA00` (equivalently `VA = file + 0x401200`
> fails; the verified relation for `.rdata` is `VA = file + 0x1200`). All ROM
> addresses below are therefore given as **file offsets**, which is what the
> extractor and patcher use.

## ROM blobs (headerless PRG/CHR, in `.rdata`/`.data`)

File offsets and sizes confirmed by finding the exact extracted bytes in the exe.
US ROMs are sanitized MMLC dumps (Nintendo references removed); JP Rockman ROMs
are pristine originals.

| Game | PRG off | PRG size | CHR off | CHR size | iNES hdr |
|---|---|---|---|---|---|
| US MM1 | `0x2af2b0` | 0x20000 | — | — | `4e45531a 08002100 ...` |
| US MM2 | `0x08f170` | 0x40000 | — | — | `4e45531a 10001000 ...` |
| US MM3 | `0x0cf1b0` | 0x40000 | `0x10f1b0` | 0x20000 | `4e45531a 10104000 ...` |
| US MM4 | `0x12f1f0` | 0x80000 | — | — | `4e45531a 20004000 ...` |
| US MM5 | `0x1af230` | 0x40000 | `0x1ef230` | 0x40000 | `4e45531a 10204000 ...` |
| US MM6 | `0x22f270` | 0x80000 | — | — | `4e45531a 20004000 ...` |
| JP RK1 | `0x512648` | 0x20000 | — | — | `4e45531a 08002100 ...` |
| JP RK2 | `0x2f2508` | 0x40000 | — | — | `4e45531a 10001000 ...` |
| JP RK3 | `0x332548` | 0x40000 | `0x372548` | 0x20000 | `4e45531a 10104000 ...` |
| JP RK4 | `0x392588` | 0x80000 | — | — | `4e45531a 20004000 ...` |
| JP RK5 | `0x4125c8` | 0x40000 | `0x4525c8` | 0x40000 | `4e45531a 10204000 ...` |
| JP RK6 | `0x492608` | 0x80000 | — | — | `4e45531a 20004000 ...` |

Validation anchors:
- US MM2 PRG CRC32 = `0xcf5de2bc` (matches brief).
- JP RK2 PRG CRC32 over `0x40000` = `0x6150517c` (matches the brief exactly).
- JP RK1 PRG CRC32 = `0xd31dc910`.

> **JP offset correction (verified at runtime):** an earlier revision had the
> JP offsets **0x18 too low**. The per-game ctor source pointers prove the
> correct values, which are the brief's original offsets:
> `RK1 0x512648`, `RK2 0x2f2508`, `RK3 0x332548` (+CHR `0x372548`),
> `RK4 0x392588`, `RK5 0x4125c8` (+CHR `0x4525c8`), `RK6 0x492608`.
> The ctor sets `[this+0x810] = file_offset + 0x1200` for every game (US and
> JP), so `romtable.py` now uses these. The old JP CRCs (`dc8c6c00` etc.) were
> derived from the shifted bytes and are superseded.

## Overlay / 0xDF callee-stub writer

- There is exactly **one** occurrence of byte sequence `C6 04 08 DF`
  (`mov byte ptr [eax+ecx], 0xDF`) in the unpacked binary:
  - file offset `0x5b62a`, VA `0x5c22a`
  - function body: `mov ecx,[ecx+0x81C]; mov eax,[esp+4]; mov byte [eax+ecx],0xDF; ret 4`
- This proves the per-instance ROM buffer pointer lives at **byte `this+0x81C`**
  (int index `0x207`). Size fields: `this+0x814` (int `0x205`, PRG size),
  `this+0x818` (int `0x206`, CHR size).
- Disable by NOPing the 4-byte store -> `90 90 90 90` (keeps stack balance;
  the function then does nothing). Verified working: randomized MM1 boots.

> **Note:** the table above lists section *RVAs*, while the brief used
> *absolute* VAs. The store is at absolute VA `0x45c22a` inside the function
> that starts at `0x45c220` — so the brief's `FUN_0045c220` was correct. The
> ROM pointer is the int index `0x207` == byte offset `0x81C`.

## Per-game loader (MM1 ctor, from Ghidra of the matching binary shape)

```
param[0x204] = &ROM_SRC;                 // .rdata source blob
dst = malloc(0x20000);   param[0x207] = dst;
memcpy(dst, &ROM_SRC, 0x20000);          // PRG into heap ROM buffer
overlay(0x1525d); ... overlay(...);      // 5 x callee-ret patch sites
param[0x203] = malloc(0x2000);           // CHR-RAM (MM1)
param[0x205] = 0x20000;  param[0x206] = 0x2000;
```

Bank / mapper function-pointer tables are installed at int indices `0x218`
(8 entries) and `0x238` (16 entries) per game.

### Verified ctor layout (byte offsets)

The MM1 ctor is at absolute VA `0x403210` (finds `[edi]=0x6d04b4`, the MM1
vtable). It does, in order:

```
[edi+0x810] = 0x6b04b0            ; ROM source = .rdata PRG blob
eax = malloc(0x20000)             ; 0x489571
[edi+0x81C] = eax                 ; heap ROM buffer pointer
memcpy(eax, 0x6b04b0, 0x20000)    ; 0x48aa5e
overlay(0x1525d); overlay(0x152a6); overlay(0x1c259); overlay(0x1c918); overlay(0x1c05a)
eax = malloc(0x2000); [edi+0x80C] = eax   ; CHR (RAM)
[edi+0x814] = 0x20000; [edi+0x818] = 0x2000
```

So `this+0x810` = PRG **source** pointer, `this+0x81C` = heap copy,
`this+0x814` = PRG size, `this+0x818` = CHR size, `this+0x80C` = CHR buffer.

US ctor sites and their `[this+0x810]` source RVAs (immediate `C7 87 10 08 00 00
<imm>`): mm1 `0x40339b`→`0x2b04b0`, mm2 `0x40146d`→`0x90370`, mm3
`0x4019af`→`0xd03b0`, mm4 `0x401e5e`→`0x1303f0`, mm5 `0x4023ef`→`0x1b0430`,
mm6 `0x4028de`→`0x230470` (all == PRG file offset + `0x1200`). JP Rockman ctors
load their source differently (source ≈ file offset + `0x1200` + `0x18`) and are
not yet mapped; injection currently targets US mm1–mm6.

### Overlay hook point

`FUN_0045c220` body is `8B 89 1C 08 00 00 8B 44 24 04 [store] C2 04 00`
(`mov ecx,[ecx+0x81C]; mov eax,[esp+4]; ...; ret 4`). Signature
`8B 89 1C 08 00 00 8B 44 24 04` is unique. Because it is called once per patch
site right after the ROM memcpy, it is the ideal hook for **runtime ROM
injection**: replace the function with a 5-byte `E9` jmp to a stub that reads
`this` from ecx and copies a same-size ROM over both `[this+0x810]` and
`[this+0x81C]`.

### MM1 randomizer offsets (PRG-relative, validated)

The sanitized MMLC MM1 keeps the vanilla data tables, so the community
algorithm (avvie/MegamanRandomizer) applies directly:

| name | offset | meaning |
|---|---|---|
| damage table | `0x1FDEE` | 6 bosses × 8 bytes, weapon order `P C I B F E G M` |
| weakness/boss-defeated | `0x1BFCC` | 6 reward bytes |
| weapon reward | `0x1C148` | 6 reward bytes |
| Guts Man level-select fix | `0x1B69E` | 1 byte |

Vanilla boss0 chart = `[3,1,0,2,3,1,14,0]` (Cut Man, weak to Super Arm). Boss
order in the table equals the Database order `DB-RM1-NAME-25..30`
(Cut, Ice, Bomb, Fire, Elec, Guts). Reward bytes: C=`0x20`, I=`0x10`, B=`0x02`,
F=`0x40`, E=`0x04`, G=`0x08`. Super Arm's weakness (index 6, dmg 14) must be
placed on a boss with throwable blocks (bosses 0/4/5). Implemented in
`tools/mmlc/randomizer.py`; synced into `database.xml` `weakness_sprite`.

### In-engine randomizer + weakness visualizer

The proxy can randomize MM1 at load (`randomize=1` in `mmlc.ini`): it shuffles
the damage charts + rewards in `[this+0x810]`/`[this+0x81C]` before the game
runs, once per game per launch, seeded from `mmlc.ini` (`seed=random` picks a
fresh seed and logs it). The C algorithm (`src/proxy/mm1_rando.h`) and the
Python one (`tools/mmlc/randomizer.py`) share an xorshift32/FNV-1a RNG and are
byte-for-byte identical (verified by `test/mm1_rando_test.c`).

The **weakness visualizer** (`WEAKNESSVISUALIZER.ips`, avvie/MegamanRandomizer,
GPLv3) is embedded in the proxy. It adds stage-select code and reads a 6-byte
weakness table at PRG `0x1BFCC` (value per boss from
`{1:0x20,2:0x10,3:0x02,4:0x40,5:0x04,6:0x08}`). IPS offsets include the 16-byte
iNES header, so apply with `adjust = -0x10`. With the visualizer on, `0x1BFCC`
holds weaknesses (not rewards) and the Guts Man fix byte is left to the patch.

### Packed backup keeps `.rdata`

`Proteus.exe.orig.bak` (SteamStub-packed) stores `.rdata` **unencrypted at the
same file offsets** as the unpacked image, so all pristine ROM blobs (including
the un-randomized US MM1, CRC `0x1c47d202`) can be read straight from it with
`PE.read_at`.

## Engine / classes (MSVC RTTI)

- `bs::nes::NESSystem`, `bs::nes::MegaMan1..6`, `bs::nes::Rockman1..6`,
  `bs::MegaManGameBase`, `bs::MegaMan*Game`, `bs::Rockman*Game`,
  `bs::BakesaleGame`, `bs::Game`, `bs::nes::NESCallback`, `bs::nes::NSFPlayer`.
- `NESSystem` vtable @ absolute `0x6f2c9c` (11 slots); MM1 subclass vtable @
  `0x6d04b4`. Vtable pointers are absolute VAs; slot map below.

Vtable slot map (verified by reading both vtables and disassembling targets):

| slot | NESSystem base | MM1 subclass | meaning |
|---|---|---|---|
| 0 | 0x45a450 | 0x4034c0 | |
| 1 | 0x45be70 | 0x4034f0 | |
| 2 | 0x475270 | **0x4645e0** | `read6502` (thunk -> body `0x464640`) |
| 3 | 0x475270 | **0x4645a0** | 16-bit addr -> banked ROM offset |
| 4 | 0x447460 | 0x403500 | |
| 5 | **0x460750** | 0x460750 | run one frame of CPU cycles |
| 6 | 0x40f8c0 | 0x4646b0 | |
| 7 | 0x40f8c0 | 0x4646b0 | |
| 8 | 0x401750 | 0x401750 | |
| 9 | 0x401690 | 0x401690 | |
| 10 | 0x462480 | 0x464630 | |

Verified function bodies:

- `bank_xlate` @ abs `0x4645a0`: `mov dx,[esp+4]; mov eax,0xC000; cmp dx,ax;
  mov eax,7; jae +6; mov eax,[ecx+0x513E4]` then `(bank<<16)|addr`. `[this+0x513E4]`
  is the current PRG bank (0..7).
- `read6502` @ abs `0x4645e0` (thunk `jmp 0x464640`); body compares addr to
  `0xC000`/`0x8000` and calls `0x45e570`/`0x40e830` for mapper/bank handling.
  Fixed-bank read helper @ `0x4645f0` reads `[[this+0x810]+addr+0x10000]`.
- `advance` @ abs `0x460750`: loops `while ([this+0x50D78] < 0x7455)` calling
  vtable slot 5's target via `[vtbl+0x10]`; `0x7455` (29781) is CPU cycles/frame.

Runtime AOB signatures (position-independent; constants are struct offsets):

```
bank_xlate : 66 8B 54 24 04 B8 00 C0 00 00 66 3B D0 B8 07 00 00 00 73 06 8B 81 E4 13 05 00
read6502  : 8B 54 24 04 53 56 BB 00 C0 00 00 8B F1 57 66 3B D3
advance   : 56 8B F1 8B 8E 78 0D 05 00 81 F9 55 74 00 00 7D 1D
overlay   : C6 04 08 DF
```

The `steam_api.dll` proxy AOB-scans these at load (`scan=1`) and logs the
resolved VAs. Live run on the local install resolved (image base `0x400000`):

```
bank_xlate : VA 004645A0
read6502   : VA 00464640   (body; thunk is 0x4645E0)
advance    : VA 00460750
```

## Assets

- `data.pie`: ~250 MB WinZip AES-256 ZIP (`PK` magic). Password (public, also
  in the mission brief):
  `P091uWEdwe4lI6StDNMNlkodPGvJ38bL3HW6t3BCMYdFi83FXKu7k0NsHP8caDKS`.
  Contents: `sprites/` (1311), `savestates/` (452), `audio/`, `locale/`,
  `shaders/`, `pc_fonts/`, `xml/` (no ROMs). Entry names list without a
  password; contents need it.
- `xml/challenges.xml` (54 challenges; `N <section game= save_state= end_floor=
  end_room= x= y= [weapons=]>`), `xml/database.xml`, `xml/musicplayer.xml`.

## Museum / Database menus (data-driven)

The Museum and Database screens are defined entirely by XML + PNG + locale
tables inside `data.pie`; there is no separate menu binary format.

- `xml/museum_cc.xml` / `xml/museum_nx.xml` (PC/Switch variants):
  `<museum><game name sprite_folder><category name/><art name sprite/></game>`.
  `art/@name` is a locale key; `art/@sprite` is a PNG basename under
  `sprites/<sprite_folder>/`. cc has 684 `<art>`, nx 893; categories are
  `MU-CAT-PROD|FILE|CONCEPT|PROMO|BOSS|ANTIQUES`.
- `xml/database.xml`: 6 `<game>` (`id=1..6`, `sprite_folder=Database/RM1..RM6`),
  315 `<entry>` with `name`/`description` (locale keys), `sprite` (portrait
  PNG), `hp`, `ap`, `weakness_sprite` (weapon icon PNG basename), optional
  `save_state="Challenges/robot_masters/*.sav"`.
- Sprites: `sprites/Database/RMx/*.png`, `sprites/Museum/*`.

### Localization (`locale/`)

- `Locale.bin`: magic `LOCL`, u32 ver=1, u32 hash, u32 (filesize-16),
  u32 n_langs=8, u32 n_strings=2178, then 8 length-prefixed lang codes
  (`en fr it de es ja ru pt-br`), then 2178 u32 **Jenkins one-at-a-time**
  hashes of the string keys, in the same index order as the string tables.
- `Strings-<lang>.bin`: magic `TEXT`, u32 ver=1, u32 **cksum**, u32 (filesize-16),
  u32 n_strings=2178, u32 blob_size, then 2178 u32 offsets, then the blob.
- The `0x08` dword (both `LOCL` and `TEXT`) is **CRC-32/POSIX (cksum)** over
  bytes `0x10..EOF`: poly `0x04C11DB7`, non-reflected, init 0, the length
  appended little-endian, final xor `0xFFFFFFFF`. The game verifies it and
  rejects a table on mismatch (all keys then render as `UNKNOWN STRING`). The
  game's routine is at VA `0x4728b0`. Recompute on every edit — see
  `mmlc.locale.cksum`.
- Lookup: `oat(key)` -> find index in `Locale.bin` hash array -> string at that
  index in `Strings-<lang>.bin`. Verified: `oat("DB-RM1-NAME-01")` -> idx 30 ->
  `"Mega Man"`; `oat("MU-CAT-PROD")` -> idx 633 -> `"PRODUCTION ART"`;
  `oat("DB-RM1-DATA-01")` -> idx 0. Zero collisions across 2178 hashes.
- Loader: magic checked at `0x4660e0`; the `TEXT` reader at `0x46d7f2` reads
  count (`0x10`) and blob size (`0x14`), allocates `count*4`, reads the offset
  table. The `0x08` dword is **not** hashed/verified (only non-zero fields are
  required), so a modified `Strings-<lang>.bin` only needs a rebuilt offset
  table + blob; the `0x08` value can be preserved or left non-zero.
- To retitle a menu item: `oat(key)` -> index -> rewrite that string in every
  `Strings-<lang>.bin`, rebuild offsets + blob, repack `data.pie`.
- **Do not repack with 7z.** 7z rewrites entry metadata (host OS in "version
  made by" `0x003f`→`0x033f`, external attrs `0x2000`→Unix `0x81a48020`,
  timestamps) and the game's custom zip reader rejects the archive — the
  Database comes up empty and all locale strings render as `UNKNOWN STRING`.
  Also, `-mx1` produces "AES-256 Deflate" which the reader can't handle.
- `tools/mmlc/zipaes.py` is therefore a **native WinZip AES-256 (AE-2)**
  reader/writer: it decrypts entries (PBKDF2-HMAC-SHA1, 1000 iters, 66 bytes;
  AES-256-CTR with a little-endian counter starting at 1; HMAC-SHA1 auth) and
  rebuilds the archive preserving every metadata byte except the changed
  content and the shifted local-header offsets. All 1802 AES entries are
  "AES-256 Store"; the reader rejects deflated AES entries.
- Entry layout: `salt(16) | pw_verify(2) | ciphertext | hmac(10)`.
- Save path (Proton): `.../compatdata/363440/pfx/drive_c/users/steamuser/AppData/Roaming/MegaMan/<id>/`.

## Steam API

- `Proteus.exe` imports `steam_api.dll` (plus KERNEL32, MSVCP140, d3d11,
  CONCRT140, VERSION, XINPUT1_3, VCRUNTIME140, api-ms-win-crt-*, USER32,
  SHELL32, ole32).
- `steam_api.dll` in the install exposes **859 named exports** (mostly
  forwarders). A proxy DLL can re-export these to a renamed
  `steam_api_orig.dll` via a generated `.def`.

## Mega Man sprite palette (offline PRG patch)

The player-sprite palette is the vanilla light/dark blue pair, stored as 4-byte
NES-palette entries `[light, dark, 0F, 0F]` (MM1 uses code immediates instead).
Offsets are **PRG** (headerless) unless noted. Verified 2026-10-09 by reading the
extracted `roms/*.nes`.

| Game | Mega Man sprite palette | Notes |
|---|---|---|
| MM1 / RK1 | operands at the `MM1_PAL_OFFSETS` list + `MM1_PAL_BOSSROOM` | `a9 2C` / `a9 11` immediates |
| MM2 / RK2 | entry @ PRG `0x3D30C` (`2C 11 0F 0F`) | slot 2 of the 9-entry table @ `0x3D304` |
| MM3 / RK3 | entry @ PRG `0x4642` (`2C 11 0F 0F`) | immediately before `MM3_WEAPONPAL` (`0x4646`) |
| MM4 / RK4 | entry @ PRG `0x73618` (`2C 11 0F 0F`) | **candidate**; other copies exist (`0x7363C`, `0x73B7F`, `0x368A7`, `0x36C4D`, `0x36C71`, `0x515A6`) — confirm in-game |
| MM5 / RK5 | entry #1 @ PRG `0x253C` (`2C 11 0F 0F`) | second of the 21-entry table @ `0x2538` |
| MM6 / RK6 | **not located** | palettes are compressed (no plain `2C 11 0F 0F`); `2C 11` sits inside packed data (`0x51227`, `0x6F227`, …) |

JP parity: RK1–RK5 share the *same* palette-data offsets as their US counterparts
(the `2C 11 0F 0F` tables are byte-identical in location for MM1/RK1, MM2/RK2,
MM3/RK3, MM5/RK5). So a US-targeted palette patch covers the JP ROMs for those
five; only MM4's copies shift slightly and MM6 is unresolved.

## MM6 damage table (randomizer) — status: not located

The standalone `Mega Man 6 (USA).nes` is byte-identical to the MMLC-extracted
`mm6.nes` except **18 bytes** around PRG `0x7968C–0x7969F` (a sanitized text
region), so any offset found in one applies to the other.

RAM facts (Data Crystal): boss HP `$03ED`, current weapon `$0699`, and weapon
energy `$0688`..`$0691` in the order **buster, Yamato, Wind, Blizzard, Fire,
Plant, Knight, Silver, Centaur, Beat** (indices 0–9).

Code found by AOB search in the flat PRG:
- boss HP init @ `0x7623F`: `$03ED = $03EE + $03EF + 1`.
- boss-defeat/health-bar routine @ `0x7EF29` (sets `$03ED=0xFF`, fills to `0x1B`).
- weapon-index reads (`LDA $0699`) @ `0x7058F`, `0x705F2` (`+0x80` then table).

Data-mining did **not** find the damage table as a contiguous boss-major or
weapon-major matrix: every boss order permutation was tried for the 8×8 chart
(contiguous per-weapon rows, per-boss rows, and 8-record blocks) with no match.
So MM6 likely stores per-weapon damage tables indexed by object id (MM4-style),
or computes damage in code. Next step: disassemble the projectile→boss hit
handler around the `$03ED` references (`0x622EA`, `0x62F99`, `0x6382C`,
`0x63851`, `0x63C22`) with a 6502 disassembler / Ghidra+Bisqwit-style disassembly.
