/* pcc/py_runtime/src/py_int_ops.c
 *
 * Public integer operation dispatch split from py_int.c for independent
 * pcc-Python replacement.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <math.h>

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

PyObject *py_int_floordiv(PyObject *a, PyObject *b) {
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        if (bv == 0) return NULL;
        /* C trunc-divides; Python wants floor. Adjust if signs differ
         * and remainder is non-zero. ``av`` and ``bv`` are in
         * [PY_TAGGED_INT_MIN, PY_TAGGED_INT_MAX], so neither the divide
         * nor the q-1 adjust can overflow i64. */
        int64_t q = av / bv;
        int64_t r = av - q * bv;
        if (r != 0 && ((r ^ bv) < 0)) {
            q -= 1;
        }
        return py_int_from_i64(q);
    }
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
    if (is_tagged_both(a, b)) {
        int64_t av = py_untag_int(a);
        int64_t bv = py_untag_int(b);
        if (bv == 0) return NULL;
        /* C %-truncates toward 0; Python mod has same sign as divisor.
         * Adjust if signs of remainder and divisor differ. */
        int64_t r = av % bv;
        if (r != 0 && ((r ^ bv) < 0)) {
            r += bv;
        }
        return py_int_from_i64(r);
    }
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
    if (overflow) return NULL;
    if (n < 0) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "negative shift count"));
        return NULL;
    }
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
    if (overflow) return NULL;
    if (n < 0) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "negative shift count"));
        return NULL;
    }
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
