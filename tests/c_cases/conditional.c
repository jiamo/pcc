// EXPECT: 42
#define USE_FAST_PATH

#ifdef USE_FAST_PATH
int compute(int x) {
    return x * 2;
}
#else
int compute(int x) {
    int i;
    int result = 0;
    for (i = 0; i < 2; i++) result += x;
    return result;
}
#endif

int main() {
    return compute(21);
}
