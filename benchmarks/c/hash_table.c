// Hash table benchmark - open addressing with various probing strategies
// Stresses: hash computation, memory access patterns, collision handling, cache behavior

#include <string.h>

#define TABLE_SIZE 262147 // prime, ~256K; keep load below pathological probe chains
#define NUM_OPS 500000

struct Entry {
    unsigned int key;
    int value;
    int occupied;
};

static struct Entry table[TABLE_SIZE];

// MurmurHash3 finalizer
static unsigned int hash(unsigned int key) {
    key ^= key >> 16;
    key *= 0x85ebca6b;
    key ^= key >> 13;
    key *= 0xc2b2ae35;
    key ^= key >> 16;
    return key;
}

static int insert(unsigned int key, int value) {
    unsigned int h = hash(key) % TABLE_SIZE;
    int probes = 0;
    while (table[h].occupied && table[h].key != key) {
        h = (h + 1) % TABLE_SIZE;
        probes++;
        if (probes > TABLE_SIZE) return -1; // full
    }
    table[h].key = key;
    table[h].value = value;
    table[h].occupied = 1;
    return probes;
}

static int lookup(unsigned int key, int *value) {
    unsigned int h = hash(key) % TABLE_SIZE;
    int probes = 0;
    while (table[h].occupied) {
        if (table[h].key == key) {
            *value = table[h].value;
            return probes;
        }
        h = (h + 1) % TABLE_SIZE;
        probes++;
        if (probes > TABLE_SIZE) return -1;
    }
    return -1; // not found
}

static int delete_key(unsigned int key) {
    unsigned int h = hash(key) % TABLE_SIZE;
    int probes = 0;
    while (table[h].occupied) {
        if (table[h].key == key) {
            // Robin Hood deletion: shift entries back
            table[h].occupied = 0;
            unsigned int next = (h + 1) % TABLE_SIZE;
            while (table[next].occupied) {
                unsigned int ideal = hash(table[next].key) % TABLE_SIZE;
                // Check if next entry is displaced from its ideal position
                int displaced;
                if (next >= ideal)
                    displaced = (h >= ideal && h < next);
                else
                    displaced = (h >= ideal || h < next);
                if (displaced) {
                    table[h] = table[next];
                    table[next].occupied = 0;
                    h = next;
                } else {
                    break;
                }
                next = (next + 1) % TABLE_SIZE;
            }
            return probes;
        }
        h = (h + 1) % TABLE_SIZE;
        probes++;
        if (probes > TABLE_SIZE) return -1;
    }
    return -1;
}

int main(void) {
    unsigned int seed = 42;
    long total_probes = 0;
    long found = 0;
    int i;

    memset(table, 0, sizeof(table));

    for (i = 0; i < NUM_OPS; i++) {
        seed = seed * 1664525u + 1013904223u;
        unsigned int key = seed;
        seed = seed * 1664525u + 1013904223u;
        int op = (seed >> 16) % 10;

        if (op < 5) {
            // Insert
            int probes = insert(key & 0xFFFFF, i);
            if (probes >= 0) total_probes += probes;
        } else if (op < 8) {
            // Lookup
            int value;
            int probes = lookup(key & 0xFFFFF, &value);
            if (probes >= 0) {
                total_probes += probes;
                found++;
            }
        } else {
            // Delete
            int probes = delete_key(key & 0xFFFFF);
            if (probes >= 0) total_probes += probes;
        }
    }

    long result = total_probes + found * 17;
    return (int)(result % 256);
}
