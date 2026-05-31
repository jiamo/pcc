#include "stack.h"

void op_add() {
    int b = pop();
    int a = pop();
    push(a + b);
}

void op_sub() {
    int b = pop();
    int a = pop();
    push(a - b);
}

void op_mul() {
    int b = pop();
    int a = pop();
    push(a * b);
}

void op_div() {
    int b = pop();
    int a = pop();
    push(a / b);
}
