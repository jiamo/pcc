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
#include <errno.h>

/* ---- Forward decls ---------------------------------------------------- */

static void        py_bigint_normalize(PyIntObject *b);
static PyIntObject *bigint_copy(const PyIntObject *a);
static PyIntObject *bigint_abs_add(const PyIntObject *a, const PyIntObject *b,
                                   int32_t sign_if_nonzero);
static PyIntObject *bigint_abs_sub(const PyIntObject *a, const PyIntObject *b,
                                   int32_t sign_if_nonzero);
static int          bigint_abs_cmp(const PyIntObject *a, const PyIntObject *b);
static PyIntObject *bigint_shl_digits_and_bits(const PyIntObject *a,
                                               uint64_t ndigits_shift,
                                               unsigned bit_shift);
static uint32_t     bigint_divmod_small_inplace(PyIntObject *a, uint32_t divisor);

/* ---- Allocation helpers ---------------------------------------------- */

PyIntObject *py_bigint_alloc(int32_t ndigits) {
    if (ndigits < 0) ndigits = 0;
    size_t bytes = sizeof(PyIntObject) + (size_t)ndigits * sizeof(uint32_t);
    PyIntObject *b = (PyIntObject *)malloc(bytes);
    if (b == NULL) return NULL;
    b->h.refcount = 1;
    b->h.type_tag = PY_TYPE_INT;
    b->h.flags    = 0;
    b->sign       = 0;
    b->ndigits    = ndigits;
    for (int32_t i = 0; i < ndigits; i++) b->digits[i] = 0;
    return b;
}

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

PyIntObject *py_bigint_from_i64(int64_t v) {
    /* Worst case: |INT64_MIN| needs 2 digits. */
    PyIntObject *b = py_bigint_alloc(2);
    if (b == NULL) return NULL;
    if (v == 0) {
        b->sign = 0;
        b->ndigits = 0;
        return b;
    }
    uint64_t u;
    if (v < 0) {
        b->sign = -1;
        /* Safe negation of INT64_MIN via unsigned. */
        u = (uint64_t)(-(v + 1)) + 1u;
    } else {
        b->sign = 1;
        u = (uint64_t)v;
    }
    b->digits[0] = (uint32_t)(u & 0xFFFFFFFFu);
    b->digits[1] = (uint32_t)(u >> 32);
    b->ndigits = (b->digits[1] != 0) ? 2 : 1;
    return b;
}

PyObject *py_bigint_to_pyobject(PyIntObject *b) {
    if (b == NULL) return NULL;
    int overflow = 0;
    int64_t v = py_bigint_to_i64(b, &overflow);
    if (!overflow && v >= PY_TAGGED_INT_MIN && v <= PY_TAGGED_INT_MAX) {
        /* Collapse to tagged int — free the bignum (refcount == 1 assumed
         * because the caller just built it). */
        free(b);
        return py_tag_int(v);
    }
    return (PyObject *)b;
}

PyIntObject *py_bigint_from_any(PyObject *o) {
    if (PY_IS_TAGGED_INT(o)) return py_bigint_from_i64(py_untag_int(o));
    if (o == NULL || py_header(o)->type_tag != PY_TYPE_INT) return NULL;
    return bigint_copy((const PyIntObject *)o);
}

/* ---- Internal helpers ------------------------------------------------- */

/* Heap-int factory for values known to fit in int64 but not the tagged
 * range. Used for the py_int_value_i64 path. */
PyObject *py_int_new_heap(int64_t v) {
    PyIntObject *b = py_bigint_from_i64(v);
    return (PyObject *)b;
}

int64_t py_int_value_i64(PyObject *o) {
    if (PY_IS_TAGGED_INT(o)) return py_untag_int(o);
    assert(o != NULL && py_header(o)->type_tag == PY_TYPE_INT);
    const PyIntObject *b = (const PyIntObject *)o;
    int overflow = 0;
    int64_t v = py_bigint_to_i64(b, &overflow);
    if (!overflow) return v;
    /* Oversized bignum: return a sentinel that's non-zero and sign-
     * preserving so that zero-comparison callers (e.g. py_obj_truthy) still
     * see the right truthiness. Callers that need an exact value should go
     * through the public py_int_to_i64 with an overflow out-param. */
    return b->sign < 0 ? INT64_MIN : INT64_MAX;
}

/* ---- Public constructors --------------------------------------------- */

PyObject *py_int_from_i64(int64_t v) {
    if (v >= PY_TAGGED_INT_MIN && v <= PY_TAGGED_INT_MAX) {
        return py_tag_int(v);
    }
    return (PyObject *)py_bigint_from_i64(v);
}

int64_t py_int_to_i64(PyObject *o, int *overflow) {
    if (overflow) *overflow = 0;
    if (o == NULL) {
        if (overflow) *overflow = 1;
        return 0;
    }
    if (PY_IS_TAGGED_INT(o)) {
        return py_untag_int(o);
    }
    if (py_header(o)->type_tag != PY_TYPE_INT) {
        if (overflow) *overflow = 1;
        return 0;
    }
    return py_bigint_to_i64((const PyIntObject *)o, overflow);
}

/* ---- Bignum <-> int64 ------------------------------------------------- */

int64_t py_bigint_to_i64(const PyIntObject *b, int *overflow) {
    if (overflow) *overflow = 0;
    if (b->sign == 0) return 0;

    if (b->ndigits > 2) {
        if (overflow) *overflow = 1;
        return 0;
    }
    uint64_t u = (uint64_t)b->digits[0];
    if (b->ndigits == 2) u |= ((uint64_t)b->digits[1]) << 32;

    if (b->sign > 0) {
        if (u > (uint64_t)INT64_MAX) {
            if (overflow) *overflow = 1;
            return 0;
        }
        return (int64_t)u;
    } else {
        /* Negative. |INT64_MIN| fits exactly. */
        if (u > (uint64_t)INT64_MAX + 1u) {
            if (overflow) *overflow = 1;
            return 0;
        }
        if (u == (uint64_t)INT64_MAX + 1u) return INT64_MIN;
        return -(int64_t)u;
    }
}

double py_bigint_to_double(const PyIntObject *b) {
    if (b->sign == 0) return 0.0;
    /* Walk high-to-low building the double. */
    double r = 0.0;
    for (int32_t i = b->ndigits - 1; i >= 0; i--) {
        r = r * 4294967296.0 + (double)b->digits[i];
    }
    return b->sign < 0 ? -r : r;
}

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

/* ---- Bignum arithmetic public ---------------------------------------- */

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

PyIntObject *py_bigint_neg(const PyIntObject *a) {
    PyIntObject *r = bigint_copy(a);
    if (r == NULL) return NULL;
    r->sign = -r->sign;
    return r;
}

int py_bigint_cmp(const PyIntObject *a, const PyIntObject *b) {
    if (a->sign != b->sign) return a->sign < b->sign ? -1 : 1;
    if (a->sign == 0) return 0;
    int mag = bigint_abs_cmp(a, b);
    return a->sign > 0 ? mag : -mag;
}

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

/* ---- Decimal string conversion ---------------------------------------- */

char *py_bigint_to_cstr(const PyIntObject *b) {
    if (b->sign == 0) {
        char *s = (char *)malloc(2);
        if (!s) return NULL;
        s[0] = '0'; s[1] = '\0';
        return s;
    }
    /* Upper bound: 10 decimal digits per 32-bit digit + sign + NUL. */
    size_t bufsz = (size_t)b->ndigits * 10 + 2;
    char *buf = (char *)malloc(bufsz);
    if (buf == NULL) return NULL;

    /* Work on a mutable copy. */
    PyIntObject *tmp = bigint_copy(b);
    if (tmp == NULL) { free(buf); return NULL; }
    tmp->sign = 1;  /* we know original is nonzero */

    size_t pos = bufsz;
    buf[--pos] = '\0';

    /* Repeatedly divmod by 10^9 to extract 9 decimal digits per iter. */
    const uint32_t CHUNK = 1000000000u;
    while (tmp->ndigits > 0) {
        uint32_t rem = bigint_divmod_small_inplace(tmp, CHUNK);
        bool more = tmp->ndigits > 0;
        /* Write exactly 9 digits if more chunks remain (zero-pad); else the
         * minimal representation. */
        int count = 0;
        if (!more) {
            /* strip leading zeros for the highest chunk */
            if (rem == 0) {
                buf[--pos] = '0';
                count = 1;
            } else {
                while (rem > 0) {
                    buf[--pos] = (char)('0' + (rem % 10));
                    rem /= 10;
                    count++;
                }
            }
        } else {
            for (int k = 0; k < 9; k++) {
                buf[--pos] = (char)('0' + (rem % 10));
                rem /= 10;
            }
            count = 9;
        }
        (void)count;
    }
    free(tmp);

    if (b->sign < 0) buf[--pos] = '-';
    /* Shift the string down so it starts at buf[0]. */
    size_t len = bufsz - pos;
    memmove(buf, buf + pos, len);
    /* Shrink allocation? Leave it — caller frees it regardless. */
    return buf;
}

/* Parse a decimal string (optional leading sign, then digits). Returns a
 * new bignum (never a tagged form) or NULL on error. */
PyIntObject *py_bigint_from_cstr(const char *s) {
    if (s == NULL) return NULL;
    const char *p = s;
    int sign = 1;
    if (*p == '+') p++;
    else if (*p == '-') { sign = -1; p++; }
    if (*p == '\0') return NULL;
    /* Accumulate by 10^9 chunks. */
    PyIntObject *acc = py_bigint_alloc(0);
    if (acc == NULL) return NULL;

    const uint32_t CHUNK = 1000000000u;

    while (*p) {
        /* Read up to 9 decimal digits. */
        uint32_t chunk = 0;
        uint32_t mul = 1;
        int count = 0;
        while (*p && count < 9) {
            if (*p < '0' || *p > '9') { free(acc); return NULL; }
            chunk = chunk * 10 + (uint32_t)(*p - '0');
            mul *= 10;
            count++;
            p++;
        }
        if (count == 0) { free(acc); return NULL; }

        /* acc = acc * mul + chunk */
        /* Multiply in-place: allocate result with one extra digit. */
        int32_t la = acc->ndigits;
        PyIntObject *next = py_bigint_alloc(la + 1);
        if (next == NULL) { free(acc); return NULL; }
        uint64_t carry = 0;
        for (int32_t i = 0; i < la; i++) {
            uint64_t cur = (uint64_t)acc->digits[i] * (uint64_t)mul + carry;
            next->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        next->digits[la] = (uint32_t)carry;
        /* Add chunk. */
        carry = chunk;
        for (int32_t i = 0; i < next->ndigits && carry; i++) {
            uint64_t cur = (uint64_t)next->digits[i] + carry;
            next->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        next->sign = 1;
        py_bigint_normalize(next);
        free(acc);
        acc = next;
    }
    if (acc->ndigits == 0) {
        acc->sign = 0;
    } else {
        acc->sign = sign;
    }
    return acc;
}

/* ---- Shifts ---------------------------------------------------------- */

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

/* ---- Bitwise ops (two's-complement-of-infinite-width semantics) ------ */
/*
 * Strategy: encode each operand into a two's-complement uint32 stream, then
 * apply the bitwise op digit-wise, then decode back. For positives the
 * stream is just the magnitude digits with zero-extension; for negatives it
 * is (2^(32*n) - |x|) — i.e. invert all digits and add 1, widened to the
 * max length. The sign of the result is determined by the top bit of the
 * output stream.
 */

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

    /* Sign-extend the top digit of each negative to fill n slots. */
    if (a->sign < 0) {
        /* already done via the loop up to n */
    }
    if (b->sign < 0) {
        /* same */
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

/* ---- Power (bignum base, non-negative bignum exponent) --------------- */

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

/* ---- Tagged-fast + bignum-slow arithmetic dispatch ------------------- */
/* The public `py_int_*` entry points try the tagged int64 path first and
 * fall back to bignums on overflow or when either operand is already a
 * heap int. */

/* Small promotion helper that always returns a heap bignum copy (new ref).
 * Caller must py_decref result. */
static PyIntObject *promote_any(PyObject *o) {
    return py_bigint_from_any(o);
}

static PyObject *wrap_bigint(PyIntObject *b) {
    if (b == NULL) return NULL;
    return py_bigint_to_pyobject(b);
}

static bool is_tagged_both(PyObject *a, PyObject *b) {
    return PY_IS_TAGGED_INT(a) && PY_IS_TAGGED_INT(b);
}

PyObject *py_int_add(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        int64_t r;
        if (!__builtin_add_overflow(av, bv, &r)) {
            return py_int_from_i64(r);
        }
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_add(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_sub(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        int64_t r;
        if (!__builtin_sub_overflow(av, bv, &r)) {
            return py_int_from_i64(r);
        }
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_sub(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_mul(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        int64_t r;
        if (!__builtin_mul_overflow(av, bv, &r)) {
            return py_int_from_i64(r);
        }
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_mul(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_neg(PyObject *a) {
    if (PY_IS_TAGGED_INT(a)) {
        int64_t v = py_untag_int(a);
        if (v != INT64_MIN) return py_int_from_i64(-v);
    }
    PyIntObject *ba = promote_any(a);
    if (!ba) return NULL;
    PyIntObject *br = py_bigint_neg(ba);
    free(ba);
    return wrap_bigint(br);
}

int py_int_cmp(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        if (av < bv) return -1;
        if (av > bv) return  1;
        return 0;
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    int r = 0;
    if (ba && bb) r = py_bigint_cmp(ba, bb);
    free(ba); free(bb);
    return r;
}

/* ---- floordiv / mod / truediv --------------------------------------- */

PyObject *py_int_floordiv(PyObject *a, PyObject *b) {
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *q = NULL, *r = NULL;
    int ok = py_bigint_divmod(ba, bb, &q, &r);
    free(ba); free(bb);
    if (ok != 0) return NULL;
    free(r);
    return wrap_bigint(q);
}

PyObject *py_int_mod(PyObject *a, PyObject *b) {
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *q = NULL, *r = NULL;
    int ok = py_bigint_divmod(ba, bb, &q, &r);
    free(ba); free(bb);
    if (ok != 0) return NULL;
    free(q);
    return wrap_bigint(r);
}

PyObject *py_int_truediv(PyObject *a, PyObject *b) {
    /* True division of two ints returns a PyFloatObject.
     *
     * For operands that fit in double we just divide directly. For very
     * large magnitudes we approximate via py_bigint_to_double — not bit-
     * accurate but matches common cases. Division by zero returns NULL so
     * callers can raise ZeroDivisionError.
     */
    if (PY_IS_TAGGED_INT(b) && py_untag_int(b) == 0) return NULL;
    if (!PY_IS_TAGGED_INT(b)) {
        const PyIntObject *bb = (const PyIntObject *)b;
        if (bb->sign == 0) return NULL;
    }

    double av, bv;
    if (PY_IS_TAGGED_INT(a)) av = (double)py_untag_int(a);
    else av = py_bigint_to_double((const PyIntObject *)a);
    if (PY_IS_TAGGED_INT(b)) bv = (double)py_untag_int(b);
    else bv = py_bigint_to_double((const PyIntObject *)b);

    double q = av / bv;
    PyFloatObject *f = (PyFloatObject *)malloc(sizeof(PyFloatObject));
    if (f == NULL) return NULL;
    f->h.refcount = 1;
    f->h.type_tag = PY_TYPE_FLOAT;
    f->h.flags    = 0;
    f->value      = q;
    return (PyObject *)f;
}

/* ---- Power (public) -------------------------------------------------- */

PyObject *py_int_pow(PyObject *a, PyObject *b) {
    /* Negative exponent => float (like Python). We don't have full float
     * semantics here; return NULL and let the caller surface an error. */
    if (PY_IS_TAGGED_INT(b)) {
        int64_t ev = py_untag_int(b);
        if (ev < 0) {
            /* Emit a float via pow(). */
            double av;
            if (PY_IS_TAGGED_INT(a)) av = (double)py_untag_int(a);
            else av = py_bigint_to_double((const PyIntObject *)a);
            double r = pow(av, (double)ev);
            PyFloatObject *f = (PyFloatObject *)malloc(sizeof(PyFloatObject));
            if (f == NULL) return NULL;
            f->h.refcount = 1;
            f->h.type_tag = PY_TYPE_FLOAT;
            f->h.flags    = 0;
            f->value      = r;
            return (PyObject *)f;
        }
    } else {
        const PyIntObject *bb = (const PyIntObject *)b;
        if (bb->sign < 0) return NULL;
    }

    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_pow(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

/* ---- Bitwise and shifts (public) ------------------------------------- */

PyObject *py_int_and(PyObject *a, PyObject *b) {
    /* Fast path: both tagged => 63-bit AND stays inside the tagged range. */
    if (is_tagged_both(a, b)) {
        int64_t r = py_untag_int(a) & py_untag_int(b);
        return py_int_from_i64(r);
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_and(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_or(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t r = py_untag_int(a) | py_untag_int(b);
        return py_int_from_i64(r);
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_or(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_xor(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t r = py_untag_int(a) ^ py_untag_int(b);
        return py_int_from_i64(r);
    }
    PyIntObject *ba = promote_any(a);
    PyIntObject *bb = promote_any(b);
    if (!ba || !bb) { free(ba); free(bb); return NULL; }
    PyIntObject *br = py_bigint_xor(ba, bb);
    free(ba); free(bb);
    return wrap_bigint(br);
}

PyObject *py_int_shl(PyObject *a, PyObject *b) {
    /* Shift count must be non-negative. */
    int overflow = 0;
    int64_t n = py_int_to_i64(b, &overflow);
    if (overflow || n < 0) return NULL;
    if (n == 0) {
        /* Return a new reference to a. Tagged ints are inherently immortal
         * (no refcount), so the same tagged pointer is safe to return. */
        if (PY_IS_TAGGED_INT(a)) return a;
        py_incref(a);
        return a;
    }
    /* Tagged-int fast path: shift fits in int64 => stay tagged. */
    if (PY_IS_TAGGED_INT(a) && n < 63) {
        int64_t av = py_untag_int(a);
        int64_t r;
        if (!__builtin_mul_overflow(av, (int64_t)1 << n, &r)) {
            return py_int_from_i64(r);
        }
    }
    PyIntObject *ba = promote_any(a);
    if (!ba) return NULL;
    PyIntObject *br = py_bigint_shl(ba, (uint64_t)n);
    free(ba);
    return wrap_bigint(br);
}

PyObject *py_int_shr(PyObject *a, PyObject *b) {
    int overflow = 0;
    int64_t n = py_int_to_i64(b, &overflow);
    if (overflow || n < 0) return NULL;
    if (n == 0) {
        if (PY_IS_TAGGED_INT(a)) return a;
        py_incref(a);
        return a;
    }
    /* Tagged-int fast path: arithmetic right shift of the 63-bit payload. */
    if (PY_IS_TAGGED_INT(a)) {
        int64_t av = py_untag_int(a);
        int64_t r = (n >= 63) ? (av < 0 ? -1 : 0) : (av >> n);
        return py_int_from_i64(r);
    }
    PyIntObject *ba = promote_any(a);
    if (!ba) return NULL;
    PyIntObject *br = py_bigint_shr(ba, (uint64_t)n);
    free(ba);
    return wrap_bigint(br);
}

/* -------- Decimal / C-string parse ------------------------------------- */

PyObject *py_int_from_cstr(const char *s, int base) {
    /* Minimal strtoll-based parse — wraps to bignum on overflow. For
     * now the overflow case returns NULL (Python's behaviour on very
     * large literals would construct a bignum; the pcc self-host
     * surface doesn't produce that shape). */
    if (s == NULL) return NULL;
    /* Skip leading whitespace to match Python's int(str) behaviour. */
    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++;
    char *end = NULL;
    errno = 0;
    long long v = strtoll(s, &end, base);
    if (end == s) return NULL;               /* no digits consumed */
    /* Allow trailing whitespace only (Python). */
    while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r') end++;
    if (*end != '\0') return NULL;
    if (errno == ERANGE) return NULL;
    return py_int_from_i64((int64_t)v);
}
