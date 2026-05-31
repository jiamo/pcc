int binary_search(int *arr, int n, int target) {
    int lo = 0;
    int hi = n - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}

int linear_search(int *arr, int n, int target) {
    int i;
    for (i = 0; i < n; i++)
        if (arr[i] == target) return i;
    return -1;
}
