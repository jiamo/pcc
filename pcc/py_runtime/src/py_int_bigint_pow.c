/* pcc/py_runtime/src/py_int_bigint_pow.c
 *
 * Bignum exponentiation split from py_int.c. The actual multiplication
 * helper remains in py_int.c because it needs full unsigned 64-bit digit
 * products.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>

static PyIntObject *bigint_copy(const PyIntObject *a) {
    PyIntObject *r = py_bigint_alloc(a->ndigits);
    if (r == NULL) return NULL;
    r->sign = a->sign;
    for (int32_t i = 0; i < a->ndigits; i++) r->digits[i] = a->digits[i];
    return r;
}

PyIntObject *py_bigint_pow(const PyIntObject *base, const PyIntObject *exp) {
    if (exp->sign < 0) return NULL;
    if (exp->sign == 0) return py_bigint_from_i64(1);

    /* Find the position of the top set bit across the whole exponent. */
    int32_t top_digit = exp->ndigits - 1;
    uint32_t top = exp->digits[top_digit];
    int top_bit = 31;
    while (top_bit >= 0 && (top & (1u << top_bit)) == 0) top_bit--;
    /* exp->sign != 0 guarantees at least one bit is set. */

    /* Square-and-multiply from MSB to LSB. Start with result = base. */
    PyIntObject *result = bigint_copy(base);
    if (result == NULL) return NULL;

    /* Walk remaining bits from (top_digit, top_bit - 1) downward to
     * (0, 0) inclusive. */
    int32_t di = top_digit;
    int bit = top_bit - 1;
    while (di >= 0) {
        while (bit >= 0) {
            /* result = result * result */
            PyIntObject *sq = py_bigint_mul(result, result);
            free(result);
            if (sq == NULL) return NULL;
            result = sq;
            /* If exponent bit is set, multiply by base. */
            if (exp->digits[di] & (1u << bit)) {
                PyIntObject *m = py_bigint_mul(result, base);
                free(result);
                if (m == NULL) return NULL;
                result = m;
            }
            bit--;
        }
        di--;
        bit = 31;
    }
    return result;
}
