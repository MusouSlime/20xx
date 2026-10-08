/* Native parity harness for the in-engine MM1 randomizer.
 *
 *   cc -o mm1_rando_test mm1_rando_test.c
 *   ./mm1_rando_test in.prg SEED out.prg [visualizer] [weapons] [palette]
 *
 * Output must match `mmlc randomize mm1 --seed SEED` byte-for-byte.
 */
#include <stdio.h>
#include <stdlib.h>

#define MM1_LOG(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#include "../src/proxy/mm1_rando.h"

int main(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr,
                "usage: %s in.prg seed out.prg [visualizer] [weapons] [palette]\n",
                argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("open in"); return 1; }
    static unsigned char buf[0x100000];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    if (n != MM1_PRG_SIZE) {
        fprintf(stderr, "size %#zx != %#lx\n", n, (unsigned long)MM1_PRG_SIZE);
        return 1;
    }
    int vis = (argc > 4) ? atoi(argv[4]) : 0;
    int weapons = (argc > 5) ? atoi(argv[5]) : 1;
    int palette = (argc > 6) ? atoi(argv[6]) : 1;
    randomize_mm1(buf, argv[2], vis, weapons, palette);
    FILE *g = fopen(argv[3], "wb");
    if (!g) { perror("open out"); return 1; }
    fwrite(buf, 1, n, g);
    fclose(g);
    return 0;
}
