// EXPECT: 25
// Count primes up to 100 using Sieve of Eratosthenes
int main() {
    int sieve[101];
    int i;
    int j;
    for (i = 0; i <= 100; i++) sieve[i] = 1;
    sieve[0] = 0;
    sieve[1] = 0;
    for (i = 2; i * i <= 100; i++) {
        if (sieve[i]) {
            for (j = i * i; j <= 100; j += i)
                sieve[j] = 0;
        }
    }
    int count = 0;
    for (i = 2; i <= 100; i++)
        if (sieve[i]) count++;
    return count;
}
