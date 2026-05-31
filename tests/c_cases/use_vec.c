// EXPECT: 50
#include "vec.h"

int main() {
    struct Vec2 a;
    struct Vec2 b;
    vec2_init(&a.x, &a.y, 3, 4);
    vec2_init(&b.x, &b.y, 0, 5);
    return vec2_len_sq(a.x, a.y) + vec2_len_sq(b.x, b.y);
    // 25 + 25 = 50
}
