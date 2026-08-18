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
 * (the common case). Matches CPython's error surface: raises ValueError for
 * mod == 0 ("pow() 3rd argument cannot be 0"), and — since we do not yet
 * compute a modular inverse — raises ValueError for a negative exponent with a
 * modulus (CPython supports it via the inverse; we defer it explicitly rather
 * than return a wrong result). Both error paths set a Python exception via
 * py_raise so the frontend py_err_occurred() check branches to the error path
 * (a bare NULL return would NOT be caught — there is no stack unwinding).
 * Returns a new reference, or NULL (with an exception set) on error. */
PyObject *py_int_pow_mod(PyObject *base, PyObject *exp, PyObject *mod) {
    if (base == NULL || exp == NULL || mod == NULL) return NULL;

    PyObject *one = py_int_from_i64(1);
    PyObject *zero = py_int_from_i64(0);
    if (one == NULL || zero == NULL) {
        if (one) py_decref(one);
        if (zero) py_decref(zero);
        return NULL;
    }

    /* mod == 0: CPython raises ValueError, not ZeroDivisionError. Check up
     * front (before the first py_int_mod, which would otherwise return a bare
     * NULL for a zero divisor with no exception set). */
    if (py_int_cmp(mod, zero) == 0) {
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
                            "pow() 3rd argument cannot be 0"));
        py_decref(one);
        py_decref(zero);
        return NULL;
    }

    /* Negative exponent: CPython computes base**-e mod m via the modular
     * inverse of base. We do not implement the inverse yet; raise a clear,
     * matching-typed error (ValueError) instead of returning a wrong result or
     * a silent NULL. */
    if (py_int_cmp(exp, zero) < 0) {
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
                            "pow() negative exponent with modulus not "
                            "supported"));
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

/* math.isqrt(n): floor of the exact square root, bignum-correct.
 *
 * Kept as a C helper in OBJ_PY_CC_HELPERS (no pcc-Python port — see the file
 * header) so the frontend can route ``math.isqrt(n)`` to a single
 * implementation for arbitrary-precision operands. Uses integer Newton
 * iteration on boxed PyObject ints via the py_int_* helpers, so it never
 * converts to float (which would lose precision above 2**53 and give wrong
 * results such as isqrt((10**30)+1)).
 *
 * Matches CPython's error surface: raises ValueError for a negative argument
 * ("isqrt() argument must be nonnegative") via py_raise so the frontend
 * py_err_occurred() check branches to the error path (a bare NULL return
 * would NOT be caught — there is no stack unwinding). Returns a new
 * reference, or NULL (with an exception set) on error.
 *
 * Algorithm (verified against math.isqrt over a wide random/bignum range):
 *   if n < 0: raise ValueError
 *   if n == 0: return 0
 *   x = 1 << ((bit_length(n) + 1) / 2)     -- initial overestimate
 *   loop: y = (x + n // x) // 2; if y >= x: return x; x = y
 * The iterate is a monotone-decreasing overestimate that stops at
 * floor(sqrt(n)); the y >= x test detects the fixed point / oscillation. */
PyObject *py_int_isqrt(PyObject *n) {
    if (n == NULL) return NULL;

    PyObject *zero = py_int_from_i64(0);
    if (zero == NULL) return NULL;

    /* Negative argument: CPython raises ValueError. */
    if (py_int_cmp(n, zero) < 0) {
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
                            "isqrt() argument must be nonnegative"));
        py_decref(zero);
        return NULL;
    }

    /* n == 0: isqrt(0) == 0. (Also guards the n // x division below, which
     * would be undefined for the initial x when n is 0.) */
    if (py_int_cmp(n, zero) == 0) {
        return zero;  /* transfer ownership of the boxed 0 to the caller */
    }

    PyObject *one = py_int_from_i64(1);
    PyObject *two = py_int_from_i64(2);
    if (one == NULL || two == NULL) {
        if (one) py_decref(one);
        if (two) py_decref(two);
        py_decref(zero);
        return NULL;
    }

    /* x = 1 << ((bit_length(n) + 1) / 2): a tight overestimate of the root
     * whose bit length is ceil(bit_length(n) / 2). */
    int64_t bl = py_int_bit_length(n);
    PyObject *shift = py_int_from_i64((bl + 1) / 2);
    PyObject *x = (shift != NULL) ? py_int_shl(one, shift) : NULL;
    if (shift) py_decref(shift);
    if (x == NULL) {
        py_decref(one);
        py_decref(two);
        py_decref(zero);
        return NULL;
    }

    /* Newton iteration on boxed ints: y = (x + n // x) // 2. */
    while (1) {
        PyObject *q = py_int_floordiv(n, x);              /* n // x */
        PyObject *sum = (q != NULL) ? py_int_add(x, q) : NULL;
        if (q) py_decref(q);
        PyObject *y = (sum != NULL) ? py_int_floordiv(sum, two) : NULL;
        if (sum) py_decref(sum);
        if (y == NULL) {
            py_decref(x);
            x = NULL;
            break;
        }
        /* if y >= x: converged, x is floor(sqrt(n)). */
        if (py_int_cmp(y, x) >= 0) {
            py_decref(y);
            break;
        }
        py_decref(x);
        x = y;
    }

    py_decref(one);
    py_decref(two);
    py_decref(zero);
    return x;  /* owned result (floor sqrt), or NULL with an exception set */
}
