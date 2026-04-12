// Fannkuch-redux benchmark
// From the Computer Language Benchmarks Game
// Stresses: integer array manipulation, permutations, branch prediction
// Original by Oleg Mazurov, adapted to single-file standalone

#include <stdlib.h>

static int max_flips = 0;
static int checksum = 0;

void fannkuch(int n) {
    int perm[16], perm1[16], count[16], tmp;
    int i, r, flips, k;

    for (i = 0; i < n; i++) perm1[i] = i;

    r = n;
    for (;;) {
        while (r > 1) { count[r - 1] = r; r--; }

        for (i = 0; i < n; i++) perm[i] = perm1[i];

        flips = 0;
        k = perm[0];
        while (k != 0) {
            int lo = 0, hi = k;
            while (lo < hi) {
                tmp = perm[lo]; perm[lo] = perm[hi]; perm[hi] = tmp;
                lo++; hi--;
            }
            flips++;
            k = perm[0];
        }

        if (flips > max_flips) max_flips = flips;
        checksum += (count[1] & 1) ? -flips : flips;

        for (;;) {
            if (r == n) return;
            int perm0 = perm1[0];
            for (i = 0; i < r; i++) perm1[i] = perm1[i + 1];
            perm1[r] = perm0;
            count[r]--;
            if (count[r] > 0) break;
            r++;
        }
    }
}

int main(void) {
    fannkuch(11); // n=11 gives good runtime at -O2
    // Expected: max_flips=51, checksum=556355 for n=11
    int result = (max_flips * 31 + checksum) & 0xFFFF;
    return result % 256;
}
