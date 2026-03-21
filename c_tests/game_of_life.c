// EXPECT: 3
// Conway's Game of Life: blinker oscillator
int get(int *g, int r, int c, int n) {
    if (r < 0 || r >= n || c < 0 || c >= n) return 0;
    return g[r * n + c];
}

int neighbors(int *g, int r, int c, int n) {
    int s = 0;
    int dr;
    int dc;
    for (dr = -1; dr <= 1; dr++)
        for (dc = -1; dc <= 1; dc++)
            if (dr != 0 || dc != 0)
                s += get(g, r + dr, c + dc, n);
    return s;
}

int main() {
    int g[25] = {
        0,0,0,0,0,
        0,0,1,0,0,
        0,0,1,0,0,
        0,0,1,0,0,
        0,0,0,0,0
    };
    int next[25];
    int r;
    int c;
    int n = 5;
    for (r = 0; r < n; r++) {
        for (c = 0; c < n; c++) {
            int nb = neighbors(g, r, c, n);
            int alive = g[r * n + c];
            if (alive && (nb == 2 || nb == 3))
                next[r * n + c] = 1;
            else if (!alive && nb == 3)
                next[r * n + c] = 1;
            else
                next[r * n + c] = 0;
        }
    }
    return next[2*5+1] + next[2*5+2] + next[2*5+3];
}
