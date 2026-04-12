// Conway's Game of Life benchmark
// Stresses: 2D array access, neighbor counting, conditional updates, cache patterns

#define ROWS 512
#define COLS 512
#define GENERATIONS 500

static unsigned char grid[ROWS][COLS];
static unsigned char next_grid[ROWS][COLS];

static int count_neighbors(int r, int c) {
    int count = 0;
    int dr, dc;
    for (dr = -1; dr <= 1; dr++) {
        for (dc = -1; dc <= 1; dc++) {
            if (dr == 0 && dc == 0) continue;
            int nr = (r + dr + ROWS) % ROWS;
            int nc = (c + dc + COLS) % COLS;
            count += grid[nr][nc];
        }
    }
    return count;
}

int main(void) {
    int r, c, gen;
    unsigned int seed = 3141592;

    // Initialize with pseudo-random pattern (~30% alive)
    for (r = 0; r < ROWS; r++) {
        for (c = 0; c < COLS; c++) {
            seed = seed * 1103515245u + 12345u;
            grid[r][c] = ((seed >> 16) % 100 < 30) ? 1 : 0;
        }
    }

    // Run generations
    for (gen = 0; gen < GENERATIONS; gen++) {
        for (r = 0; r < ROWS; r++) {
            for (c = 0; c < COLS; c++) {
                int n = count_neighbors(r, c);
                if (grid[r][c]) {
                    next_grid[r][c] = (n == 2 || n == 3) ? 1 : 0;
                } else {
                    next_grid[r][c] = (n == 3) ? 1 : 0;
                }
            }
        }
        // Swap grids
        for (r = 0; r < ROWS; r++)
            for (c = 0; c < COLS; c++)
                grid[r][c] = next_grid[r][c];
    }

    // Count live cells
    long alive = 0;
    for (r = 0; r < ROWS; r++)
        for (c = 0; c < COLS; c++)
            alive += grid[r][c];

    return (int)(alive % 256);
}
