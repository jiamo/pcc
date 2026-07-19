#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
    double x;
    double y;
} Sample;

static double hot(const Sample values[static 2], int64_t rounds) {
    double total = 0.25;
    for (int64_t i = 0; i < rounds; i++) {
        const Sample left = values[0];
        const Sample right = values[1];
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
        total = (total + left.x) * right.y - left.y;
        total = (total + right.x) * left.y - right.y;
    }
    return total;
}

int main(void) {
    const Sample values[2] = {{0.125, 0.5}, {0.25, 0.75}};
    printf("%.17g\n", hot(values, INT64_C(1000000)));
    return 0;
}
