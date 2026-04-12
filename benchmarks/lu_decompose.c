// LU Decomposition benchmark (Linpack-style)
// Stresses: floating point, array access patterns, loop interchange, cache performance

#include <math.h>

#define N 500

static double A[N][N];
static double L[N][N];
static double U[N][N];
static int pivot[N];

static void lu_decompose(int n) {
    int i, j, k;

    // Copy A into U, initialize L to identity
    for (i = 0; i < n; i++) {
        for (j = 0; j < n; j++) {
            U[i][j] = A[i][j];
            L[i][j] = (i == j) ? 1.0 : 0.0;
        }
        pivot[i] = i;
    }

    for (k = 0; k < n - 1; k++) {
        // Find pivot
        double max_val = fabs(U[k][k]);
        int max_row = k;
        for (i = k + 1; i < n; i++) {
            double v = fabs(U[i][k]);
            if (v > max_val) {
                max_val = v;
                max_row = i;
            }
        }

        // Swap rows
        if (max_row != k) {
            int tmp = pivot[k];
            pivot[k] = pivot[max_row];
            pivot[max_row] = tmp;
            for (j = 0; j < n; j++) {
                double t = U[k][j];
                U[k][j] = U[max_row][j];
                U[max_row][j] = t;
            }
            for (j = 0; j < k; j++) {
                double t = L[k][j];
                L[k][j] = L[max_row][j];
                L[max_row][j] = t;
            }
        }

        // Eliminate
        if (fabs(U[k][k]) > 1e-15) {
            for (i = k + 1; i < n; i++) {
                L[i][k] = U[i][k] / U[k][k];
                for (j = k; j < n; j++) {
                    U[i][j] -= L[i][k] * U[k][j];
                }
            }
        }
    }
}

int main(void) {
    int i, j;
    unsigned int seed = 314159;

    // Initialize matrix with pseudo-random values
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            seed = seed * 1664525u + 1013904223u;
            A[i][j] = (double)((int)(seed >> 8) % 2000 - 1000) / 100.0;
        }
        // Make diagonally dominant to ensure stability
        A[i][i] += 50.0;
    }

    lu_decompose(N);

    // Checksum from diagonal of U
    double check = 0.0;
    for (i = 0; i < N; i++)
        check += U[i][i];

    int result = (int)(check * 100.0);
    if (result < 0) result = -result;
    return result % 256;
}
