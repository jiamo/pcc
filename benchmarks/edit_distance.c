// Edit distance (Levenshtein) benchmark
// Stresses: 2D array access, min operations, dynamic programming, string processing

#include <string.h>

#define MAX_LEN 2048

static int dp[MAX_LEN + 1][MAX_LEN + 1];
static char str_a[MAX_LEN];
static char str_b[MAX_LEN];

static int min3(int a, int b, int c) {
    if (a < b) return (a < c) ? a : c;
    return (b < c) ? b : c;
}

static int edit_distance(const char *a, int la, const char *b, int lb) {
    int i, j;
    for (i = 0; i <= la; i++) dp[i][0] = i;
    for (j = 0; j <= lb; j++) dp[0][j] = j;

    for (i = 1; i <= la; i++) {
        for (j = 1; j <= lb; j++) {
            int cost = (a[i-1] == b[j-1]) ? 0 : 1;
            dp[i][j] = min3(
                dp[i-1][j] + 1,
                dp[i][j-1] + 1,
                dp[i-1][j-1] + cost
            );
        }
    }
    return dp[la][lb];
}

int main(void) {
    int iter;
    long total = 0;
    unsigned int seed = 271828;

    for (iter = 0; iter < 200; iter++) {
        int la = 800 + (iter % 400);
        int lb = 800 + ((iter * 7) % 400);
        int i;

        // Generate pseudo-random strings
        for (i = 0; i < la; i++) {
            seed = seed * 1103515245u + 12345u;
            str_a[i] = 'a' + (seed >> 16) % 4; // small alphabet for more matches
        }
        for (i = 0; i < lb; i++) {
            seed = seed * 1103515245u + 12345u;
            str_b[i] = 'a' + (seed >> 16) % 4;
        }

        total += edit_distance(str_a, la, str_b, lb);
    }

    return (int)(total % 256);
}
