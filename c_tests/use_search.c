// EXPECT: 21
#include "search.h"

int main() {
    int arr[10] = {2, 5, 8, 12, 16, 23, 38, 56, 72, 91};
    int a = binary_search(arr, 10, 23);     // 5
    int b = binary_search(arr, 10, 91);     // 9
    int c = linear_search(arr, 10, 56);     // 7
    int d = binary_search(arr, 10, 99) + 1; // -1 + 1 = 0
    return a + b + c + d;                   // 21
}
