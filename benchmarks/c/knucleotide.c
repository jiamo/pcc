// K-nucleotide style benchmark
// Stresses: hash table operations, string processing, memory access patterns
// Counts frequency of k-length subsequences in a generated DNA sequence

#include <string.h>

#define HASH_SIZE 65537
#define SEQ_LEN 2000000

struct Entry {
    unsigned long long key;
    int count;
    int used;
};

static struct Entry table[HASH_SIZE];
static char sequence[SEQ_LEN];

static unsigned int hash_key(unsigned long long key) {
    key = key ^ (key >> 16);
    key *= 0x45d9f3b;
    key = key ^ (key >> 16);
    return (unsigned int)(key % HASH_SIZE);
}

static void insert(unsigned long long key) {
    unsigned int h = hash_key(key);
    while (table[h].used && table[h].key != key) {
        h = (h + 1) % HASH_SIZE;
    }
    if (!table[h].used) {
        table[h].key = key;
        table[h].used = 1;
        table[h].count = 0;
    }
    table[h].count++;
}

static int encode(char c) {
    switch (c) {
        case 'A': return 0;
        case 'C': return 1;
        case 'G': return 2;
        case 'T': return 3;
    }
    return 0;
}

// Generate a pseudo-random DNA sequence
static void generate_sequence(void) {
    unsigned int state = 12345u;
    int i;
    const char bases[] = "ACGT";
    for (i = 0; i < SEQ_LEN; i++) {
        state = (state * 1103515245u + 12345u) & 0x7FFFFFFFu;
        sequence[i] = bases[state % 4];
    }
}

static long count_kmers(int k) {
    unsigned long long mask = (1ULL << (2 * k)) - 1;
    unsigned long long kmer = 0;
    long total = 0;
    int i;

    memset(table, 0, sizeof(table));

    for (i = 0; i < k - 1; i++) {
        kmer = (kmer << 2) | encode(sequence[i]);
    }

    for (i = k - 1; i < SEQ_LEN; i++) {
        kmer = ((kmer << 2) | encode(sequence[i])) & mask;
        insert(kmer);
    }

    for (i = 0; i < HASH_SIZE; i++) {
        if (table[i].used)
            total += (long)table[i].count * table[i].count;
    }
    return total;
}

int main(void) {
    generate_sequence();

    long result = 0;
    result += count_kmers(1);
    result += count_kmers(2);
    result += count_kmers(3);
    result += count_kmers(6);

    return (int)((result >> 4) % 256);
}
