// Pi digit computation using Machin's formula with fixed-point arithmetic
// Stresses: integer arithmetic, large number multiplication, carries

// Compute pi using: pi/4 = 4*arctan(1/5) - arctan(1/239)
// Using fixed-point arithmetic with a large radix

#define NDIGITS 2000
#define ARRAY_SIZE (NDIGITS / 4 + 16)

static int pi_digits[ARRAY_SIZE];
static int tmp[ARRAY_SIZE];
static int result[ARRAY_SIZE];

static void zero(int *a, int n) {
    int i;
    for (i = 0; i < n; i++) a[i] = 0;
}

static void assign(int *dst, const int *src, int n) {
    int i;
    for (i = 0; i < n; i++) dst[i] = src[i];
}

static void div_int(int *a, int n, int d) {
    long carry = 0;
    int i;
    for (i = 0; i < n; i++) {
        long cur = carry * 10000L + a[i];
        a[i] = (int)(cur / d);
        carry = cur % d;
    }
}

static void add(int *a, const int *b, int n) {
    int carry = 0;
    int i;
    for (i = n - 1; i >= 0; i--) {
        int sum = a[i] + b[i] + carry;
        a[i] = sum % 10000;
        carry = sum / 10000;
    }
}

static void sub(int *a, const int *b, int n) {
    int borrow = 0;
    int i;
    for (i = n - 1; i >= 0; i--) {
        int diff = a[i] - b[i] - borrow;
        if (diff < 0) {
            diff += 10000;
            borrow = 1;
        } else {
            borrow = 0;
        }
        a[i] = diff;
    }
}

static int is_zero(const int *a, int n) {
    int i;
    for (i = 0; i < n; i++)
        if (a[i] != 0) return 0;
    return 1;
}

// Compute arctan(1/x) * 4 and store in result
static void arctan(int x, int n) {
    int power = x;
    int sign = 1;
    int k = 1;

    zero(result, n);
    zero(tmp, n);
    tmp[0] = 1;
    div_int(tmp, n, x); // tmp = 1/x

    while (!is_zero(tmp, n)) {
        if (sign > 0)
            add(result, tmp, n);
        else
            sub(result, tmp, n);

        div_int(tmp, n, x);
        div_int(tmp, n, x);
        k += 2;
        // Divide by (2k-1)/(2k-3) effectively
        // Actually: next term = prev_term / (x^2) / ((2k+1)/(2k-1))
        // Simpler: divide tmp by x^2 already done, now divide by (2k-1)
        // But we need to track the divisor correctly
        // arctan(1/x) = 1/x - 1/(3x^3) + 1/(5x^5) - ...
        // After dividing by x^2, divide by k to get next term
        {
            // We already divided by x twice, now divide by k
            int old_k = k;
            // Undo wrong division: we need term_n = term_{n-1} * (k-2) / (k * x^2)
            // Actually let's just be correct:
            // tmp was divided by x^2. But we need to also adjust for k/(k-2)
        }
        sign = -sign;
    }
}

// Simplified: use Leibniz-Machin with manual long division
static void compute_pi(int n) {
    int i, k, sign;

    // Use: pi/4 = 4*arctan(1/5) - arctan(1/239) (Machin's formula)
    // arctan(1/x) = sum_{k=0}^inf (-1)^k / ((2k+1) * x^(2k+1))

    zero(pi_digits, n);

    // 16 * arctan(1/5)
    zero(tmp, n);
    tmp[0] = 16;
    div_int(tmp, n, 5);
    for (k = 0; ; k++) {
        int divisor = 2 * k + 1;
        zero(result, n);
        assign(result, tmp, n);
        div_int(result, n, divisor);

        if (is_zero(result, n)) break;

        if (k % 2 == 0)
            add(pi_digits, result, n);
        else
            sub(pi_digits, result, n);

        div_int(tmp, n, 25); // divide by 5^2
    }

    // 4 * arctan(1/239)
    zero(tmp, n);
    tmp[0] = 4;
    div_int(tmp, n, 239);
    for (k = 0; ; k++) {
        int divisor = 2 * k + 1;
        zero(result, n);
        assign(result, tmp, n);
        div_int(result, n, divisor);

        if (is_zero(result, n)) break;

        if (k % 2 == 0)
            sub(pi_digits, result, n);
        else
            add(pi_digits, result, n);

        div_int(tmp, n, 57121); // divide by 239^2
    }
}

int main(void) {
    int n = ARRAY_SIZE;
    int i;

    compute_pi(n);

    // Checksum over computed digits
    long check = 0;
    for (i = 0; i < n; i++)
        check += (long)pi_digits[i] * (i + 1);

    return (int)(check % 256);
}
