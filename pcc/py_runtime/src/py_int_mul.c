/* pcc/py_runtime/src/py_int_mul.c
 *
 * Bignum schoolbook multiplication split out from py_int.c so the
 * pcc-Python runtime archive can replace it independently.
 */

#include "py_internal.h"
#include <stdint.h>

static void py_bigint_normalize(PyIntObject *b) {
    while (b->ndigits > 0 && b->digits[b->ndigits - 1] == 0) {
        b->ndigits--;
    }
    if (b->ndigits == 0) b->sign = 0;
    else if (b->sign == 0) b->sign = 1;
}

PyIntObject *py_bigint_mul(const PyIntObject *a, const PyIntObject *b) {
    if (a->sign == 0 || b->sign == 0) return py_bigint_alloc(0);
    int32_t lr = a->ndigits + b->ndigits;
    PyIntObject *r = py_bigint_alloc(lr);
    if (r == NULL) return NULL;
    for (int32_t i = 0; i < a->ndigits; i++) {
        uint64_t carry = 0;
        uint64_t av = a->digits[i];
        for (int32_t j = 0; j < b->ndigits; j++) {
            uint64_t cur = (uint64_t)r->digits[i + j]
                         + av * (uint64_t)b->digits[j]
                         + carry;
            r->digits[i + j] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        r->digits[i + b->ndigits] += (uint32_t)carry;
    }
    r->sign = (a->sign == b->sign) ? 1 : -1;
    py_bigint_normalize(r);
    return r;
}
