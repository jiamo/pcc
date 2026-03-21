#define STACK_MAX 64

int stack_data[STACK_MAX];
int stack_top = -1;

void stack_push(int val) {
    stack_top++;
    stack_data[stack_top] = val;
}

int stack_pop() {
    int val = stack_data[stack_top];
    stack_top--;
    return val;
}

int stack_peek() {
    return stack_data[stack_top];
}

int stack_empty() {
    return stack_top < 0;
}

int stack_size() {
    return stack_top + 1;
}
