// Tower of Hanoi benchmark
// Stresses: deep recursion, function call overhead, minimal computation per call

static long move_count;

static void hanoi(int n, int from, int to, int via) {
    if (n == 0) return;
    hanoi(n - 1, from, via, to);
    move_count++;
    hanoi(n - 1, via, to, from);
}

int main(void) {
    move_count = 0;
    hanoi(28, 0, 2, 1); // 2^28 - 1 = 268435455 moves
    return (int)(move_count % 256);
}
