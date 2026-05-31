/* pcc/py_runtime/src/py_int_modexp.c
 *
 * Three-argument pow(base, exp, mod) — modular exponentiation by
 * square-and-multiply. Kept as a C helper linked in both the cc and the
 * pcc-Python (no-libpython) runtime archives (OBJ_PY_CC_HELPERS), so the
 * frontend can route ``pow(b, e, mod)`` to a single implementation without the
 * pcc-Python port having to reimplement a refcount-careful loop.
 *
 * Crucially this never materialises the full ``base ** exp`` (which for a
 * crypto-size exponent would exhaust memory) — it reduces modulo ``mod`` at
 * every step. Operates on boxed PyObject ints via the existing py_int_* helpers
 * so it transparently handles bignum operands.
 */
#include "py_internal.h"

/* pow(base, exp, mod). Assumes integer operands. Handles exp >= 0 and mod != 0
 * (the common case; CPython raises for a negative exp without a modular
 * inverse and for mod == 0 — those are left to a NULL return). Returns a new
 * reference, or NULL on error. */
PyObject *py_int_pow_mod(PyObject *base, PyObject *exp, PyObject *mod) {
    if (base == NULL || exp == NULL || mod == NULL) return NULL;

    PyObject *one = py_int_from_i64(1);
    PyObject *zero = py_int_from_i64(0);
    if (one == NULL || zero == NULL) {
        if (one) py_decref(one);
        if (zero) py_decref(zero);
        return NULL;
    }

    /* Negative exponent: not supported here (CPython would need a modular
     * inverse). Return NULL so the caller surfaces an error rather than a
     * wrong result. */
    if (py_int_cmp(exp, zero) < 0) {
        py_decref(one);
        py_decref(zero);
        return NULL;
    }

    PyObject *result = py_int_from_i64(1);   /* owned */
    PyObject *b = py_int_mod(base, mod);     /* owned: base %= mod */
    PyObject *e = exp;                       /* owned working copy */
    py_incref(e);

    if (result == NULL || b == NULL) {
        if (result) py_decref(result);
        if (b) py_decref(b);
        py_decref(e);
        py_decref(one);
        py_decref(zero);
        return NULL;
    }

    while (py_int_cmp(e, zero) > 0) {
        /* if (e & 1): result = (result * b) % mod */
        PyObject *bit = py_int_and(e, one);
        int odd = (bit != NULL) && (py_int_cmp(bit, zero) != 0);
        if (bit) py_decref(bit);
        if (odd) {
            PyObject *prod = py_int_mul(result, b);
            PyObject *nr = (prod != NULL) ? py_int_mod(prod, mod) : NULL;
            if (prod) py_decref(prod);
            py_decref(result);
            result = nr;
            if (result == NULL) break;
        }
        /* b = (b * b) % mod */
        PyObject *bsq = py_int_mul(b, b);
        PyObject *nb = (bsq != NULL) ? py_int_mod(bsq, mod) : NULL;
        if (bsq) py_decref(bsq);
        py_decref(b);
        b = nb;
        if (b == NULL) break;
        /* e >>= 1 */
        PyObject *ne = py_int_shr(e, one);
        py_decref(e);
        e = ne;
        if (e == NULL) break;
    }

    if (b) py_decref(b);
    if (e) py_decref(e);

    /* Final reduction handles the exp == 0 case: pow(b, 0, mod) == 1 % mod
     * (e.g. pow(5, 0, 1) == 0). For exp > 0 result is already < mod, so this
     * is a no-op there. */
    if (result != NULL) {
        PyObject *reduced = py_int_mod(result, mod);
        py_decref(result);
        result = reduced;
    }

    py_decref(one);
    py_decref(zero);
    return result;
}
