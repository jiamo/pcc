// Floyd-Warshall all-pairs shortest paths benchmark
// Stresses: triple-nested loop, memory access patterns (cache pressure), branch prediction

#define N 512
#define INF 999999999

static int dist[N][N];

int main(void) {
    int i, j, k;
    unsigned int seed = 54321;

    // Initialize distance matrix
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            if (i == j) {
                dist[i][j] = 0;
            } else {
                seed = seed * 1664525u + 1013904223u;
                // ~30% of edges exist
                if ((seed >> 16) % 100 < 30)
                    dist[i][j] = (int)((seed >> 4) % 100) + 1;
                else
                    dist[i][j] = INF;
            }
        }
    }

    // Floyd-Warshall
    for (k = 0; k < N; k++) {
        for (i = 0; i < N; i++) {
            if (dist[i][k] == INF) continue; // optimization
            for (j = 0; j < N; j++) {
                int through_k = dist[i][k] + dist[k][j];
                if (through_k < dist[i][j])
                    dist[i][j] = through_k;
            }
        }
    }

    // Checksum
    long check = 0;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            if (dist[i][j] < INF)
                check += dist[i][j];

    return (int)((check >> 8) % 256);
}
