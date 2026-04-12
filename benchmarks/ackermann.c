// Ackermann function benchmark
// Stresses: deep recursion, function call overhead, stack pressure

static int ackermann(int m, int n) {
    if (m == 0) return n + 1;
    if (n == 0) return ackermann(m - 1, 1);
    return ackermann(m - 1, ackermann(m, n - 1));
}

int main(void) {
    // ack(3,11) = 16381, takes noticeable time
    int result = ackermann(3, 11);
    return result % 256;
}
