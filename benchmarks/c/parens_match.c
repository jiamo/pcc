// Parentheses matching / expression evaluation benchmark
// Stresses: stack operations, character processing, recursive descent parsing

#define EXPR_SIZE 256
#define NUM_EXPRESSIONS 2000000

static char expr[EXPR_SIZE];
static int pos;

static long parse_expr(void);
static long parse_term(void);
static long parse_factor(void);

static long parse_expr(void) {
    long result = parse_term();
    while (expr[pos] == '+' || expr[pos] == '-') {
        char op = expr[pos++];
        long right = parse_term();
        if (op == '+') result += right;
        else result -= right;
    }
    return result;
}

static long parse_term(void) {
    long result = parse_factor();
    while (expr[pos] == '*' || expr[pos] == '/') {
        char op = expr[pos++];
        long right = parse_factor();
        if (op == '*') result *= right;
        else if (right != 0) result /= right;
    }
    return result;
}

static long parse_factor(void) {
    if (expr[pos] == '(') {
        pos++;
        long result = parse_expr();
        if (expr[pos] == ')') pos++;
        return result;
    }
    // Parse number
    long num = 0;
    int neg = 0;
    if (expr[pos] == '-') { neg = 1; pos++; }
    while (expr[pos] >= '0' && expr[pos] <= '9') {
        num = num * 10 + (expr[pos] - '0');
        pos++;
    }
    return neg ? -num : num;
}

// Generate a random valid expression
static int gen_expr(unsigned int *seed, int depth, int max_pos) {
    int p = 0;
    if (depth > 5 || max_pos < 10) {
        // Generate a number
        *seed = *seed * 1103515245u + 12345u;
        int num = (*seed >> 16) % 100;
        if (num >= 100) num = 99;
        if (num >= 10) {
            expr[max_pos - (p + 2)] = '0'; // placeholder
            expr[p++] = '0' + num / 10;
        }
        expr[p++] = '0' + num % 10;
        return p;
    }

    *seed = *seed * 1103515245u + 12345u;
    int choice = (*seed >> 16) % 5;

    if (choice == 0 && max_pos > 20) {
        // Parenthesized expression
        expr[p++] = '(';
        p += gen_expr(seed, depth + 1, max_pos - p - 1);
        *seed = *seed * 1103515245u + 12345u;
        char ops[] = "+-*/";
        expr[p++] = ops[(*seed >> 16) % 4];
        p += gen_expr(seed, depth + 1, max_pos - p - 1);
        expr[p++] = ')';
    } else {
        // Number op number
        *seed = *seed * 1103515245u + 12345u;
        int num = (*seed >> 16) % 99 + 1;
        if (num >= 10) expr[p++] = '0' + num / 10;
        expr[p++] = '0' + num % 10;

        if (max_pos - p > 5) {
            *seed = *seed * 1103515245u + 12345u;
            char ops[] = "+-*/";
            expr[p++] = ops[(*seed >> 16) % 4];
            p += gen_expr(seed, depth + 1, max_pos - p);
        }
    }
    return p;
}

int main(void) {
    unsigned int seed = 8675309;
    long total = 0;

    for (int iter = 0; iter < NUM_EXPRESSIONS; iter++) {
        // Build a simple random expression
        int len = 0;
        seed = seed * 1103515245u + 12345u;
        int a = (seed >> 16) % 100 + 1;
        seed = seed * 1103515245u + 12345u;
        int b = (seed >> 16) % 100 + 1;
        seed = seed * 1103515245u + 12345u;
        int c = (seed >> 16) % 100 + 1;
        seed = seed * 1103515245u + 12345u;
        char ops[] = "+-*/";
        char op1 = ops[(seed >> 16) % 4];
        seed = seed * 1103515245u + 12345u;
        char op2 = ops[(seed >> 16) % 4];

        // Format: (a op1 b) op2 c
        expr[len++] = '(';
        if (a >= 10) expr[len++] = '0' + a / 10;
        expr[len++] = '0' + a % 10;
        expr[len++] = op1;
        if (b >= 10) expr[len++] = '0' + b / 10;
        expr[len++] = '0' + b % 10;
        expr[len++] = ')';
        expr[len++] = op2;
        if (c >= 10) expr[len++] = '0' + c / 10;
        expr[len++] = '0' + c % 10;
        expr[len] = '\0';

        pos = 0;
        total += parse_expr();
    }

    long result = total;
    if (result < 0) result = -result;
    return (int)(result % 256);
}
