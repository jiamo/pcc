// Segmented sieve of Eratosthenes
// Stresses: cache-friendly memory access, bit manipulation, modular arithmetic

#include <math.h>
#include <string.h>

#define LIMIT 50000000
#define SEGMENT_SIZE 32768 // L1 cache-friendly
#define SQRT_LIMIT 7072    // ceil(sqrt(LIMIT))

static char small_sieve[SQRT_LIMIT + 1];
static int small_primes[1000]; // primes up to sqrt(LIMIT)
static int num_small_primes;
static char segment[SEGMENT_SIZE];

int main(void) {
    int i, j;
    long count = 0;

    // Step 1: Simple sieve for small primes
    memset(small_sieve, 1, sizeof(small_sieve));
    small_sieve[0] = small_sieve[1] = 0;
    for (i = 2; i * i <= SQRT_LIMIT; i++) {
        if (small_sieve[i]) {
            for (j = i * i; j <= SQRT_LIMIT; j += i)
                small_sieve[j] = 0;
        }
    }

    num_small_primes = 0;
    for (i = 2; i <= SQRT_LIMIT; i++) {
        if (small_sieve[i])
            small_primes[num_small_primes++] = i;
    }

    // Step 2: Segmented sieve
    long low = 2;
    while (low < LIMIT) {
        long high = low + SEGMENT_SIZE - 1;
        if (high >= LIMIT) high = LIMIT - 1;

        memset(segment, 1, SEGMENT_SIZE);

        for (i = 0; i < num_small_primes; i++) {
            long p = small_primes[i];
            // Find first multiple of p in [low, high]
            long start = ((low + p - 1) / p) * p;
            if (start == p) start += p; // don't cross off p itself
            if (start < p * p && p * p <= high) start = p * p;

            for (j = (int)(start - low); j <= (int)(high - low); j += (int)p)
                segment[j] = 0;
        }

        for (i = 0; i <= (int)(high - low); i++)
            if (segment[i]) count++;

        low = high + 1;
    }

    // count should be the number of primes below LIMIT
    return (int)(count % 256);
}
