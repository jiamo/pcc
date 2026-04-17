/* pcc/py_runtime/src/py_int_addsub.c
 *
 * Bignum add/sub split from py_int.c for independent pcc-Python
 * replacement. Multiplication stays in py_int.c for now because its
 * uint32*uint32 intermediate needs full unsigned 64-bit behavior.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>

static void py_bigint_normalize(PyIntObject *b) {
    while (b->ndigits > 0 && b->digits[b->ndigits - 1] == 0) {
        b->ndigits--;
    }
    if (b->ndigits == 0) b->sign = 0;
    else if (b->sign == 0) b->sign = 1;
}

static PyIntObject *bigint_copy(const PyIntObject *a) {
    PyIntObject *r = py_bigint_alloc(a->ndigits);
    if (r == NULL) return NULL;
    r->sign = a->sign;
    for (int32_t i = 0; i < a->ndigits; i++) r->digits[i] = a->digits[i];
    return r;
}

static int bigint_abs_cmp(const PyIntObject *a, const PyIntObject *b) {
    if (a->ndigits != b->ndigits) return a->ndigits < b->ndigits ? -1 : 1;
    for (int32_t i = a->ndigits - 1; i >= 0; i--) {
        if (a->digits[i] != b->digits[i]) {
            return a->digits[i] < b->digits[i] ? -1 : 1;
        }
    }
    return 0;
}

/* r = |a| + |b|. Sign of r is `sign_if_nonzero` when the sum is nonzero. */
static PyIntObject *bigint_abs_add(const PyIntObject *a, const PyIntObject *b,
                                   int32_t sign_if_nonzero) {
    int32_t la = a->ndigits, lb = b->ndigits;
    int32_t lr = (la > lb ? la : lb) + 1;
    PyIntObject *r = py_bigint_alloc(lr);
    if (r == NULL) return NULL;
    uint64_t carry = 0;
    for (int32_t i = 0; i < lr; i++) {
        uint64_t av = (i < la) ? a->digits[i] : 0;
        uint64_t bv = (i < lb) ? b->digits[i] : 0;
        uint64_t sum = av + bv + carry;
        r->digits[i] = (uint32_t)(sum & 0xFFFFFFFFu);
        carry = sum >> 32;
    }
    r->sign = sign_if_nonzero;
    py_bigint_normalize(r);
    return r;
}

/* r = |a| - |b|, requires |a| >= |b|. */
static PyIntObject *bigint_abs_sub(const PyIntObject *a, const PyIntObject *b,
                                   int32_t sign_if_nonzero) {
    int32_t la = a->ndigits, lb = b->ndigits;
    PyIntObject *r = py_bigint_alloc(la);
    if (r == NULL) return NULL;
    int64_t borrow = 0;
    for (int32_t i = 0; i < la; i++) {
        int64_t av = (int64_t)(uint64_t)a->digits[i];
        int64_t bv = (i < lb) ? (int64_t)(uint64_t)b->digits[i] : 0;
        int64_t diff = av - bv - borrow;
        if (diff < 0) { diff += ((int64_t)1 << 32); borrow = 1; }
        else          { borrow = 0; }
        r->digits[i] = (uint32_t)(diff & 0xFFFFFFFFu);
    }
    r->sign = sign_if_nonzero;
    py_bigint_normalize(r);
    return r;
}

PyIntObject *py_bigint_add(const PyIntObject *a, const PyIntObject *b) {
    if (a->sign == 0) return bigint_copy(b);
    if (b->sign == 0) return bigint_copy(a);
    if (a->sign == b->sign) return bigint_abs_add(a, b, a->sign);
    /* Opposite signs: subtract smaller magnitude from larger. */
    int c = bigint_abs_cmp(a, b);
    if (c == 0) {
        PyIntObject *r = py_bigint_alloc(0);
        return r;   /* zero */
    }
    if (c > 0) return bigint_abs_sub(a, b, a->sign);
    return bigint_abs_sub(b, a, b->sign);
}

PyIntObject *py_bigint_sub(const PyIntObject *a, const PyIntObject *b) {
    if (b->sign == 0) return bigint_copy(a);
    if (a->sign == 0) {
        PyIntObject *r = bigint_copy(b);
        if (r == NULL) return NULL;
        r->sign = -r->sign;
        return r;
    }
    if (a->sign != b->sign) {
        /* a - b with opposite signs == addition of magnitudes, sign of a. */
        return bigint_abs_add(a, b, a->sign);
    }
    /* Same sign: |a| - |b|. */
    int c = bigint_abs_cmp(a, b);
    if (c == 0) return py_bigint_alloc(0);
    if (c > 0) return bigint_abs_sub(a, b, a->sign);
    return bigint_abs_sub(b, a, -a->sign);
}
