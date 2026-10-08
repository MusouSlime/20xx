# Mission brief (recovered)

Turn Mega Man Legacy Collection 1 (Steam appid 363440, install folder `Suzy`,
main binary `Proteus.exe`) into a **moddable host** that can run the six NES
games plus added NES-format content, with in-engine randomizers/shufflers, an
Archipelago client, and custom menus.

## Target / environment (verified)

- `Proteus.exe`: PE32 i386, ~6.07 MB, MSVC RTTI, opcode image base `0x400000`,
  ASLR on. SteamStub v3.1 (x86); unpack with Steamless v3.1.0.5 under Mono.
- Debug-dir leak: `C:\workspace\rockman-classic-collection\Projects\Proteus\out\Win32\SteamRelease\bin\Proteus\Proteus.pdb`.
- `data.pie`: ~250 MB WinZip AES-256 ZIP; password
  `P091uWEdwe4lI6StDNMNlkodPGvJ38bL3HW6t3BCMYdFi83FXKu7k0NsHP8caDKS`.
  Contents: `sprites/ savestates/Challenges/ audio/ locale/ shaders/ xml/` (no ROMs).

## Engine facts (Ghidra on the unpacked exe)

- Custom NES emulator, Digital Eclipse "Eclipse" engine. RTTI:
  `bs::nes::NESSystem`, `bs::nes::MegaMan1..6` / `Rockman1..6`,
  `bs::MegaManGameBase`, `bs::MegaMan*Game` / `bs::Rockman*Game`,
  `bs::BakesaleGame`, `bs::Game`, `bs::nes::NESCallback`, `bs::nes::NSFPlayer`.
- `NESSystem` vtable `0x6f2c9c` (11 slots); MM1 subclass `0x6d04b4`.
  `read6502 @ 0x4645e0`, bank/mapper translate `@ 0x4645a0`, cycle advance
  `@ 0x460750` (all verified — see `RE_FACTS.md`).
- Per-game ctor: `memcpy(heap_buf, &ROM_blob, size)`, then mapper tables at
  `this+0x218` / `this+0x238`. ROM buffer pointer at `this+0x81C` (int `0x207`),
  sizes `this+0x814` (PRG) / `this+0x818` (CHR).
- Runtime overlay `FUN_0045c220`: `*(byte*)(rom+off) = 0xDF` (callee `ret 4`),
  called ~5–15× per game right after the ROM copy. Disable by NOP-ing the
  store `C6 04 08 DF`.
- ROMs are headerless PRG/CHR blobs in `.rdata`; file offsets verified in
  `RE_FACTS.md` (brief's `VA→file = VA - 0x401200` is wrong; use per-section math).
- **Proven pipeline:** Steamless unpack → NOP overlay → overwrite the `.rdata`
  ROM blob with a same-size randomized ROM → launch via Steam/Proton; boots and
  MM1 is randomized.
- Menus/content are data-driven via `ObjectDataManager<...>`: `xml/challenges.xml`,
  `xml/database.xml`, `xml/musicplayer.xml`, `locale/Strings-*.bin`. Main menu is
  engine-coded; CHALLENGES / Music / Database lists are data.

## Objectives (ranked)

1. In-engine randomizer/shuffler framework for MM1–6 (per-launch seed; patch the
   in-memory ROM at load). Proxy `steam_api.dll` (forward exports + DllMain
   hooks) vs direct exe patching.
2. Archipelago: run AP patched ROMs in-engine + a client reading/writing NES RAM
   via `read6502`/`write6502`, speaking the AP protocol. AP core supports MM2 &
   MM3; community MM1/4/5/6.
3. Custom menus: extend `challenges.xml` + sprites + locale + data.pie repack for
   "RANDOMIZER"/"AP"; assess a new top-level menu (code-driven).
4. Optional: better NES core (Mesen 1 x86 in-process) behind the 11-slot vtable.
5. Optional: added NES-format games via the generic emulator.

## Constraints

- **Patch-only distribution**: never bundle the unpacked exe or any Capcom
  ROM/asset; operate on the user's own install. DMCA 1201 / Steam ToS caveats.
- Third-party cores (Mesen GPLv3 / FCEUX GPLv2) stay user-supplied/runtime-loaded;
  mod code permissively licensed.
- `data.pie` edits require repacking WinZip AES-256 (`7z -mem=AES256`).
- Everything reversible (backups, restore).
