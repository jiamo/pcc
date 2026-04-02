// Simple N-body simulation - stresses floating point and loops
#include <stdio.h>
#include <math.h>

#define N 200
#define STEPS 100

double px[N], py[N], pz[N];
double vx[N], vy[N], vz[N];
double mass[N];

int main() {
    int i, j, step;
    double dt = 0.01;

    // Initialize bodies in a simple pattern
    for (i = 0; i < N; i++) {
        px[i] = (double)(i * 13 % 97) / 97.0 * 10.0;
        py[i] = (double)(i * 31 % 97) / 97.0 * 10.0;
        pz[i] = (double)(i * 47 % 97) / 97.0 * 10.0;
        vx[i] = 0.0;
        vy[i] = 0.0;
        vz[i] = 0.0;
        mass[i] = 1.0;
    }

    for (step = 0; step < STEPS; step++) {
        // Compute forces and update velocities
        for (i = 0; i < N; i++) {
            double fx = 0.0, fy = 0.0, fz = 0.0;
            for (j = 0; j < N; j++) {
                if (i == j) continue;
                double dx = px[j] - px[i];
                double dy = py[j] - py[i];
                double dz = pz[j] - pz[i];
                double dist2 = dx * dx + dy * dy + dz * dz + 0.01;
                double inv_dist = 1.0 / sqrt(dist2);
                double inv_dist3 = inv_dist * inv_dist * inv_dist;
                double f = mass[j] * inv_dist3;
                fx += dx * f;
                fy += dy * f;
                fz += dz * f;
            }
            vx[i] += dt * fx;
            vy[i] += dt * fy;
            vz[i] += dt * fz;
        }
        // Update positions
        for (i = 0; i < N; i++) {
            px[i] += dt * vx[i];
            py[i] += dt * vy[i];
            pz[i] += dt * vz[i];
        }
    }

    // Compute total kinetic energy
    double ke = 0.0;
    for (i = 0; i < N; i++) {
        ke += 0.5 * mass[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
    }
    printf("%.6f\n", ke);
    return 0;
}
