# Mega Man Legacy Collection 1 — Moddable-Host Execution Plan

> Recovered from the prior planning session (`ses_ef069f08…`) and reconciled
> with the verified facts in `RE_FACTS.md`. All addresses are static against
> ImageBase `0x400000`; at runtime resolve `base = GetModuleHandleW(L"Proteus.exe")`
> and add.

## Current status (this repo)

| Phase | State | Notes |
|---|---|---|
| 0 Environment/safety | **done** | Steamless wrapper, PE reader, backup/restore, canary CRCs, tests |
| 1 Runtime injection | **done (MVP)** | proxy forwards 859 exports, NOPs overlay, AOB-scans virtuals, and hooks `FUN_0045c220` to inject a same-size ROM over `[this+0x810]`+`[this+0x81C]` |
| 2 Asset/data.pie | **partial** | `data.pie` extract/update/repack + locale/db editors done; **open**: loose-file override vs pack |
| 3 Randomizer | **MM1, MM2, MM3, MM4, MM5 offline** | 20xx patches offline (`--seed`). MM1 full; MM2/MM3/MM5 weakness + reward + palette (MM2/MM3/MM5 weaknesses and MM5 weapon-get are byte-for-byte upstream ports); MM4 full (own shuffles). MM6 pending |
| 4 Custom menus | **partial** | Museum/Database/locale editing done; challenge entries pending |
| 5 Archipelago | not started | |
| 6 Mesen core | deferred | |
| 7 Added games | not started | |
| 8 Distribution | partial | backup/restore + canary done |

## 0. Locked decisions

| Decision | Choice | Why |
|---|---|---|
| Injection method | **Proxy `steam_api.dll`** + inline hooks; static exe patch only as fallback | loader resolves imports before `.bind`; no exe rewrite; reversible; ASLR-safe via `GetModuleHandle` |
| Randomization delivery | **Runtime in-memory patcher** (primary); external pre-randomized ROM injection as a mode | avoids re-encrypting `data.pie` per seed; per-launch seeds |
| AP client location | **In-engine** in the proxy, via `read6502`/`write6502` | distributable, no external host |
| Better NES core | **Defer**; IPC Mesen2 (x64) if needed | avoids GPLv3 linking; Mesen2 is x64-only |
| Menu strategy | **Data-driven first**; new top-level menu = stretch | main menu is engine-coded |
| Distribution | **Patch-only**, no exe/ROM/Capcom asset shipped | DMCA 1201 / Steam ToS |
| License | mod code MIT; cores runtime-loaded/user-supplied | limit GPL conflict |

## Phase 1 — Runtime injection framework (core enabler)

- Proxy: original → `steam_api_orig.dll`; new `steam_api.dll` forwards all exports + `DllMain`.
- Base: `GetModuleHandleW(L"Proteus.exe")` (never hardcode `0x400000`).
- Overlay killer: NOP store `C6 04 08 DF` (done) or hook `FUN_0045c220`.
- Locate per-game ctors via RTTI → vtable → ctor xrefs; hook ctor, call
  original, then patch the heap ROM buffer at `this+0x81C` before first instruction.
- Sibling `write6502` sits adjacent to `read6502`.
- **Acceptance:** proxy forwards exports; game boots; overlay disabled; a test
  hook can read+mutate one ROM byte and observe effect; disabling restores stock.

## Phase 2 — Asset access & data.pie strategy

- Experiment 1: drop loose `xml/` next to the exe / in the save dir; hook file
  I/O; see if `ObjectDataManager` prefers disk over `data.pie`. **Bias: loose
  overrides** (tool ships only its own files).
- Else: repack with `7z -mem=AES256` (implemented) and verify boot + challenges.
- Record savestate layout `savestates/Challenges/...`.

## Phase 3 — In-engine randomizer framework

- Normalized `(PRG, CHR)` model; keep total size identical so `this+0x205` stays valid.
- Reuse community logic: `htv04/mmlc-dac-extractor` IPS patches (US→original) and
  `avvie/MegamanRandomizer` shuffle rules; port to a seed-driven runtime patcher.
- Order: **MM1** (0x20000, no CHR split) → **MM2** (AP-core) → MM3–6.
- Application point: ctor hook, post-original-call, pre-first-instruction.
- **Acceptance:** MM1/MM2 boot randomized; same seed ⇒ identical ROM hash;
  stock unaffected when disabled; US and JP covered (post-patch CRC).

## Phase 4 — Custom menus & content

- Track A (recommended): extend `challenges.xml` + `sprites/Global/Challenges/*`
  + locale strings (engine `TEXT` table) → "launch randomized/AP run" entries.
- Track B (stretch): new top-level main-menu item (code-patch; gated behind A).
- Savestate strategy: test `save_state=`-less entries; else author minimal states;
  prefer clean start states for RNG-sensitive modes.

## Phase 5 — Archipelago

- ROM injection mode: accept a same-size AP-patched ROM, overwrite the in-memory
  blob before first instruction; validate size/CRC.
- AP client: websocket/text protocol in the proxy; worker thread.
- RAM bridge via `read6502`/`write6502`; pace off cycle advance.
- **Acceptance:** MM2 connects, one item in, one location out, reconnect resumes.

## Phase 6 — Optional Mesen core (defer)

- In-process Mesen 1 (x86) fills the 11-slot vtable (GPLv3, 32-bit core) vs IPC
  Mesen2 (x64, cleaner license, latency/second window). Recommend IPC Mesen2 first.

## Phase 7 — Optional added NES games

- Characterize game registration (subclass vtable + ctor + blob); generic
  descriptor for a user-supplied blob; mapper/CHR banking + overlay contract.

## Phase 8 — Distribution & hardening

- Patch tool = unpacker + proxy DLL + config + restore; never ships exe/ROM/assets.
- Installer verifies install (canary), backs up, installs, one-click revert.
- Version-pinning keyed on canary CRCs.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Randomizer byte collides with overlay `0xDF` | corrupt bank switch | overlay disabled; collision scan; per-game boot tests |
| Savestate/rewind desync | broken challenges/AP | clean start states; inject before load |
| `data.pie` codec mismatch | engine rejects archive | prefer loose override; `-mem=AES256`; verify boot |
| ASLR / wrong base | crash | always `GetModuleHandleW` |
| GPL contamination | can't distribute | MIT mod code; runtime-loaded cores |
| SteamStub/update drift | offsets invalid | canary CRCs; fail closed; re-derive |
| US-ROM sanitization breaks randomizer | bad patches | reconstruct via `mmlc-dac-extractor`; CRC-check |
| Proxy vs Steam DRM | DLL won't load | verify on packed exe; fallback: post-Steamless |

## Milestones

- **M1 (MVP):** patch-only proxy; overlay disabled; **per-launch seeded in-memory
  randomization for MM1 & MM2**; config-triggered; CRC/backup/restore.
- **M2:** data-driven menu entries + savestate strategy + MM3–6.
- **M3:** Archipelago in-engine (MM2/MM3 first) + RAM bridge + reconnect.
- **M4:** distribution hardening, revert, canary versioning.
- **M5 (gated):** Mesen core (IPC) or added NES games.
