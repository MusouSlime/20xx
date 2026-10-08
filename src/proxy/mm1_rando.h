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
    memcpy(prg + MM1_DAMAGE_TABLE, charts, sizeof(charts));

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
