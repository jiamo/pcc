// 0/1 Knapsack dynamic programming benchmark
// Stresses: 2D array access, conditional updates, dynamic programming patterns

#define MAX_ITEMS 500
#define MAX_WEIGHT 1000

static int dp[MAX_ITEMS + 1][MAX_WEIGHT + 1];
static int weights[MAX_ITEMS];
static int values[MAX_ITEMS];

int main(void) {
    int n = MAX_ITEMS;
    int W = MAX_WEIGHT;
    int i, w;
    unsigned int seed = 31415;
    long total = 0;

    // Run multiple instances
    for (int iter = 0; iter < 20; iter++) {
        // Generate random items
        for (i = 0; i < n; i++) {
            seed = seed * 1664525u + 1013904223u;
            weights[i] = (int)((seed >> 8) % 50) + 1;
            seed = seed * 1664525u + 1013904223u;
            values[i] = (int)((seed >> 8) % 100) + 1;
        }

        // DP
        for (w = 0; w <= W; w++)
            dp[0][w] = 0;

        for (i = 1; i <= n; i++) {
            for (w = 0; w <= W; w++) {
                dp[i][w] = dp[i-1][w];
                if (weights[i-1] <= w) {
                    int with_item = dp[i-1][w - weights[i-1]] + values[i-1];
                    if (with_item > dp[i][w])
                        dp[i][w] = with_item;
                }
            }
        }

        total += dp[n][W];
    }

    return (int)(total % 256);
}
