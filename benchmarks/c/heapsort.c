// Heapsort benchmark
// Stresses: array access patterns, comparison-based sorting, cache behavior

#define N 500000

static int arr[N];

static void sift_down(int *a, int start, int end) {
    int root = start;
    while (root * 2 + 1 <= end) {
        int child = root * 2 + 1;
        int swap = root;
        if (a[swap] < a[child])
            swap = child;
        if (child + 1 <= end && a[swap] < a[child + 1])
            swap = child + 1;
        if (swap == root)
            return;
        int tmp = a[root];
        a[root] = a[swap];
        a[swap] = tmp;
        root = swap;
    }
}

static void heapsort(int *a, int count) {
    int start, end;

    // Build heap
    for (start = (count - 2) / 2; start >= 0; start--)
        sift_down(a, start, count - 1);

    // Extract elements
    for (end = count - 1; end > 0; end--) {
        int tmp = a[end];
        a[end] = a[0];
        a[0] = tmp;
        sift_down(a, 0, end - 1);
    }
}

int main(void) {
    int i;
    long checksum = 0;

    // Initialize with pseudo-random data
    unsigned int seed = 12345;
    for (i = 0; i < N; i++) {
        seed = seed * 1664525u + 1013904223u;
        arr[i] = (int)(seed >> 4);
    }

    heapsort(arr, N);

    // Verify sorted and compute checksum
    for (i = 0; i < N; i++)
        checksum += (long)arr[i] * (i & 0xFF);

    return (int)((checksum >> 16) % 256);
}
