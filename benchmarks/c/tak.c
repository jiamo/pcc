// Takeuchi (TAK) function benchmark
// Stresses: deep recursion, function call overhead, conditional branches
// Classic Lisp/AI benchmark by Ikuo Takeuchi

static int tak(int x, int y, int z) {
    if (y >= x) return z;
    return tak(tak(x - 1, y, z),
               tak(y - 1, z, x),
               tak(z - 1, x, y));
}

int main(void) {
    int result = 0;
    int i;
    // Run multiple times for sufficient runtime
    for (i = 0; i < 500; i++) {
        result += tak(24, 16, 8);
    }
    return result % 256;
}
