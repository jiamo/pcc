// EXPECT: 15
#include "stack.h"

int main() {
    int i;
    for (i = 1; i <= 5; i++)
        stack_push(i);

    int sum = 0;
    while (!stack_empty())
        sum += stack_pop();

    return sum;    // 5+4+3+2+1 = 15
}
