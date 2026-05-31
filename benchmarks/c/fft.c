// FFT (Fast Fourier Transform) benchmark - Cooley-Tukey radix-2
// Stresses: floating point, trigonometric functions, butterfly operations, array indexing

#include <math.h>

#define LOG2N 16
#define FFT_N (1 << LOG2N)

static double re[FFT_N];
static double im[FFT_N];

static void bit_reverse(int n, int log2n) {
    int i, j;
    for (i = 0; i < n; i++) {
        j = 0;
        int ii = i;
        int k;
        for (k = 0; k < log2n; k++) {
            j = (j << 1) | (ii & 1);
            ii >>= 1;
        }
        if (j > i) {
            double tmp;
            tmp = re[i]; re[i] = re[j]; re[j] = tmp;
            tmp = im[i]; im[i] = im[j]; im[j] = tmp;
        }
    }
}

static void fft(int n, int log2n, int inverse) {
    double pi2 = inverse ? -6.283185307179586 : 6.283185307179586;
    int step, group, pair;

    bit_reverse(n, log2n);

    for (step = 1; step < n; step <<= 1) {
        int jump = step << 1;
        double angle = pi2 / (double)jump;
        double wr = cos(angle);
        double wi = sin(angle);

        for (group = 0; group < n; group += jump) {
            double twr = 1.0, twi = 0.0;
            for (pair = 0; pair < step; pair++) {
                int a = group + pair;
                int b = a + step;
                double tr = twr * re[b] - twi * im[b];
                double ti = twr * im[b] + twi * re[b];
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
                double new_wr = twr * wr - twi * wi;
                twi = twr * wi + twi * wr;
                twr = new_wr;
            }
        }
    }

    if (inverse) {
        int i;
        double inv_n = 1.0 / n;
        for (i = 0; i < n; i++) {
            re[i] *= inv_n;
            im[i] *= inv_n;
        }
    }
}

int main(void) {
    int i;
    int n = FFT_N;

    // Initialize signal: sum of sinusoids
    for (i = 0; i < n; i++) {
        double t = (double)i / n;
        re[i] = sin(2.0 * 3.14159265358979 * 7.0 * t) +
                 0.5 * sin(2.0 * 3.14159265358979 * 31.0 * t) +
                 0.25 * cos(2.0 * 3.14159265358979 * 127.0 * t);
        im[i] = 0.0;
    }

    // Forward FFT
    fft(n, LOG2N, 0);

    // Find magnitude spectrum peak (besides DC)
    double max_mag = 0.0;
    int max_idx = 0;
    for (i = 1; i < n / 2; i++) {
        double mag = re[i] * re[i] + im[i] * im[i];
        if (mag > max_mag) {
            max_mag = mag;
            max_idx = i;
        }
    }

    // Inverse FFT
    fft(n, LOG2N, 1);

    // Verify round-trip: compute error
    double error = 0.0;
    for (i = 0; i < n; i++) {
        double t = (double)i / n;
        double expected = sin(2.0 * 3.14159265358979 * 7.0 * t) +
                          0.5 * sin(2.0 * 3.14159265358979 * 31.0 * t) +
                          0.25 * cos(2.0 * 3.14159265358979 * 127.0 * t);
        double diff = re[i] - expected;
        error += diff * diff;
    }

    int result = max_idx * 31 + (int)(error * 1000.0);
    if (result < 0) result = -result;
    return result % 256;
}
