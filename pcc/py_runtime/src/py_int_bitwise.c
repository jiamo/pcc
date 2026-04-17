/* pcc/py_runtime/src/py_int_bitwise.c
 *
 * Bignum bitwise helpers split from py_int.c for independent
 * pcc-Python replacement.
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

enum BitOp { BIT_AND, BIT_OR, BIT_XOR };

static PyIntObject *bigint_bitop(const PyIntObject *a, const PyIntObject *b,
                                 enum BitOp op) {
    int32_t na = a->ndigits, nb = b->ndigits;
    int32_t n = (na > nb ? na : nb) + 1;    /* extra sign-extension digit */
    uint32_t *sa = (uint32_t *)calloc((size_t)n, sizeof(uint32_t));
    uint32_t *sb = (uint32_t *)calloc((size_t)n, sizeof(uint32_t));
    if (!sa || !sb) { free(sa); free(sb); return NULL; }

    /* Fill sa with two's-complement representation of a. */
    if (a->sign >= 0) {
        for (int32_t i = 0; i < na; i++) sa[i] = a->digits[i];
    } else {
        uint64_t borrow = 0;
        for (int32_t i = 0; i < n; i++) {
            uint32_t d = (i < na) ? a->digits[i] : 0;
            /* two's complement: ~d + (i==0 ? 1 : 0) with carry */
            uint64_t inv = (uint32_t)~d;
            if (i == 0) inv += 1;
            inv += borrow;
            sa[i] = (uint32_t)(inv & 0xFFFFFFFFu);
            borrow = inv >> 32;
        }
    }
    if (b->sign >= 0) {
        for (int32_t i = 0; i < nb; i++) sb[i] = b->digits[i];
    } else {
        uint64_t borrow = 0;
        for (int32_t i = 0; i < n; i++) {
            uint32_t d = (i < nb) ? b->digits[i] : 0;
            uint64_t inv = (uint32_t)~d;
            if (i == 0) inv += 1;
            inv += borrow;
            sb[i] = (uint32_t)(inv & 0xFFFFFFFFu);
            borrow = inv >> 32;
        }
    }

    uint32_t *sr = (uint32_t *)calloc((size_t)n, sizeof(uint32_t));
    if (!sr) { free(sa); free(sb); return NULL; }

    for (int32_t i = 0; i < n; i++) {
        switch (op) {
            case BIT_AND: sr[i] = sa[i] & sb[i]; break;
            case BIT_OR:  sr[i] = sa[i] | sb[i]; break;
            case BIT_XOR: sr[i] = sa[i] ^ sb[i]; break;
        }
    }
    free(sa);
    free(sb);

    /* Decode result: sign is determined by the top bit of sr[n-1]. */
    int result_sign = (sr[n - 1] & 0x80000000u) ? -1 : 1;
    PyIntObject *r = py_bigint_alloc(n);
    if (r == NULL) { free(sr); return NULL; }

    if (result_sign >= 0) {
        for (int32_t i = 0; i < n; i++) r->digits[i] = sr[i];
        r->sign = 1;
    } else {
        /* magnitude = 2^(32n) - sr == ~sr + 1 */
        uint64_t carry = 1;
        for (int32_t i = 0; i < n; i++) {
            uint64_t inv = (uint32_t)~sr[i];
            inv += carry;
            r->digits[i] = (uint32_t)(inv & 0xFFFFFFFFu);
            carry = inv >> 32;
        }
        r->sign = -1;
    }
    free(sr);
    py_bigint_normalize(r);
    return r;
}

PyIntObject *py_bigint_and(const PyIntObject *a, const PyIntObject *b) { return bigint_bitop(a, b, BIT_AND); }
PyIntObject *py_bigint_or (const PyIntObject *a, const PyIntObject *b) { return bigint_bitop(a, b, BIT_OR);  }
PyIntObject *py_bigint_xor(const PyIntObject *a, const PyIntObject *b) { return bigint_bitop(a, b, BIT_XOR); }
