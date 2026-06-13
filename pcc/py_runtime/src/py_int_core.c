/* pcc/py_runtime/src/py_int_core.c
 *
 * Small int/bignum boundary helpers split out from py_int.c so the
 * pcc-Python runtime can replace them independently from the heavier
 * arithmetic implementation.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>
#include <assert.h>

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
    if (o == NULL) return NULL;
    /* bool is-a int (CPython): True -> 1, False -> 0. Lets bool operands flow
     * through every int op (sum, +, *, ...) instead of failing as non-int. */
    if (py_header(o)->type_tag == PY_TYPE_BOOL)
        return py_bigint_from_i64(o == py_True ? 1 : 0);
    if (py_header(o)->type_tag != PY_TYPE_INT) return NULL;
    return bigint_copy((const PyIntObject *)o);
}

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

/* int.bit_length(): number of bits to represent abs(value), 0 for 0. Exact for
 * bignums: (ndigits-1)*32 + bits in the top base-2^32 digit. */
int64_t py_int_bit_length(PyObject *o) {
    if (o == NULL) return 0;
    if (PY_IS_TAGGED_INT(o)) {
        int64_t v = py_untag_int(o);
        uint64_t a = (v < 0) ? (uint64_t)(-(v + 1)) + 1u : (uint64_t)v;
        int64_t bits = 0;
        while (a > 0) { bits++; a >>= 1; }
        return bits;
    }
    if (py_header(o)->type_tag == PY_TYPE_INT) {
        const PyIntObject *b = (const PyIntObject *)o;
        if (b->ndigits <= 0) return 0;
        uint32_t top = b->digits[b->ndigits - 1];
        int64_t top_bits = 0;
        while (top > 0) { top_bits++; top >>= 1; }
        return (int64_t)(b->ndigits - 1) * 32 + top_bits;
    }
    return 0;
}

/* int.bit_count(): number of set bits (population count) in abs(value); 0 for
 * 0. CPython counts bits of the magnitude, so negatives match their absolute
 * value: (-255).bit_count() == 8. Exact for bignums (popcount each base-2^32
 * limb; the limbs already store the magnitude, sign is separate). */
int64_t py_int_bit_count(PyObject *o) {
    if (o == NULL) return 0;
    if (PY_IS_TAGGED_INT(o)) {
        int64_t v = py_untag_int(o);
        uint64_t a = (v < 0) ? (uint64_t)(-(v + 1)) + 1u : (uint64_t)v;
        int64_t bits = 0;
        while (a > 0) { bits += (int64_t)(a & 1u); a >>= 1; }
        return bits;
    }
    if (py_header(o)->type_tag == PY_TYPE_INT) {
        const PyIntObject *b = (const PyIntObject *)o;
        int64_t bits = 0;
        for (int32_t i = 0; i < b->ndigits; i++) {
            uint32_t d = b->digits[i];
            while (d > 0) { bits += (int64_t)(d & 1u); d >>= 1; }
        }
        return bits;
    }
    return 0;
}

PyObject *py_int_from_i64(int64_t v) {
    if (v >= PY_TAGGED_INT_MIN && v <= PY_TAGGED_INT_MAX) {
        return py_tag_int(v);
    }
    return (PyObject *)py_bigint_from_i64(v);
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

int py_int_cmp(PyObject *a, PyObject *b) {
    if (PY_IS_TAGGED_INT(a) && PY_IS_TAGGED_INT(b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        if (av < bv) return -1;
        if (av > bv) return  1;
        return 0;
    }
    PyIntObject *ba = py_bigint_from_any(a);
    PyIntObject *bb = py_bigint_from_any(b);
    int r = 0;
    if (ba && bb) r = py_bigint_cmp(ba, bb);
    free(ba);
    free(bb);
    return r;
}
