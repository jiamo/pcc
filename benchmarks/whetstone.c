// Whetstone benchmark - single-file C version
// Original by H.J. Curnow and B.A. Wichmann, 1976
// Stresses: floating point arithmetic, transcendental functions, array indexing

#include <math.h>

static double e1[4];
static int j, k, l;
static double t, t1, t2;

static void pa(double e[4]) {
    int j;
    j = 0;
    do {
        e[0] = (e[0] + e[1] + e[2] - e[3]) * t;
        e[1] = (e[0] + e[1] - e[2] + e[3]) * t;
        e[2] = (e[0] - e[1] + e[2] + e[3]) * t;
        e[3] = (-e[0] + e[1] + e[2] + e[3]) / t2;
        j++;
    } while (j < 6);
}

static void p0(void) {
    e1[j] = e1[k];
    e1[k] = e1[l];
    e1[l] = e1[j];
}

static void p3(double x, double y, double *z) {
    double x1, y1;
    x1 = x;
    y1 = y;
    x1 = t * (x1 + y1);
    y1 = t * (x1 + y1);
    *z = (x1 + y1) / t2;
}

int main(void) {
    int i, n1, n2, n3, n4, n5, n6, n7, n8;
    double x1, x2, x3, x4, x, y, z;
    int loop;
    int WHET_LOOPS = 100; // scale factor

    n1 = 0;
    n2 = 12 * WHET_LOOPS;
    n3 = 14 * WHET_LOOPS;
    n4 = 345 * WHET_LOOPS;
    n5 = 0;
    n6 = 210 * WHET_LOOPS;
    n7 = 32 * WHET_LOOPS;
    n8 = 899 * WHET_LOOPS;

    t = 0.499975;
    t1 = 0.50025;
    t2 = 2.0;

    // Module 1: Simple identifiers
    x1 = 1.0;
    x2 = x3 = x4 = -1.0;
    for (i = 1; i <= n1; i++) {
        x1 = (x1 + x2 + x3 - x4) * t;
        x2 = (x1 + x2 - x3 + x4) * t;
        x3 = (x1 - x2 + x3 + x4) * t;
        x4 = (-x1 + x2 + x3 + x4) * t;
    }

    // Module 2: Array elements
    e1[0] = 1.0;
    e1[1] = e1[2] = e1[3] = -1.0;
    for (i = 1; i <= n2; i++)
        pa(e1);

    // Module 3: Array as parameter
    j = k = l = 0;
    for (i = 1; i <= n3; i++) {
        j = (j + 1) % 4;
        k = (k + 2) % 4;
        l = (l + 3) % 4;
        p0();
    }

    // Module 4: Conditional jumps
    j = 1;
    for (i = 1; i <= n4; i++) {
        if (j == 1) j = 2;
        else j = 3;
        if (j > 2) j = 0;
        else j = 1;
        if (j < 1) j = 1;
        else j = 0;
    }

    // Module 5: Omitted (I/O)

    // Module 6: Integer arithmetic
    j = 1;
    k = 2;
    l = 3;
    for (i = 1; i <= n6; i++) {
        j = j * (k - j) * (l - k);
        k = l * k - (l - j) * k;
        l = (l - k) * (k + j);
        e1[l - 2] = (double)(j + k + l);
        e1[k - 2] = (double)(j * k * l);
    }

    // Module 7: Trig functions
    x = y = 0.5;
    for (i = 1; i <= n7; i++) {
        x = t * atan(t2 * sin(x) * cos(x) / (cos(x + y) + cos(x - y) - 1.0));
        y = t * atan(t2 * sin(y) * cos(y) / (cos(x + y) + cos(x - y) - 1.0));
    }

    // Module 8: Procedure calls
    x = y = z = 1.0;
    for (i = 1; i <= n8; i++)
        p3(x, y, &z);

    // Module 11: Standard functions
    x = 0.75;
    for (i = 1; i <= n6; i++)
        x = sqrt(exp(log(x) / t1));

    double result = x + e1[0] + e1[1] + e1[2] + e1[3] + (double)j + (double)k + z;
    int iresult = (int)(result * 1000000.0);
    if (iresult < 0) iresult = -iresult;
    return iresult % 256;
}
