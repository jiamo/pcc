// Power method for eigenvalue computation (simplified spectral norm)
// Stresses: dense matrix-vector multiply, floating point, convergence iteration
// Based on ideas from SPEC CPU benchmarks

#include <math.h>

#define N 400

static double A[N][N];
static double x[N], y[N], z[N];

static void mat_vec(double mat[N][N], const double *v, double *out, int n) {
    int i, j;
    for (i = 0; i < n; i++) {
        double sum = 0.0;
        for (j = 0; j < n; j++)
            sum += mat[i][j] * v[j];
        out[i] = sum;
    }
}

static double dot(const double *a, const double *b, int n) {
    double sum = 0.0;
    int i;
    for (i = 0; i < n; i++) sum += a[i] * b[i];
    return sum;
}

static void normalize(double *v, int n) {
    double norm = sqrt(dot(v, v, n));
    if (norm > 0.0) {
        int i;
        for (i = 0; i < n; i++) v[i] /= norm;
    }
}

int main(void) {
    int i, j, iter;

    // Build a symmetric matrix with known eigenvalue structure
    for (i = 0; i < N; i++) {
        for (j = 0; j <= i; j++) {
            double val = 1.0 / ((double)(i + j + 1));
            A[i][j] = val;
            A[j][i] = val;
        }
    }

    // Initialize eigenvector guess
    for (i = 0; i < N; i++) x[i] = 1.0;
    normalize(x, N);

    // Power iteration
    double eigenvalue = 0.0;
    for (iter = 0; iter < 200; iter++) {
        mat_vec(A, x, y, N);
        eigenvalue = dot(x, y, N);
        double norm = sqrt(dot(y, y, N));
        for (i = 0; i < N; i++) x[i] = y[i] / norm;
    }

    int result = (int)(eigenvalue * 1000000.0);
    if (result < 0) result = -result;
    return result % 256;
}
