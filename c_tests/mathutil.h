int square(int x) {
    return x * x;
}

int cube(int x) {
    return x * x * x;
}

int power(int base, int exp) {
    int result = 1;
    int i;
    for (i = 0; i < exp; i++)
        result *= base;
    return result;
}

int gcd(int a, int b) {
    while (b != 0) {
        int t = b;
        b = a % b;
        a = t;
    }
    return a;
}

int lcm(int a, int b) {
    return a / gcd(a, b) * b;
}
