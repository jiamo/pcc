// LZW-like compression benchmark
// Stresses: hash table, string matching, dictionary building, compression patterns

#include <string.h>

#define DICT_SIZE 4096
#define HASH_SIZE 8191 // prime
#define DATA_SIZE 65536

struct DictEntry {
    int prefix;
    unsigned char ch;
    int code;
    int next; // hash chain
};

static struct DictEntry dict[DICT_SIZE];
static int hash_table[HASH_SIZE];
static int dict_count;

static void dict_init(void) {
    int i;
    memset(hash_table, -1, sizeof(hash_table));
    dict_count = 0;
    for (i = 0; i < 256; i++) {
        dict[dict_count].prefix = -1;
        dict[dict_count].ch = (unsigned char)i;
        dict[dict_count].code = dict_count;
        dict[dict_count].next = -1;
        int h = i % HASH_SIZE;
        dict[dict_count].next = hash_table[h];
        hash_table[h] = dict_count;
        dict_count++;
    }
}

static int dict_lookup(int prefix, unsigned char ch) {
    unsigned int h = ((unsigned int)(prefix + 1) * 259 + ch) % HASH_SIZE;
    int idx = hash_table[h];
    while (idx >= 0) {
        if (dict[idx].prefix == prefix && dict[idx].ch == ch)
            return idx;
        idx = dict[idx].next;
    }
    return -1;
}

static int dict_add(int prefix, unsigned char ch) {
    if (dict_count >= DICT_SIZE) return -1;
    int idx = dict_count++;
    dict[idx].prefix = prefix;
    dict[idx].ch = ch;
    dict[idx].code = idx;
    unsigned int h = ((unsigned int)(prefix + 1) * 259 + ch) % HASH_SIZE;
    dict[idx].next = hash_table[h];
    hash_table[h] = idx;
    return idx;
}

static long compress(const unsigned char *data, int len) {
    long output_bits = 0;
    int current_bits = 9; // starts at 9-bit codes
    int i = 0;
    int w = data[i++];

    while (i < len) {
        unsigned char ch = data[i++];
        int idx = dict_lookup(w, ch);
        if (idx >= 0) {
            w = idx;
        } else {
            output_bits += current_bits;
            if (dict_count < DICT_SIZE) {
                dict_add(w, ch);
                if (dict_count > (1 << current_bits) && current_bits < 12)
                    current_bits++;
            }
            w = ch;
        }
    }
    output_bits += current_bits; // final code
    return output_bits;
}

int main(void) {
    unsigned char data[DATA_SIZE];
    int i;
    long total_bits = 0;
    unsigned int seed = 98765;

    for (int iter = 0; iter < 500; iter++) {
        // Generate semi-repetitive data (more compressible)
        for (i = 0; i < DATA_SIZE; i++) {
            seed = seed * 1103515245u + 12345u;
            // Mix of repetitive and random
            if ((seed >> 16) % 4 == 0)
                data[i] = (unsigned char)(seed % 26 + 'a');
            else
                data[i] = (unsigned char)(data[(i > 0) ? i - 1 : 0]);
        }

        dict_init();
        total_bits += compress(data, DATA_SIZE);
    }

    return (int)((total_bits >> 8) % 256);
}
