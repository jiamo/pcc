// Matrix multiplication 200x200
#include <stdio.h>

int a[200][200];
int b[200][200];
int c[200][200];

int main() {
    int i, j, k;
    int n = 200;
    // Initialize
    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++) {
            a[i][j] = i + j;
            b[i][j] = i - j;
            c[i][j] = 0;
        }
    // Multiply
    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++)
            for (k = 0; k < n; k++)
                c[i][j] += a[i][k] * b[k][j];
    printf("%d\n", c[99][99]);
    return 0;
}
