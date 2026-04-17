/* pcc/py_runtime/src/py_int.c
 *
 * Tagged-int fast path plus real bignum fallback (Phase 2).
 *
 * Encoding (see py_internal.h):
 *   - Low bit = 1 => tagged int; value = (p as intptr_t) >> 1.
 *   - Low bit = 0 => PyObject* with PY_TYPE_INT header. The heap form is a
 *     sign-magnitude bignum with base 2^32 digits (PyIntObject).
 *
 * Phase-2 scope (this file):
 *   - add / sub / mul / neg / cmp / pow: tagged fast path with overflow
 *     promotion to bignum; bignum schoolbook for the slow path.
 *   - floordiv / mod / truediv: Python semantics (sign-of-divisor for mod,
 *     float result for truediv).
 *   - bitwise ops (and / or / xor) and shifts (shl / shr) on both tagged and
 *     bignum operands.
 *   - bignum <-> decimal string conversion for the printer and for parsing
 *     numeric literals.
 *
 * Any routine that returns PyObject* returns a new reference; callers own
 * it and must py_decref when done.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <math.h>

/* ---- Forward decls ---------------------------------------------------- */

static void        py_bigint_normalize(PyIntObject *b);
static int          bigint_abs_cmp(const PyIntObject *a, const PyIntObject *b);
static uint32_t     bigint_divmod_small_inplace(PyIntObject *a, uint32_t divisor);

/* ---- Allocation helpers ---------------------------------------------- */

/* py_bigint_alloc / py_bigint_from_i64 / py_bigint_to_pyobject,
 * py_bigint_from_any, comparison, and the tagged-int boundary helpers
 * live in py_int_core.c so they can be replaced independently by
 * py_int_core.py. */

static PyIntObject *bigint_copy(const PyIntObject *a) {
    PyIntObject *r = py_bigint_alloc(a->ndigits);
    if (r == NULL) return NULL;
    r->sign = a->sign;
    for (int32_t i = 0; i < a->ndigits; i++) r->digits[i] = a->digits[i];
    return r;
}

/* Strip leading-zero digits; if result is zero force sign=0. */
static void py_bigint_normalize(PyIntObject *b) {
    while (b->ndigits > 0 && b->digits[b->ndigits - 1] == 0) {
        b->ndigits--;
    }
    if (b->ndigits == 0) b->sign = 0;
    else if (b->sign == 0) b->sign = 1;   /* defensive: shouldn't happen */
}

/* ---- Internal helpers ------------------------------------------------- */

/* ---- Public constructors --------------------------------------------- */

/* py_int_to_i64 moved to py_int_convert.c so it can be replaced
 * independently by py_int_convert.py. */

/* ---- Bignum <-> int64 ------------------------------------------------- */

/* py_bigint_to_i64 moved to py_int_bigint_convert.c so it can be
 * replaced independently by py_int_bigint_convert.py. */

/* ---- Magnitude primitives -------------------------------------------- */

/* Compare magnitudes only. */
static int bigint_abs_cmp(const PyIntObject *a, const PyIntObject *b) {
    if (a->ndigits != b->ndigits) return a->ndigits < b->ndigits ? -1 : 1;
    for (int32_t i = a->ndigits - 1; i >= 0; i--) {
        if (a->digits[i] != b->digits[i]) {
            return a->digits[i] < b->digits[i] ? -1 : 1;
        }
    }
    return 0;
}

/* ---- Bignum arithmetic public ---------------------------------------- */
/* Bignum add/sub moved to py_int_addsub.c so they can be replaced
 * independently by py_int_addsub.py. */

/* Bignum multiplication moved to py_int_mul.c so it can be replaced
 * independently by py_int_mul.py. */

/* ---- In-place divmod by a small (32-bit) divisor. --------------------- */
/* Used for decimal output and small-divisor fast path. `a` is mutated to
 * the quotient (magnitude only); returns the remainder. */
static uint32_t bigint_divmod_small_inplace(PyIntObject *a, uint32_t divisor) {
    uint64_t rem = 0;
    for (int32_t i = a->ndigits - 1; i >= 0; i--) {
        uint64_t cur = (rem << 32) | (uint64_t)a->digits[i];
        a->digits[i] = (uint32_t)(cur / divisor);
        rem = cur % divisor;
    }
    while (a->ndigits > 0 && a->digits[a->ndigits - 1] == 0) a->ndigits--;
    if (a->ndigits == 0) a->sign = 0;
    return (uint32_t)rem;
}

/* Decimal conversion moved to py_int_decimal.c so it can be replaced
 * independently by py_int_decimal.py. */

/* Bignum shifts moved to py_int_shift.c so they can be replaced
 * independently by py_int_shift.py. */

/* Bignum bitwise ops moved to py_int_bitwise.c so they can be replaced
 * independently by py_int_bitwise.py. */

/* ---- Divmod (Python floor semantics) --------------------------------- */
/*
 * Classical long division on magnitudes (base 2^32). We normalize the
 * divisor, do Knuth D-ish digit-at-a-time quotient estimation with a
 * fallback correction loop, then translate the magnitude result into
 * Python's floor-division / sign-of-divisor convention.
 */

/* Shift-left helper: compute |m| << bits into an existing buffer with
 * ndigits `n_out` slots. Returns new digit count. */
static int32_t abs_shl_bits_into(const PyIntObject *m, unsigned bits,
                                 uint32_t *out, int32_t n_out) {
    for (int32_t i = 0; i < n_out; i++) out[i] = 0;
    if (bits == 0) {
        for (int32_t i = 0; i < m->ndigits; i++) out[i] = m->digits[i];
        return m->ndigits;
    }
    uint32_t carry = 0;
    int32_t i;
    for (i = 0; i < m->ndigits; i++) {
        uint64_t cur = ((uint64_t)m->digits[i] << bits) | carry;
        out[i] = (uint32_t)(cur & 0xFFFFFFFFu);
        carry = (uint32_t)(cur >> 32);
    }
    out[i] = carry;
    int32_t r = m->ndigits + (carry ? 1 : 0);
    while (r > 0 && out[r - 1] == 0) r--;
    return r;
}

/* In-place right shift of `out` by `bits` (0..31). */
static void abs_shr_bits_inplace(uint32_t *out, int32_t n, unsigned bits) {
    if (bits == 0) return;
    for (int32_t i = 0; i < n; i++) {
        uint32_t low  = out[i] >> bits;
        uint32_t high = (i + 1 < n) ? (out[i + 1] << (32 - bits)) : 0;
        out[i] = low | high;
    }
}

int py_bigint_divmod(const PyIntObject *a, const PyIntObject *b,
                     PyIntObject **q_out, PyIntObject **r_out) {
    if (b->sign == 0) return -1;   /* div by zero */

    /* Truncated-magnitude divmod first. */
    PyIntObject *tq;    /* magnitude quotient */
    PyIntObject *tr;    /* magnitude remainder */

    if (bigint_abs_cmp(a, b) < 0) {
        /* |a| < |b| : quotient magnitude = 0, remainder magnitude = |a| */
        tq = py_bigint_alloc(0);
        tr = bigint_copy(a);
        if (!tq || !tr) { free(tq); free(tr); return -1; }
        tr->sign = (tr->ndigits == 0) ? 0 : 1;
    } else if (b->ndigits == 1) {
        /* Single-digit divisor fast path. */
        tq = bigint_copy(a);
        if (tq == NULL) return -1;
        tq->sign = 1;
        uint32_t rem = bigint_divmod_small_inplace(tq, b->digits[0]);
        py_bigint_normalize(tq);
        tr = py_bigint_from_i64((int64_t)rem);
        if (tr == NULL) { free(tq); return -1; }
    } else {
        /* General case. Knuth Algorithm D. */
        int32_t n = b->ndigits;
        int32_t m = a->ndigits - n;

        /* Normalize: shift so high digit of divisor has MSB set. */
        unsigned shift = 0;
        {
            uint32_t top = b->digits[n - 1];
            while ((top & 0x80000000u) == 0) { top <<= 1; shift++; }
        }

        uint32_t *u = (uint32_t *)calloc((size_t)(a->ndigits + 2), sizeof(uint32_t));
        uint32_t *v = (uint32_t *)calloc((size_t)(n + 1), sizeof(uint32_t));
        uint32_t *q = (uint32_t *)calloc((size_t)(m + 1), sizeof(uint32_t));
        if (!u || !v || !q) { free(u); free(v); free(q); return -1; }

        (void)abs_shl_bits_into(a, shift, u, a->ndigits + 2);
        (void)abs_shl_bits_into(b, shift, v, n + 1);

        /* u has at least a->ndigits + 1 slots now. Top digit may be 0. */
        int32_t j;
        for (j = m; j >= 0; j--) {
            /* qhat = (u[j+n]*B + u[j+n-1]) / v[n-1] */
            uint64_t u_two = ((uint64_t)u[j + n] << 32) | (uint64_t)u[j + n - 1];
            uint64_t qhat = u_two / (uint64_t)v[n - 1];
            uint64_t rhat = u_two % (uint64_t)v[n - 1];
            if (qhat > 0xFFFFFFFFu) qhat = 0xFFFFFFFFu;
            /* Refine qhat: while qhat*v[n-2] > B*rhat + u[j+n-2], decrement. */
            while (qhat > 0 &&
                   (uint64_t)v[n - 2] * qhat >
                   ((rhat << 32) | (uint64_t)u[j + n - 2])) {
                qhat--;
                rhat += v[n - 1];
                if (rhat > 0xFFFFFFFFu) break;
            }

            /* u[j..j+n] -= qhat * v[0..n-1] */
            int64_t borrow = 0;
            uint64_t carry_mul = 0;
            for (int32_t i = 0; i < n; i++) {
                uint64_t prod = qhat * (uint64_t)v[i] + carry_mul;
                carry_mul = prod >> 32;
                int64_t sub = (int64_t)(uint64_t)u[j + i] - (int64_t)(uint32_t)(prod & 0xFFFFFFFFu) - borrow;
                if (sub < 0) { sub += ((int64_t)1 << 32); borrow = 1; }
                else         { borrow = 0; }
                u[j + i] = (uint32_t)(sub & 0xFFFFFFFFu);
            }
            int64_t top = (int64_t)(uint64_t)u[j + n] - (int64_t)carry_mul - borrow;
            if (top < 0) {
                /* qhat was one too large — add back. */
                qhat--;
                uint64_t carry_add = 0;
                for (int32_t i = 0; i < n; i++) {
                    uint64_t s = (uint64_t)u[j + i] + (uint64_t)v[i] + carry_add;
                    u[j + i] = (uint32_t)(s & 0xFFFFFFFFu);
                    carry_add = s >> 32;
                }
                top += carry_add;
            }
            u[j + n] = (uint32_t)(top & 0xFFFFFFFFu);
            q[j] = (uint32_t)qhat;
        }

        /* Quotient magnitude: digits q[0..m]. */
        tq = py_bigint_alloc(m + 1);
        if (tq == NULL) { free(u); free(v); free(q); return -1; }
        for (int32_t i = 0; i <= m; i++) tq->digits[i] = q[i];
        tq->sign = 1;
        py_bigint_normalize(tq);

        /* Remainder magnitude: u[0..n-1], then un-normalize (shift back). */
        abs_shr_bits_inplace(u, n, shift);
        tr = py_bigint_alloc(n);
        if (tr == NULL) { free(u); free(v); free(q); free(tq); return -1; }
        for (int32_t i = 0; i < n; i++) tr->digits[i] = u[i];
        tr->sign = 1;
        py_bigint_normalize(tr);

        free(u); free(v); free(q);
    }

    /* Translate truncated result into Python floor-division convention.
     *   a  b   truncated(q, r)       python(q, r)
     *   +  +       (+q, +r)              same
     *   -  -       (+q, -r)              same
     *   +  -       (-q, +r)              r==0 ? same : (q-1, r+b)
     *   -  +       (-q, -r)              r==0 ? same : (q-1, r+b)
     *
     * i.e. if signs differ and remainder is nonzero, subtract one from the
     * quotient and add divisor to the remainder.
     */
    int signs_differ = (a->sign * b->sign) < 0;
    if (a->sign == 0) {
        /* a == 0 => q = 0, r = 0. tq/tr already correct. */
    } else {
        tq->sign = (a->sign == b->sign) ? 1 : -1;
        if (tq->ndigits == 0) tq->sign = 0;
        tr->sign = (tr->ndigits == 0) ? 0 : a->sign;
    }

    if (signs_differ && tr->ndigits > 0) {
        /* q -= 1 */
        PyIntObject *one = py_bigint_from_i64(1);
        PyIntObject *q2 = py_bigint_sub(tq, one);
        free(one); free(tq);
        if (q2 == NULL) { free(tr); return -1; }
        tq = q2;
        /* r += b */
        PyIntObject *r2 = py_bigint_add(tr, b);
        free(tr);
        if (r2 == NULL) { free(tq); return -1; }
        tr = r2;
    }

    *q_out = tq;
    *r_out = tr;
    return 0;
}

/* Bignum exponentiation moved to py_int_bigint_pow.c so it can be
 * replaced independently by py_int_bigint_pow.py. */

/* Public integer operation dispatch moved to py_int_ops.c so it can be
 * replaced independently by py_int_ops.py. */

/* py_int_from_cstr moved to py_int_parse.c so the parser-facing helper
 * can be replaced independently by py_int_parse.py. */
