// Huffman encoding/decoding benchmark
// Stresses: tree construction, priority queue, bit manipulation, compression-like patterns

#include <string.h>

#define MAX_SYMBOLS 256
#define MAX_NODES 512

struct HuffNode {
    int freq;
    int symbol; // -1 for internal nodes
    int left, right;
};

static struct HuffNode nodes[MAX_NODES];
static int node_count;
static int code_len[MAX_SYMBOLS];
static unsigned int code_val[MAX_SYMBOLS];

static int new_node(int freq, int symbol, int left, int right) {
    int idx = node_count++;
    nodes[idx].freq = freq;
    nodes[idx].symbol = symbol;
    nodes[idx].left = left;
    nodes[idx].right = right;
    return idx;
}

// Simple priority queue using linear scan (good enough for 256 symbols)
static int heap[MAX_NODES];
static int heap_size;

static void heap_push(int node_idx) {
    heap[heap_size++] = node_idx;
}

static int heap_pop(void) {
    int min_i = 0, i;
    for (i = 1; i < heap_size; i++) {
        if (nodes[heap[i]].freq < nodes[heap[min_i]].freq)
            min_i = i;
    }
    int result = heap[min_i];
    heap[min_i] = heap[--heap_size];
    return result;
}

static void assign_codes(int node_idx, unsigned int code, int len) {
    if (nodes[node_idx].symbol >= 0) {
        code_len[nodes[node_idx].symbol] = len;
        code_val[nodes[node_idx].symbol] = code;
        return;
    }
    if (nodes[node_idx].left >= 0)
        assign_codes(nodes[node_idx].left, code << 1, len + 1);
    if (nodes[node_idx].right >= 0)
        assign_codes(nodes[node_idx].right, (code << 1) | 1, len + 1);
}

static long benchmark_iteration(const unsigned char *data, int data_len) {
    int freq[MAX_SYMBOLS];
    int i;
    long total_bits = 0;

    memset(freq, 0, sizeof(freq));
    for (i = 0; i < data_len; i++)
        freq[data[i]]++;

    node_count = 0;
    heap_size = 0;
    memset(code_len, 0, sizeof(code_len));
    memset(code_val, 0, sizeof(code_val));

    int active = 0;
    for (i = 0; i < MAX_SYMBOLS; i++) {
        if (freq[i] > 0) {
            int n = new_node(freq[i], i, -1, -1);
            heap_push(n);
            active++;
        }
    }

    if (active < 2) {
        // Degenerate case
        if (active == 1) {
            int n = heap_pop();
            code_len[nodes[n].symbol] = 1;
            code_val[nodes[n].symbol] = 0;
        }
    } else {
        while (heap_size > 1) {
            int a = heap_pop();
            int b = heap_pop();
            int parent = new_node(nodes[a].freq + nodes[b].freq, -1, a, b);
            heap_push(parent);
        }
        int root = heap_pop();
        assign_codes(root, 0, 0);
    }

    // Compute total encoded size
    for (i = 0; i < data_len; i++)
        total_bits += code_len[data[i]];

    return total_bits;
}

int main(void) {
    unsigned char data[4096];
    int i;
    long total = 0;
    int state = 42;

    for (int iter = 0; iter < 50000; iter++) {
        // Generate pseudo-random data with varying distribution
        for (i = 0; i < 4096; i++) {
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF;
            // Bias toward lower values to make Huffman more interesting
            int v = state % 256;
            v = (v * v) >> 8;
            data[i] = (unsigned char)v;
        }
        total += benchmark_iteration(data, 4096);
    }

    return (int)((total >> 8) % 256);
}
