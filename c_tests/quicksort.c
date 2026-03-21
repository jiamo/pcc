// EXPECT: 83
void swap(int *a, int *b) {
    int t = *a; *a = *b; *b = t;
}

int partition(int *a, int lo, int hi) {
    int pivot = a[hi];
    int i = lo - 1;
    int j;
    for (j = lo; j < hi; j++) {
        if (a[j] <= pivot) {
            i++;
            swap(a + i, a + j);
        }
    }
    swap(a + i + 1, a + hi);
    return i + 1;
}

void qs(int *a, int lo, int hi) {
    if (lo < hi) {
        int p = partition(a, lo, hi);
        qs(a, lo, p - 1);
        qs(a, p + 1, hi);
    }
}

int main() {
    int a[8] = {38, 27, 43, 3, 9, 82, 10, 1};
    qs(a, 0, 7);
    // sorted: 1 3 9 10 27 38 43 82
    return a[0] + a[7];
}
