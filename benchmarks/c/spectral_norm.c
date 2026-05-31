// Spectral-norm benchmark
// From the Computer Language Benchmarks Game
// Stresses: floating point, function calls, loop-carried dependencies
// Original by Sebastien Loisel, simplified to single-threaded C

#include <math.h>

static double eval_A(int i, int j) {
    return 1.0 / ((double)((i + j) * (i + j + 1) / 2 + i + 1));
}

static void eval_A_times_u(int N, const double u[], double Au[]) {
    int i, j;
    for (i = 0; i < N; i++) {
        Au[i] = 0.0;
        for (j = 0; j < N; j++)
            Au[i] += eval_A(i, j) * u[j];
    }
}

static void eval_At_times_u(int N, const double u[], double Au[]) {
    int i, j;
    for (i = 0; i < N; i++) {
        Au[i] = 0.0;
        for (j = 0; j < N; j++)
            Au[i] += eval_A(j, i) * u[j];
    }
}

static void eval_AtA_times_u(int N, const double u[], double AtAu[]) {
    double v[2048];
    eval_A_times_u(N, u, v);
    eval_At_times_u(N, v, AtAu);
}

int main(void) {
    int N = 500;
    double u[2048], v[2048];
    int i;

    for (i = 0; i < N; i++) u[i] = 1.0;

    for (i = 0; i < 10; i++) {
        eval_AtA_times_u(N, u, v);
        eval_AtA_times_u(N, v, u);
    }

    double vBv = 0.0, vv = 0.0;
    for (i = 0; i < N; i++) {
        vBv += u[i] * v[i];
        vv  += v[i] * v[i];
    }

    double result = sqrt(vBv / vv);
    // result is approximately 1.274224153
    int iresult = (int)(result * 1000000.0);
    return iresult % 256;
}
