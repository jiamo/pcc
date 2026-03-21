// EXPECT: 1073
#include "mathutil.h"

int main() {
    int a = square(5);      // 25
    int b = cube(4);        // 64
    int c = power(2, 10);   // 1024
    int d = gcd(48, 18);    // 6
    int e = lcm(12, 8);     // 24
    return c + a + e;       // 1024 + 25 + 24 = 1073
}
