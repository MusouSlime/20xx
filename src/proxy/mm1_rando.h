/*
 * mm1_rando.h -- in-engine MM1 randomizer, shared by the proxy and the
 * native test. Ported from tools/mmlc/randomizer.py; the RNG (xorshift32
 * seeded by FNV-1a) is identical on both sides so offline spoilers match the
 * in-engine result for the same seed.
 *
 * The includer must define MM1_LOG(fmt, ...) before including this file.
 */
#ifndef MM1_RANDO_H
#define MM1_RANDO_H

#include <string.h>
#include <stddef.h>

#ifndef MM1_LOG
#define MM1_LOG(...) ((void)0)
#endif

#define MM1_PRG_SIZE       0x20000UL
#define MM1_DAMAGE_TABLE   0x1FDEEUL
#define MM1_WEAKNESS_TABLE 0x1BFCCUL
#define MM1_WEAPON_REWARD  0x1C148UL
#define MM1_GUTSMAN_FIX    0x1B69EUL
#define MM1_WILY_ROWS_BASE (MM1_DAMAGE_TABLE + 6UL * 8UL)  /* rows 6-10 */

/* Green top-buster marker: the visualizer's injected stage-select routine
 * (Y = boss index 0-5) writes one portrait palette byte per boss via
 * STA $2007 at 0x1FF2B. Trampoline that store through free zero space so
 * the boss taking the most Buster damage renders NES light green ($2A).
 * Mirrors apply_green_top_buster() in tools/mmlc/randomizer.py. */
#define MM1_VIS_ROUTINE_STA 0x1FF2BUL
#define MM1_GREEN_CODE      0x1FF35UL
#define MM1_GREEN_BOSS      0x1FF43UL
#define MM1_GREEN_COLOR     0x2A

/* Mega Man sprite/weapon palette (ported from avvie's PaletteGenerator). */
#define MM1_PAL_BOSSROOM_HI 0x1C29BUL
#define MM1_PAL_BOSSROOM_LO 0x1C2A0UL
#define MM1_WEAPON_PAL      0x1D487UL

static const unsigned long MM1_PAL_OFFSETS[16] = {
    0x0CB1UL, 0x0CE1UL, 0x0DDDUL, 0x4CB1UL, 0x4CE1UL, 0x4D9BUL,
    0x8CB1UL, 0x8CE1UL, 0xCCB1UL, 0xCCE1UL, 0x10CB1UL, 0x10CE1UL,
    0x14CB1UL, 0x1D485UL, 0x1D493UL, 0x14CE1UL,
};
static const unsigned char MM1_PRIMARY_PAL[16] = {
    0x2C, 0x11, 0x30, 0x00, 0x30, 0x12, 0x30, 0x19,
    0x28, 0x16, 0x38, 0x00, 0x30, 0x17, 0x2C, 0x11,
};

/* weakness value per weapon index (P C I B F E G M); P/M have none */
static const unsigned char MM1_WEAK_VALUE[8] = {
    0x00, 0x20, 0x10, 0x02, 0x40, 0x04, 0x08, 0x00
};
static const char *const MM1_WEAPON_SPRITE[8] = {
    "rockbuster", "rollingcutter", "iceslasher", "hyperbomb",
    "firestorm", "thunderbeam", "superarm", "magnetbeam"
};
static const char *const MM1_BOSS_ENTRY[6] = {
    "DB-RM1-NAME-25", "DB-RM1-NAME-26", "DB-RM1-NAME-27",
    "DB-RM1-NAME-28", "DB-RM1-NAME-29", "DB-RM1-NAME-30"
};
static const int MM1_THROWABLE[3] = { 0, 4, 5 };

/* WEAKNESSVISUALIZER.ips (avvie/MegamanRandomizer, GPLv3) -- see
 * tools/mmlc/patches/. Offsets include the 16-byte iNES header, so the
 * applier uses adjust = -0x10 for our headerless PRG. */
static const unsigned char MM1_WV_IPS[] = {
    0x50, 0x41, 0x54, 0x43, 0x48, 0x01, 0xb5, 0xc3, 0x00, 0x26, 0x06, 0xa9,
    0x00, 0x85, 0xae, 0xf0, 0x0c, 0xa5, 0x5d, 0x3d, 0xcc, 0xbf, 0x85, 0xae,
    0xa5, 0x5d, 0x3d, 0x48, 0xc1, 0x85, 0x0e, 0xa0, 0x00, 0xa5, 0x05, 0x8d,
    0x06, 0x20, 0xa5, 0x04, 0x8d, 0x06, 0x20, 0xa2, 0x06, 0x4c, 0x00, 0xff,
    0x01, 0xb5, 0xe9, 0x00, 0x00, 0x00, 0x1e, 0xea, 0x01, 0xb6, 0x17, 0x00,
    0x01, 0xc2, 0x01, 0xb6, 0xad, 0x00, 0x03, 0xc9, 0xfe, 0xd0, 0x01, 0xb6,
    0xe7, 0x00, 0x01, 0x05, 0x01, 0xbd, 0xc4, 0x00, 0x06, 0x16, 0x2a, 0x0f,
    0x0f, 0x16, 0x2a, 0x01, 0xbd, 0xf4, 0x00, 0x01, 0x2a, 0x01, 0xbf, 0xdc,
    0x00, 0x06, 0x08, 0x04, 0x40, 0x10, 0x20, 0x02, 0x01, 0xff, 0x10, 0x00,
    0x35, 0xa5, 0x0c, 0xc9, 0x06, 0xf0, 0x0c, 0xb9, 0x1b, 0xbe, 0xd0, 0x0a,
    0xd0, 0x05, 0xb9, 0x3f, 0xbe, 0xd0, 0x03, 0xb9, 0x63, 0xbe, 0xc9, 0x21,
    0xd0, 0x12, 0xa5, 0x0e, 0xf0, 0x04, 0xa9, 0x00, 0xf0, 0x0a, 0xa5, 0xae,
    0xd0, 0x04, 0xa9, 0x21, 0x10, 0x02, 0xa9, 0x7c, 0x8d, 0x07, 0x20, 0xc8,
    0xca, 0xd0, 0xce, 0x4c, 0xf7, 0xb5, 0x45, 0x4f, 0x46,
};

static unsigned int g_rng;

static unsigned int mm1_fnv1a(const char *s)
{
    unsigned int h = 2166136261u;
    while (*s) {
        h ^= (unsigned char)*s++;
        h *= 16777619u;
    }
    return h;
}

static unsigned int mm1_rng_next(void)
{
    unsigned int x = g_rng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    g_rng = x ? x : 0x1234567u;
    return g_rng;
}

static unsigned int mm1_rng_below(unsigned int n)
{
    return n ? mm1_rng_next() % n : 0;
}

/* One randomised (light, dark) palette pair, matching tools/mmlc/randomizer.py
 * and avvie's PaletteGenerator. */
static void mm1_palette_pair(unsigned char c1, unsigned char c2,
                             unsigned char *hi, unsigned char *lo)
{
    int off = (int)c1 - (int)c2;
    int rh = 1 + (int)mm1_rng_below(2);
    int rl = (int)mm1_rng_below(13);
    int ph = rh * 16 + rl;
    int pl = ph - off;
    if (pl < 0)
        pl = (int)(unsigned char)(pl ^ 0xC8);  /* == pl ^ -56, low byte */
    if (pl == ph)
        ph -= 16;
    *hi = (unsigned char)(ph & 0xFF);
    *lo = (unsigned char)(pl & 0xFF);
}

static void mm1_shuffle_palette(unsigned char *prg)
{
    unsigned char hi, lo;
    mm1_palette_pair(0x2C, 0x11, &hi, &lo);
    prg[MM1_PAL_BOSSROOM_HI] = hi;
    prg[MM1_PAL_BOSSROOM_LO] = lo;
    for (int i = 0; i < 16; i++) {
        prg[MM1_PAL_OFFSETS[i]] = hi;
        prg[MM1_PAL_OFFSETS[i] + 1] = lo;
    }
    for (int x = 2; x < 15; x += 2) {
        mm1_palette_pair(MM1_PRIMARY_PAL[x], MM1_PRIMARY_PAL[x + 1], &hi, &lo);
        prg[MM1_WEAPON_PAL + (x - 2)] = hi;
        prg[MM1_WEAPON_PAL + (x - 2) + 1] = lo;
    }
}

static int mm1_apply_ips(unsigned char *buf, size_t bufsize,
                         const unsigned char *ips, size_t ipslen, long adjust)
{
    if (ipslen < 8 || memcmp(ips, "PATCH", 5) != 0)
        return -1;
    size_t p = 5;
    while (p + 3 <= ipslen) {
        if (memcmp(ips + p, "EOF", 3) == 0)
            break;
        long off = (ips[p] << 16) | (ips[p + 1] << 8) | ips[p + 2];
        p += 3;
        if (p + 2 > ipslen)
            break;
        int size = (ips[p] << 8) | ips[p + 1];
        p += 2;
        off += adjust;
        if (size) {
            if (off < 0 || (size_t)off + size > bufsize || p + (size_t)size > ipslen)
                return -2;
            memcpy(buf + off, ips + p, size);
            p += size;
        } else {
            if (p + 3 > ipslen)
                break;
            int rle = (ips[p] << 8) | ips[p + 1];
            int val = ips[p + 2];
            p += 3;
            if (off < 0 || (size_t)off + rle > bufsize)
                return -2;
            memset(buf + off, val, rle);
        }
    }
    return 0;
}

/* Mark the highest-buster-damage boss green on the stage select.
 * `charts` are the (possibly shuffled) damage charts; the boss with the
 * highest column-0 (Buster) value wins (first wins ties). Requires the
 * visualizer IPS first; returns the winning boss index, or -1 when the
 * routine is absent. Mirrors apply_green_top_buster() in
 * tools/mmlc/randomizer.py for byte parity. */
static int mm1_apply_green(unsigned char *prg, unsigned char charts[6][8])
{
    static const unsigned char code[13] = {
        0xCC, 0x43, 0xFF,   /* CPY GreenBoss */
        0xD0, 0x02,         /* BNE +2 -> STA (passthrough for other bosses) */
        0xA9, MM1_GREEN_COLOR, /* LDA #green */
        0x8D, 0x07, 0x20,   /* STA $2007 (both paths converge here) */
        0x4C, 0x2E, 0xFF,   /* JMP back to INY */
    };
    int top = 0, b;
    for (b = 1; b < 6; b++)
        if (charts[b][0] > charts[top][0])
            top = b;
    if (prg[MM1_VIS_ROUTINE_STA] != 0x8D ||
        prg[MM1_VIS_ROUTINE_STA + 1] != 0x07 ||
        prg[MM1_VIS_ROUTINE_STA + 2] != 0x20)
        return -1;
    prg[MM1_VIS_ROUTINE_STA] = 0x4C;
    prg[MM1_VIS_ROUTINE_STA + 1] = 0x35;
    prg[MM1_VIS_ROUTINE_STA + 2] = 0xFF;
    memcpy(prg + MM1_GREEN_CODE, code, sizeof(code));
    prg[MM1_GREEN_CODE + 13] = 0x00;
    prg[MM1_GREEN_BOSS] = (unsigned char)top;
    return top;
}

/* Cursor-driven stage-select tint hook (mirrors apply_cursor_hook() in
 * tools/mmlc/randomizer.py for byte parity).
 *
 * Disassembly ground truth (mm1dasm bank6): CurrentStage ($31) is both the
 * stage-select cursor and the stage index (0-5 = Cut/Ice/Bomb/Fire/Elec/
 * Guts, damage-table order). The cursor highlight is painted by F1BB02,
 * called with A=$55 (on) or A=$00 (off); it ends with STA $1C; RTS
 * (bytes 85 1C 60 at PRG 0x1BB47). The hook replaces that tail with
 * JSR $FF44. On $55 calls it moves the selected face's 9 OAM sprites to
 * sprite palette slot 1 (faces at OAM entries [2,13,23,33,45]+9), recolors
 * slot 1 ($03E5-7) with the weakness colors (solid $2A when the selected
 * boss is the $FF43 top-buster boss), mirrors the triple to BG slot 3
 * ($03DD-DF) for Guts (BG-only face), then requests an NMI palette flush
 * ($3A/$3B=$03D0 image, $34=$20, $37=1). $00 calls are skipped so the
 * tint stays solid while the highlight border blinks. */
#define MM1_CURSOR_VAR      0x31UL
#define MM1_HOOK_JSR_SITE   0x1BB47UL
#define MM1_HOOK_CODE       0x1FF44UL   /* 156 zero bytes (CPU $FF44) */
#define MM1_HOOK_CODE_LEN   156
#define MM1_HOOK_DATA       0x1FCC7UL   /* 59 zero bytes (CPU $FCC7) */
#define MM1_HOOK_DATA_LEN   59
#define MM1_STAGESEL_PAL    0x1BDABUL   /* L1BDAB: 32-byte select palette */
#define MM1_HOOK_GREEN      0x2A

static const unsigned char MM1_HOOK_FACE_BASES[5] = { 0x0A, 0x36, 0x5E, 0x86, 0xB6 };
static const unsigned char MM1_HOOK_FACE_SLOTS[5] = { 2, 0, 3, 2, 3 };

/* Assembled by _cursor_hook_code() in tools/mmlc/randomizer.py; the Python
 * test asserts byte parity with the assembler output and the 156B limit.
 * NOTE: the on/off gate reads $032D (set by F1BB02 to the entry A earlier
 * in the call), because A at the tail is always $86/$8A (clobbered by the
 * all-defeated branch), never $55/$00. */
static const unsigned char MM1_HOOK_CODE_BYTES[] = {
    0x85, 0x1C, 0xAD, 0x2D, 0x03, 0xC9, 0x55, 0xF0, 0x03, 0x4C, 0xD6, 0xFF,
    0xA6, 0x31, 0xE0, 0x06, 0x90, 0x03, 0x4C, 0xD6, 0xFF, 0xA9, 0x02, 0x85,
    0x0D, 0xA2, 0x04, 0xBD, 0xF3, 0xFC, 0x85, 0x0C, 0xBD, 0xF8, 0xFC, 0xE4,
    0x31, 0xD0, 0x02, 0xA9, 0x01, 0x85, 0x0E, 0xA0, 0x24, 0x88, 0x88, 0x88,
    0x88, 0xB1, 0x0C, 0x29, 0xFC, 0x05, 0x0E, 0x91, 0x0C, 0x98, 0xD0, 0xF1,
    0xCA, 0x10, 0xDC, 0xA2, 0x1F, 0xBD, 0xC7, 0xFC, 0x9D, 0xD0, 0x03, 0xCA,
    0x10, 0xF7, 0xA6, 0x31, 0xEC, 0x43, 0xFF, 0xF0, 0x15, 0x8A, 0x0A, 0xAA,
    0xBD, 0xE7, 0xFC, 0x8D, 0xE5, 0x03, 0x8D, 0xE7, 0x03, 0xBD, 0xE8, 0xFC,
    0x8D, 0xE6, 0x03, 0x4C, 0xB5, 0xFF, 0xA9, 0x2A, 0x8D, 0xE5, 0x03, 0x8D,
    0xE6, 0x03, 0x8D, 0xE7, 0x03, 0xA5, 0x31, 0xC9, 0x05, 0xD0, 0x0B, 0xA2,
    0x02, 0xBD, 0xE5, 0x03, 0x9D, 0xDD, 0x03, 0xCA, 0x10, 0xF7, 0xA9, 0xD0,
    0x85, 0x3A, 0xA9, 0x03, 0x85, 0x3B, 0xA9, 0x20, 0x85, 0x34, 0xA9, 0x01,
    0x85, 0x37, 0x60,
};

/* Robot-master signature colors (light, dark), shared with
 * tools/mmlc/wily.py ROBOT_MASTER_COLORS. */
static const unsigned char MM1_RM_COLORS[6][2] = {
    { 0x16, 0x06 },   /* Cut Man */
    { 0x21, 0x11 },   /* Ice Man */
    { 0x2A, 0x1A },   /* Bomb Man */
    { 0x16, 0x06 },   /* Fire Man */
    { 0x30, 0x20 },   /* Elec Man */
    { 0x27, 0x17 },   /* Guts Man */
};

/* weapon index -> robot master index (P/Buster and Magnet Beam have none) */
static const signed char MM1_WEAPON_TO_RM[8] = { -1, 0, 1, 2, 3, 4, 5, -1 };

/* Per-boss (light, dark) weakness pairs (12 bytes): damage-table argmax
 * weapon's robot-master colors, Buster/Magnet falls back to own colors. */
static void mm1_hook_weakness_colors(unsigned char charts[6][8],
                                     unsigned char pairs[12])
{
    int b;
    for (b = 0; b < 6; b++) {
        int w = 0, k;
        for (k = 1; k < 8; k++)
            if (charts[b][k] > charts[b][w])
                w = k;
        int rm = (w >= 0 && w < 8 && MM1_WEAPON_TO_RM[w] >= 0)
            ? MM1_WEAPON_TO_RM[w] : b;
        pairs[b * 2] = MM1_RM_COLORS[rm][0];
        pairs[b * 2 + 1] = MM1_RM_COLORS[rm][1];
    }
}

/* Install the cursor hook (fresh install). Requires the visualizer first
 * (reads the $FF43 top-buster index). Returns 0 on success, -1 on a
 * dirty/missing site.
 *
 * NOTE: offline builds can also install the hook *without* the visualizer
 * (`cursor_tint` in tools/mmlc/randomizer.py / 20xx `--cursor-tint`): then
 * the $FF43 index is recorded directly and the base image is the vanilla
 * L1BDAB. That combo has no C entry point; C installs with the visualizer.
 */
static int mm1_apply_cursor_hook(unsigned char *prg, unsigned char charts[6][8])
{
    unsigned char pairs[12];
    size_t i;
    if (prg[MM1_HOOK_JSR_SITE] != 0x85 ||
        prg[MM1_HOOK_JSR_SITE + 1] != 0x1C ||
        prg[MM1_HOOK_JSR_SITE + 2] != 0x60)
        return -1;
    for (i = 0; i < MM1_HOOK_CODE_LEN; i++)
        if (prg[MM1_HOOK_CODE + i])
            return -1;
    for (i = 0; i < MM1_HOOK_DATA_LEN; i++)
        if (prg[MM1_HOOK_DATA + i])
            return -1;
    prg[MM1_HOOK_JSR_SITE] = 0x4C;
    prg[MM1_HOOK_JSR_SITE + 1] = 0x44;
    prg[MM1_HOOK_JSR_SITE + 2] = 0xFF;
    memcpy(prg + MM1_HOOK_CODE, MM1_HOOK_CODE_BYTES,
           sizeof(MM1_HOOK_CODE_BYTES));
    memcpy(prg + MM1_HOOK_DATA, prg + MM1_STAGESEL_PAL, 32);
    mm1_hook_weakness_colors(charts, pairs);
    memcpy(prg + MM1_HOOK_DATA + 32, pairs, 12);
    memcpy(prg + MM1_HOOK_DATA + 44, MM1_HOOK_FACE_BASES, 5);
    memcpy(prg + MM1_HOOK_DATA + 49, MM1_HOOK_FACE_SLOTS, 5);
    return 0;
}

static const char *mm1_reward_sprite(unsigned char r)
{
    switch (r) {
    case 0x20: return "rollingcutter";
    case 0x10: return "iceslasher";
    case 0x02: return "hyperbomb";
    case 0x40: return "firestorm";
    case 0x04: return "thunderbeam";
    case 0x08: return "superarm";
    }
    return "?";
}

/* Randomize an MM1 PRG in place. Returns 0 on success. */
static int randomize_mm1(unsigned char *prg, const char *seed, int visualizer,
                         int weapons, int palette)
{
    unsigned char charts[6][8];
    memcpy(charts, prg + MM1_DAMAGE_TABLE, sizeof(charts));

    g_rng = mm1_fnv1a(seed);
    if (!g_rng)
        g_rng = 1;

    for (int i = 5; i > 0; i--) {
        int j = (int)mm1_rng_below((unsigned)i + 1);
        for (int k = 0; k < 8; k++) {
            unsigned char t = charts[i][k];
            charts[i][k] = charts[j][k];
            charts[j][k] = t;
        }
    }
    for (int i = 0; i < 6; i++) {
        if (charts[i][6] == 14) {
            int j = MM1_THROWABLE[mm1_rng_below(3)];
            for (int k = 0; k < 8; k++) {
                unsigned char t = charts[i][k];
                charts[i][k] = charts[j][k];
                charts[j][k] = t;
            }
            break;
        }
    }
    /* Buster standard: the Guts-weak boss keeps buster 3, others drop to 1
     * (so the green top-buster marker always points at the beginner boss). */
    {
        int guts = -1;
        for (int b = 0; b < 6; b++)
            if (charts[b][6] == 14) { guts = b; break; }
        if (guts < 0)
            for (int b = 0; b < 6; b++) {
                int mi = 0;
                for (int k = 1; k < 8; k++)
                    if (charts[b][k] > charts[b][mi]) mi = k;
                if (mi == 6) { guts = b; break; }
            }
        for (int b = 0; b < 6; b++)
            charts[b][0] = (b == guts) ? 3 : 1;
    }
    memcpy(prg + MM1_DAMAGE_TABLE, charts, sizeof(charts));

    /* Shuffle the Wily rows among themselves (the all-zero CWU-01P row is
     * left in place). Mirrors _shuffle_wily() in tools/mmlc/randomizer.py. */
    {
        static const int widx[4] = { 0, 1, 3, 4 };
        unsigned char wrows[4][8];
        int order[4] = { 0, 1, 2, 3 };
        for (int i = 0; i < 4; i++)
            memcpy(wrows[i], prg + MM1_WILY_ROWS_BASE + widx[i] * 8, 8);
        for (int i = 3; i > 0; i--) {
            int j = (int)mm1_rng_below((unsigned)i + 1);
            int t = order[i]; order[i] = order[j]; order[j] = t;
        }
        for (int d = 0; d < 4; d++)
            memcpy(prg + MM1_WILY_ROWS_BASE + widx[d] * 8, wrows[order[d]], 8);
    }

    int mx[6];
    for (int b = 0; b < 6; b++) {
        int mi = 0;
        for (int k = 1; k < 8; k++)
            if (charts[b][k] > charts[b][mi])
                mi = k;
        mx[b] = mi;
    }

    if (visualizer) {
        mm1_apply_ips(prg, MM1_PRG_SIZE, MM1_WV_IPS, sizeof(MM1_WV_IPS), -0x10);
        for (int b = 0; b < 6; b++)
            prg[MM1_WEAKNESS_TABLE + b] = MM1_WEAK_VALUE[mx[b]];
        mm1_apply_green(prg, charts);
        mm1_apply_cursor_hook(prg, charts);
    }

    unsigned char table[6] = { 0x20, 0x10, 0x02, 0x40, 0x04, 0x08 };
    if (weapons) {
        unsigned char pool[6] = { 0x20, 0x10, 0x02, 0x40, 0x04, 0x08 };
        int pooln = 6;
        int tn = 0;
        for (int b = 0; b < 6; b++) {
            unsigned char rewardlist[6];
            int rn = 0;
            for (int i = 0; i < pooln; i++)
                rewardlist[rn++] = pool[i];
            int excl = MM1_WEAK_VALUE[mx[b]];
            if (excl) {
                for (int i = 0; i < rn; i++)
                    if (rewardlist[i] == (unsigned char)excl) {
                        for (int j = i; j < rn - 1; j++)
                            rewardlist[j] = rewardlist[j + 1];
                        rn--;
                        break;
                    }
            }
            if (rn == 0) {
                unsigned char prev = table[4];
                table[4] = (unsigned char)excl;
                table[tn++] = prev;
            } else {
                int r = (int)mm1_rng_below((unsigned)rn);
                unsigned char reward = rewardlist[r];
                table[tn++] = reward;
                for (int i = 0; i < pooln; i++)
                    if (pool[i] == reward) {
                        for (int j = i; j < pooln - 1; j++)
                            pool[j] = pool[j + 1];
                        pooln--;
                        break;
                    }
            }
        }
    }
    for (int i = 0; i < 6; i++)
        prg[MM1_WEAPON_REWARD + i] = table[i];
    if (!visualizer) {
        for (int i = 0; i < 6; i++)
            prg[MM1_WEAKNESS_TABLE + i] = table[i];
        prg[MM1_GUTSMAN_FIX] = table[5];
    }
    if (palette)
        mm1_shuffle_palette(prg);

    MM1_LOG("randomize mm1 seed=%s visualizer=%d weapons=%d palette=%d",
            seed, visualizer, weapons, palette);
    for (int b = 0; b < 6; b++)
        MM1_LOG("  %s weakness=%s reward=%s", MM1_BOSS_ENTRY[b],
                MM1_WEAPON_SPRITE[mx[b]], mm1_reward_sprite(table[b]));
    return 0;
}

#endif /* MM1_RANDO_H */
