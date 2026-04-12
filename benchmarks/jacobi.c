// Jacobi iterative solver benchmark (2D Laplace equation)
// Stresses: stencil computation, floating point, memory bandwidth, convergence

#include <math.h>

#define N 512

static double u[N][N];
static double u_new[N][N];

int main(void) {
    int i, j, iter;

    // Initialize: boundary conditions
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            u[i][j] = 0.0;
            u_new[i][j] = 0.0;
        }
    }
    // Top boundary: sin wave
    for (j = 0; j < N; j++) {
        u[0][j] = sin(3.14159265358979 * j / (N - 1));
        u_new[0][j] = u[0][j];
    }
    // Bottom boundary
    for (j = 0; j < N; j++) {
        u[N-1][j] = 0.0;
    }

    // Jacobi iteration
    int max_iter = 500;
    for (iter = 0; iter < max_iter; iter++) {
        for (i = 1; i < N - 1; i++) {
            for (j = 1; j < N - 1; j++) {
                u_new[i][j] = 0.25 * (u[i-1][j] + u[i+1][j] +
                                       u[i][j-1] + u[i][j+1]);
            }
        }
        // Swap
        for (i = 1; i < N - 1; i++) {
            for (j = 1; j < N - 1; j++) {
                u[i][j] = u_new[i][j];
            }
        }
    }

    // Checksum
    double check = 0.0;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            check += u[i][j];

    int result = (int)(check * 10000.0);
    if (result < 0) result = -result;
    return result % 256;
}
