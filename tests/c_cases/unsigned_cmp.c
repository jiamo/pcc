// EXPECT: 0
/* Test unsigned integer comparisons */
int main() {
    int errors = 0;
    unsigned int a = 3;
    unsigned int big = (1u << 31);  /* 2147483648 */

    /* 3 < 2147483648 unsigned */
    if (a > big) errors++;

    /* Unsigned overflow wrap */
    unsigned int x = 0;
    unsigned int y = x - 1;  /* UINT_MAX = 4294967295 */
    if (y < x) errors++;     /* should be false: UINT_MAX > 0 */

    return errors;
}
