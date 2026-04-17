/* pcc/py_runtime/src/py_int_shift.c
 *
 * Bignum shift helpers split from py_int.c for independent pcc-Python
 * replacement.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>

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

static PyIntObject *bigint_shl_digits_and_bits(const PyIntObject *a,
                                               uint64_t ndigits_shift,
                                               unsigned bit_shift) {
    if (a->sign == 0) return py_bigint_alloc(0);
    int32_t new_len = (int32_t)(a->ndigits + ndigits_shift + 1);
    PyIntObject *r = py_bigint_alloc(new_len);
    if (r == NULL) return NULL;
    uint32_t carry = 0;
    for (int32_t i = 0; i < a->ndigits; i++) {
        uint64_t cur = ((uint64_t)a->digits[i] << bit_shift) | carry;
        r->digits[i + (int32_t)ndigits_shift] = (uint32_t)(cur & 0xFFFFFFFFu);
        carry = (uint32_t)(cur >> 32);
    }
    r->digits[a->ndigits + (int32_t)ndigits_shift] = carry;
    r->sign = a->sign;
    py_bigint_normalize(r);
    return r;
}

PyIntObject *py_bigint_shl(const PyIntObject *a, uint64_t bits) {
    if (a->sign == 0 || bits == 0) return bigint_copy(a);
    uint64_t nd = bits / 32;
    unsigned nb = (unsigned)(bits % 32);
    if (nb == 0) {
        /* Pure digit shift. */
        int32_t new_len = (int32_t)(a->ndigits + nd);
        PyIntObject *r = py_bigint_alloc(new_len);
        if (r == NULL) return NULL;
        for (int32_t i = 0; i < a->ndigits; i++) {
            r->digits[i + (int32_t)nd] = a->digits[i];
        }
        r->sign = a->sign;
        py_bigint_normalize(r);
        return r;
    }
    return bigint_shl_digits_and_bits(a, nd, nb);
}

PyIntObject *py_bigint_shr(const PyIntObject *a, uint64_t bits) {
    /* Python: negative >> k = floor(a / 2^k). We approximate by doing the
     * magnitude shift, then adjust sign for negatives (round toward -inf).
     * For sign = +1: plain magnitude right shift.
     * For sign = -1: compute q = (|a| + 2^k - 1) >> k; result = -q.
     */
    if (a->sign == 0) return py_bigint_alloc(0);
    uint64_t nd = bits / 32;
    unsigned nb = (unsigned)(bits % 32);

    if ((int64_t)nd >= a->ndigits) {
        if (a->sign < 0) {
            /* floor(negative / big) = -1 (if any bits were nonzero). */
            PyIntObject *r = py_bigint_from_i64(-1);
            return r;
        }
        return py_bigint_alloc(0);
    }

    /* Build |a| shifted. */
    int32_t new_len = a->ndigits - (int32_t)nd;
    PyIntObject *mag = py_bigint_alloc(new_len);
    if (mag == NULL) return NULL;
    for (int32_t i = 0; i < new_len; i++) {
        uint64_t low  = (uint64_t)a->digits[i + (int32_t)nd];
        uint64_t high = (i + (int32_t)nd + 1 < a->ndigits)
                      ? (uint64_t)a->digits[i + (int32_t)nd + 1]
                      : 0;
        uint64_t cur;
        if (nb == 0) cur = low;
        else cur = (low >> nb) | (high << (32 - nb));
        mag->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
    }
    mag->sign = 1;
    py_bigint_normalize(mag);

    if (a->sign > 0) return mag;

    /* Negative: check if any shifted-out bits were nonzero. */
    bool tail_nonzero = false;
    if (nb != 0) {
        /* low bits of a->digits[nd] that were chopped */
        uint32_t mask = (1u << nb) - 1u;
        if ((a->digits[nd] & mask) != 0) tail_nonzero = true;
    }
    for (int32_t i = 0; i < (int32_t)nd && !tail_nonzero; i++) {
        if (a->digits[i] != 0) tail_nonzero = true;
    }
    if (tail_nonzero) {
        /* mag += 1 */
        uint32_t carry = 1;
        for (int32_t i = 0; i < mag->ndigits && carry; i++) {
            uint64_t cur = (uint64_t)mag->digits[i] + carry;
            mag->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = (uint32_t)(cur >> 32);
        }
        if (carry) {
            /* grow by one digit */
            PyIntObject *grow = py_bigint_alloc(mag->ndigits + 1);
            if (grow == NULL) { free(mag); return NULL; }
            for (int32_t i = 0; i < mag->ndigits; i++) grow->digits[i] = mag->digits[i];
            grow->digits[mag->ndigits] = carry;
            grow->sign = 1;
            py_bigint_normalize(grow);
            free(mag);
            mag = grow;
        }
    }
    mag->sign = -1;
    py_bigint_normalize(mag);
    return mag;
}
