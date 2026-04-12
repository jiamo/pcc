// N-Queens benchmark
// Stresses: recursion, backtracking, array access, branch prediction

static int solutions;

static void solve(int n, int row, int cols, int diag1, int diag2) {
    if (row == n) {
        solutions++;
        return;
    }
    int avail = ((1 << n) - 1) & ~(cols | diag1 | diag2);
    while (avail) {
        int bit = avail & (-avail); // lowest set bit
        avail &= avail - 1;
        solve(n, row + 1, cols | bit, (diag1 | bit) << 1, (diag2 | bit) >> 1);
    }
}

int main(void) {
    // N=14 gives 365596 solutions and good runtime
    solutions = 0;
    solve(14, 0, 0, 0, 0);
    // Expected: 365596
    return solutions % 256;
}
