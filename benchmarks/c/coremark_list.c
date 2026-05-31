// CoreMark-inspired linked list benchmark
// Based on EEMBC CoreMark list processing workload
// Stresses: pointer chasing, insertion, search, comparison, cache misses

#include <string.h>

#define LIST_SIZE 2000
#define ITERATIONS 500

struct ListItem {
    int key;
    int data;
    int next; // index-based pointer
};

static struct ListItem items[LIST_SIZE];
static int head;

static void list_init(unsigned int seed) {
    int i;
    head = 0;
    for (i = 0; i < LIST_SIZE; i++) {
        seed = seed * 1664525u + 1013904223u;
        items[i].key = (int)(seed >> 4) & 0xFFFF;
        items[i].data = (int)(seed >> 12);
        items[i].next = (i + 1 < LIST_SIZE) ? i + 1 : -1;
    }
}

// Insertion sort on linked list
static void list_insert_sort(void) {
    if (head == -1) return;
    int sorted = head;
    int curr = items[head].next;
    items[sorted].next = -1;

    while (curr != -1) {
        int next = items[curr].next;

        if (items[curr].key <= items[sorted].key) {
            items[curr].next = sorted;
            sorted = curr;
        } else {
            int search = sorted;
            while (items[search].next != -1 &&
                   items[items[search].next].key < items[curr].key) {
                search = items[search].next;
            }
            items[curr].next = items[search].next;
            items[search].next = curr;
        }
        curr = next;
    }
    head = sorted;
}

// Reverse the list
static void list_reverse(void) {
    int prev = -1;
    int curr = head;
    while (curr != -1) {
        int next = items[curr].next;
        items[curr].next = prev;
        prev = curr;
        curr = next;
    }
    head = prev;
}

// Find element by key (linear search)
static int list_find(int key) {
    int curr = head;
    while (curr != -1) {
        if (items[curr].key == key) return curr;
        curr = items[curr].next;
    }
    return -1;
}

// Count elements matching a predicate
static int list_count_if(int threshold) {
    int count = 0;
    int curr = head;
    while (curr != -1) {
        if (items[curr].data > threshold) count++;
        curr = items[curr].next;
    }
    return count;
}

// Merge two sorted halves (for merge sort)
static int list_merge(int a, int b) {
    if (a == -1) return b;
    if (b == -1) return a;
    int result;
    if (items[a].key <= items[b].key) {
        result = a;
        items[a].next = list_merge(items[a].next, b);
    } else {
        result = b;
        items[b].next = list_merge(a, items[b].next);
    }
    return result;
}

int main(void) {
    long total = 0;
    unsigned int seed = 12345678;

    for (int iter = 0; iter < ITERATIONS; iter++) {
        seed = seed * 1664525u + 1013904223u;
        list_init(seed);

        // Sort
        list_insert_sort();

        // Search for various keys
        int found = 0;
        int j;
        unsigned int search_seed = seed;
        for (j = 0; j < 100; j++) {
            search_seed = search_seed * 1103515245u + 12345u;
            int key = (int)(search_seed >> 4) & 0xFFFF;
            if (list_find(key) >= 0) found++;
        }

        // Count elements above threshold
        int count = list_count_if(0);

        // Reverse
        list_reverse();

        total += found + count + items[head].key;
    }

    return (int)(total % 256);
}
