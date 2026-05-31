// Sieve of Eratosthenes up to 1,000,000
#include <stdio.h>

int sieve[1000001];

int main() {
    int i, j, count = 0;
    for (i = 0; i <= 1000000; i++) sieve[i] = 1;
    sieve[0] = 0;
    sieve[1] = 0;
    for (i = 2; i * i <= 1000000; i++) {
        if (sieve[i]) {
            for (j = i * i; j <= 1000000; j += i)
                sieve[j] = 0;
        }
    }
    for (i = 2; i <= 1000000; i++)
        if (sieve[i]) count++;
    printf("%d\n", count);
    return 0;
}
