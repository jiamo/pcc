// Quicksort benchmark
// Stresses: recursion, partition logic, cache access patterns, branch prediction

#define N 1000000

static int arr[N];

static void swap(int *a, int *b) {
    int t = *a; *a = *b; *b = t;
}

static int partition(int *a, int lo, int hi) {
    // Median-of-three pivot
    int mid = lo + (hi - lo) / 2;
    if (a[mid] < a[lo]) swap(&a[mid], &a[lo]);
    if (a[hi] < a[lo]) swap(&a[hi], &a[lo]);
    if (a[mid] < a[hi]) swap(&a[mid], &a[hi]);
    int pivot = a[hi];

    int i = lo - 1;
    int j;
    for (j = lo; j < hi; j++) {
        if (a[j] <= pivot) {
            i++;
            swap(&a[i], &a[j]);
        }
    }
    swap(&a[i + 1], &a[hi]);
    return i + 1;
}

static void quicksort(int *a, int lo, int hi) {
    while (lo < hi) {
        int p = partition(a, lo, hi);
        // Tail call optimization: recurse on smaller partition
        if (p - lo < hi - p) {
            quicksort(a, lo, p - 1);
            lo = p + 1;
        } else {
            quicksort(a, p + 1, hi);
            hi = p - 1;
        }
    }
}

int main(void) {
    int i;
    long checksum = 0;

    // Initialize with pseudo-random data
    unsigned int seed = 7654321;
    for (i = 0; i < N; i++) {
        seed = seed * 1664525u + 1013904223u;
        arr[i] = (int)(seed >> 4);
    }

    quicksort(arr, 0, N - 1);

    // Verify and checksum
    for (i = 0; i < N; i++)
        checksum += (long)arr[i] * (i & 0xFF);

    return (int)((checksum >> 16) % 256);
}
