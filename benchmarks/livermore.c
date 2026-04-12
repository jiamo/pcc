// Livermore Loops - selected kernels from the Livermore Fortran Kernels
// Original by Frank McMahon, Lawrence Livermore National Laboratory
// Stresses: vectorizable loops, floating point, array access patterns

#include <math.h>

#define N 1000
#define OUTER 2000

static double x[N], y[N], z[N], u[N], v[N], w[N];
static double a[N][N];

// Kernel 1: Hydro fragment
static double kernel1(void) {
    int i, k;
    double q = 0.5;
    double r = 0.3;
    double t = 0.7;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        for (i = 0; i < N; i++)
            x[i] = q + y[i] * (r * z[i + 10 < N ? i + 10 : i] + t * z[i + 11 < N ? i + 11 : i]);
        sum += x[N/2];
    }
    return sum;
}

// Kernel 2: ICCG excerpt (incomplete Cholesky)
static double kernel2(void) {
    int i, k;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        for (i = 1; i < N; i++)
            x[i] = x[i] - x[i-1] * 0.5 * y[i-1];
        sum += x[N/2];
    }
    return sum;
}

// Kernel 3: Inner product
static double kernel3(void) {
    int i, k;
    double q = 0.0;
    for (k = 0; k < OUTER; k++) {
        q = 0.0;
        for (i = 0; i < N; i++)
            q += z[i] * x[i];
    }
    return q;
}

// Kernel 5: Tri-diagonal elimination (below diagonal)
static double kernel5(void) {
    int i, k;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        for (i = 1; i < N; i++)
            x[i] = z[i] * (y[i] - x[i-1]);
        sum += x[N/2];
    }
    return sum;
}

// Kernel 7: Equation of state fragment
static double kernel7(void) {
    int i, k;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        for (i = 0; i < N - 6; i++)
            x[i] = u[i] + v[i] * (w[i] + y[i] * (z[i] + u[i+3] * (v[i+3] + w[i+3] * (y[i+3] + z[i+3]))));
        sum += x[N/4];
    }
    return sum;
}

// Kernel 11: First sum
static double kernel11(void) {
    int i, k;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        x[0] = y[0];
        for (i = 1; i < N; i++)
            x[i] = x[i-1] + y[i];
        sum += x[N/2];
    }
    return sum;
}

// Kernel 12: First difference
static double kernel12(void) {
    int i, k;
    double sum = 0.0;
    for (k = 0; k < OUTER; k++) {
        for (i = 0; i < N - 1; i++)
            x[i] = y[i+1] - y[i];
        sum += x[N/2];
    }
    return sum;
}

// Kernel 21: Matrix*Matrix product
static double kernel21(void) {
    int i, j, k;
    double sum = 0.0;
    int M = 100; // smaller for matrix*matrix
    for (k = 0; k < 20; k++) {
        for (i = 0; i < M; i++)
            for (j = 0; j < M; j++) {
                double s = 0.0;
                int l;
                for (l = 0; l < M; l++)
                    s += a[i][l] * a[l][j];
                a[i][j] = s;
            }
        sum += a[M/2][M/2];
    }
    return sum;
}

int main(void) {
    int i, j;
    // Initialize arrays
    for (i = 0; i < N; i++) {
        x[i] = 0.0001 * (i + 1);
        y[i] = 0.0002 * (N - i);
        z[i] = 0.0003 * (i + 1);
        u[i] = 0.0004 * (i + 1);
        v[i] = 0.0005 * (N - i);
        w[i] = 0.0006 * (i + 1);
    }
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            a[i][j] = 0.001 * ((i + 1) * (j + 1) % 1000);

    double total = 0.0;
    total += kernel1();
    total += kernel2();
    total += kernel3();
    total += kernel5();
    total += kernel7();
    total += kernel11();
    total += kernel12();
    total += kernel21();

    long result = (long)(total * 1337.0);
    if (result < 0) result = -result;
    return (int)((result + 1) % 256);
}
