// Bit counting / manipulation benchmark
// Stresses: bit operations, integer arithmetic, lookup tables, multiple algorithms

#define ITERATIONS 50000000

// Method 1: Brian Kernighan's algorithm
static int popcount_kernighan(unsigned int v) {
    int c = 0;
    while (v) { v &= v - 1; c++; }
    return c;
}

// Method 2: Parallel bit counting (Hamming weight)
static int popcount_parallel(unsigned int v) {
    v = v - ((v >> 1) & 0x55555555);
    v = (v & 0x33333333) + ((v >> 2) & 0x33333333);
    return (((v + (v >> 4)) & 0x0F0F0F0F) * 0x01010101) >> 24;
}

// Method 3: Lookup table
static unsigned char popcount_table[256];

static void init_table(void) {
    int i;
    for (i = 0; i < 256; i++) {
        popcount_table[i] = (unsigned char)popcount_kernighan(i);
    }
}

static int popcount_lookup(unsigned int v) {
    return popcount_table[v & 0xFF] +
           popcount_table[(v >> 8) & 0xFF] +
           popcount_table[(v >> 16) & 0xFF] +
           popcount_table[(v >> 24) & 0xFF];
}

// Method 4: Count leading/trailing zeros style operations
static int clz(unsigned int v) {
    if (v == 0) return 32;
    int n = 0;
    if ((v & 0xFFFF0000) == 0) { n += 16; v <<= 16; }
    if ((v & 0xFF000000) == 0) { n += 8;  v <<= 8;  }
    if ((v & 0xF0000000) == 0) { n += 4;  v <<= 4;  }
    if ((v & 0xC0000000) == 0) { n += 2;  v <<= 2;  }
    if ((v & 0x80000000) == 0) { n += 1; }
    return n;
}

static int ctz(unsigned int v) {
    if (v == 0) return 32;
    int n = 0;
    if ((v & 0x0000FFFF) == 0) { n += 16; v >>= 16; }
    if ((v & 0x000000FF) == 0) { n += 8;  v >>= 8;  }
    if ((v & 0x0000000F) == 0) { n += 4;  v >>= 4;  }
    if ((v & 0x00000003) == 0) { n += 2;  v >>= 2;  }
    if ((v & 0x00000001) == 0) { n += 1; }
    return n;
}

// Bit reversal
static unsigned int bit_reverse(unsigned int v) {
    v = ((v >> 1) & 0x55555555) | ((v & 0x55555555) << 1);
    v = ((v >> 2) & 0x33333333) | ((v & 0x33333333) << 2);
    v = ((v >> 4) & 0x0F0F0F0F) | ((v & 0x0F0F0F0F) << 4);
    v = ((v >> 8) & 0x00FF00FF) | ((v & 0x00FF00FF) << 8);
    v = (v >> 16) | (v << 16);
    return v;
}

int main(void) {
    init_table();

    long total = 0;
    unsigned int v = 0xDEADBEEF;
    int i;

    for (i = 0; i < ITERATIONS; i++) {
        v = v * 1664525u + 1013904223u;
        total += popcount_kernighan(v);
        total += popcount_parallel(v);
        total += popcount_lookup(v);
        total += clz(v);
        total += ctz(v);
        total += popcount_parallel(bit_reverse(v));
    }

    return (int)(total % 256);
}
