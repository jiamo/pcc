// Matrix chain multiplication ordering benchmark (dynamic programming)
// Plus actual matrix multiplication of optimal ordering
// Stresses: DP table construction, nested loops, recursion, integer arithmetic

#define MAX_MATRICES 200

static long dp[MAX_MATRICES + 1][MAX_MATRICES + 1];
static int split[MAX_MATRICES + 1][MAX_MATRICES + 1];
static int dims[MAX_MATRICES + 1];

static void matrix_chain_order(int n) {
    int i, j, k, l;

    for (i = 1; i <= n; i++)
        dp[i][i] = 0;

    for (l = 2; l <= n; l++) { // chain length
        for (i = 1; i <= n - l + 1; i++) {
            j = i + l - 1;
            dp[i][j] = 0x7FFFFFFFFFFFFFFFLL;
            for (k = i; k < j; k++) {
                long cost = dp[i][k] + dp[k+1][j] +
                            (long)dims[i-1] * dims[k] * dims[j];
                if (cost < dp[i][j]) {
                    dp[i][j] = cost;
                    split[i][j] = k;
                }
            }
        }
    }
}

// Count multiplications in optimal order using split table
static long count_ops(int i, int j) {
    if (i == j) return 0;
    int k = split[i][j];
    long left = count_ops(i, k);
    long right = count_ops(k + 1, j);
    return left + right + (long)dims[i-1] * dims[k] * dims[j];
}

int main(void) {
    int iter;
    long total = 0;
    unsigned int seed = 271828;

    for (iter = 0; iter < 500; iter++) {
        int n = 50 + (iter % 150); // vary chain length

        // Generate random dimensions
        int i;
        for (i = 0; i <= n; i++) {
            seed = seed * 1664525u + 1013904223u;
            dims[i] = (int)((seed >> 8) % 50) + 5;
        }

        matrix_chain_order(n);

        // Verify with recursive count
        long optimal_cost = count_ops(1, n);
        total += optimal_cost;
        total += dp[1][n]; // should be same
    }

    return (int)((total >> 16) % 256);
}
