// EXPECT: 31
int moves = 0;

void hanoi(int n, int from, int to, int aux) {
    if (n == 1) {
        moves++;
        return;
    }
    hanoi(n - 1, from, aux, to);
    moves++;
    hanoi(n - 1, aux, to, from);
}

int main() {
    hanoi(5, 1, 3, 2);
    return moves;
}
