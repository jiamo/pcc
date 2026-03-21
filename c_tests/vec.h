struct Vec2 {
    int x;
    int y;
};

void vec2_init(int *px, int *py, int x, int y) {
    *px = x;
    *py = y;
}

int vec2_dot(int x1, int y1, int x2, int y2) {
    return x1 * x2 + y1 * y2;
}

int vec2_len_sq(int x, int y) {
    return x * x + y * y;
}
