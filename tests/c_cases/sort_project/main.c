// EXPECT: 2
#include "sort.h"

int main() {
    int a[5] = {5, 3, 1, 4, 2};
    int b[5] = {9, 7, 5, 3, 1};

    bubble_sort(a, 5);
    selection_sort(b, 5);

    int result = is_sorted(a, 5) + is_sorted(b, 5);
    return result;  // 1 + 1 = 2
}
