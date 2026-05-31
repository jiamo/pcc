// EXPECT: 14
#include "stack.h"

void op_add();
void op_sub();
void op_mul();
void op_div();

int main() {
    // Calculate: (3 + 4) * 2 = 14
    push(3);
    push(4);
    op_add();       // stack: [7]
    push(2);
    op_mul();       // stack: [14]
    return pop();   // 14
}
