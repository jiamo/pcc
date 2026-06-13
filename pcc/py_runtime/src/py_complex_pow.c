/* py_complex_pow.c — complex ``base ** exp``.
 *
 * C-only OBJ_PY_CC_HELPERS module (no pcc-Python port mirror): the general
 * complex-power path needs transcendental math (exp/log/cos/sin/atan2/hypot)
 * that the pcc-Python runtime port would only awkwardly reimplement, so — as
 * with the sibling complex helpers in py_format.c (py_complex_sub/mul/div/…) —
 * a single C implementation is linked into BOTH the C-runtime archive
 * (libpy_runtime.a) and the default pcc-Python port archive
 * (libpy_runtime_pcc_py.a via OBJ_PY_CC_HELPERS). See py_complex_pow.py for the
 * intentional C-only marker and a reference algorithm mirror.
 *
 * Semantics mirror CPython Objects/complexobject.c::_Py_c_pow (+ c_powi /
 * c_powu / _Py_c_quot):
 *
 *   - exp == 0            -> 1+0j
 *   - base == 0           -> 0+0j, but ZeroDivisionError when exp has a
 *                            negative real part or a non-zero imaginary part
 *                            ("zero to a negative or complex power")
 *   - exp is a real integer with |exp| <= 100  -> exact repeated-squaring path
 *   - otherwise           -> general exp/log/cos/sin polar path
 *
 * Operands may be complex/int/float/bool; Python coerces the non-complex side
 * to a real (imag == 0), matching the coercion used by py_complex_add etc.
 *
 * The helpers pass (re, im) as scalar doubles / out-parameters rather than
 * returning aggregates by value: this file is also compiled by pcc's own C
 * frontend for the LIB_PCC archive, and scalar params avoid any struct-return
 * ABI dependency.
 */
#include "py_internal.h"
#include <math.h>
#include <stdint.h>

extern PyObject *py_complex_new(double real, double imag);
extern double py_bigint_to_double(const PyIntObject *o);

/* Real/imag coercion identical to pcc_cx_re/pcc_cx_im in py_format.c. Kept
 * file-local (static) so there is no cross-TU duplicate-symbol clash. */
static double pcc_cxp_re(PyObject *o) {
    if (o == NULL) return 0.0;
    if (PY_IS_TAGGED_INT(o)) return (double)py_untag_int(o);
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_COMPLEX) return ((PyComplexObject *)o)->real;
    if (tag == PY_TYPE_FLOAT) return ((PyFloatObject *)o)->value;
    if (tag == PY_TYPE_INT) return py_bigint_to_double((const PyIntObject *)o);
    if (tag == PY_TYPE_BOOL) return o == py_True ? 1.0 : 0.0;
    return 0.0;
}
static double pcc_cxp_im(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0.0;
    if (py_header(o)->type_tag == PY_TYPE_COMPLEX)
        return ((PyComplexObject *)o)->imag;
    return 0.0;
}

/* out = a * b (complex product). */
static void pcc_cxp_prod(double are, double aim, double bre, double bim,
                         double *ore, double *oim) {
    *ore = are * bre - aim * bim;
    *oim = are * bim + aim * bre;
}

/* out = a / b via Smith's algorithm — matches CPython _Py_c_quot so the
 * negative-integer-exponent path rounds identically. Only ever called here
 * with a non-zero divisor (numerator is 1+0j from the c_powi path), but keeps
 * the zero guard for parity. */
static void pcc_cxp_quot(double are, double aim, double bre, double bim,
                         double *ore, double *oim) {
    const double abs_breal = bre < 0 ? -bre : bre;
    const double abs_bimag = bim < 0 ? -bim : bim;
    if (abs_breal >= abs_bimag) {
        if (abs_breal == 0.0) {
            *ore = 0.0;
            *oim = 0.0;
            return;
        }
        const double ratio = bim / bre;
        const double denom = bre + bim * ratio;
        *ore = (are + aim * ratio) / denom;
        *oim = (aim - are * ratio) / denom;
    } else {
        const double ratio = bre / bim;
        const double denom = bre * ratio + bim;
        *ore = (are * ratio + aim) / denom;
        *oim = (aim * ratio - are) / denom;
    }
}

/* out = x ** n for n >= 0 via repeated squaring (c_powu). */
static void pcc_cxp_powu(double xre, double xim, int64_t n,
                         double *ore, double *oim) {
    double rre = 1.0;
    double rim = 0.0;
    double pre = xre;
    double pim = xim;
    int64_t mask = 1;
    while (mask > 0 && n >= mask) {
        if (n & mask) {
            double t_re, t_im;
            pcc_cxp_prod(rre, rim, pre, pim, &t_re, &t_im);
            rre = t_re;
            rim = t_im;
        }
        mask <<= 1;
        double sq_re, sq_im;
        pcc_cxp_prod(pre, pim, pre, pim, &sq_re, &sq_im);
        pre = sq_re;
        pim = sq_im;
    }
    *ore = rre;
    *oim = rim;
}

/* out = x ** n for any (small) integer n (c_powi). */
static void pcc_cxp_powi(double xre, double xim, int64_t n,
                         double *ore, double *oim) {
    if (n > 0) {
        pcc_cxp_powu(xre, xim, n, ore, oim);
        return;
    }
    double d_re, d_im;
    pcc_cxp_powu(xre, xim, -n, &d_re, &d_im);
    pcc_cxp_quot(1.0, 0.0, d_re, d_im, ore, oim);
}

PyObject *py_complex_pow(PyObject *a, PyObject *b) {
    double base_re = pcc_cxp_re(a);
    double base_im = pcc_cxp_im(a);
    double ex_re = pcc_cxp_re(b);
    double ex_im = pcc_cxp_im(b);

    /* Anything ** 0 == 1+0j (including 0 ** 0). */
    if (ex_re == 0.0 && ex_im == 0.0) {
        return py_complex_new(1.0, 0.0);
    }

    /* 0 ** exp: 0 for a positive real exponent, else ZeroDivisionError. */
    if (base_re == 0.0 && base_im == 0.0) {
        if (ex_im != 0.0 || ex_re < 0.0) {
            PyObject *e = py_exc_new(PY_EXC_ZERODIVISIONERROR,
                                     "zero to a negative or complex power");
            py_raise(e);
            if (e) py_decref(e);
            return NULL;
        }
        return py_complex_new(0.0, 0.0);
    }

    /* Exact integer fast path (|exp| <= 100, real integer exponent). */
    if (ex_im == 0.0 && ex_re == floor(ex_re) && fabs(ex_re) <= 100.0) {
        double r_re, r_im;
        pcc_cxp_powi(base_re, base_im, (int64_t)ex_re, &r_re, &r_im);
        return py_complex_new(r_re, r_im);
    }

    /* General polar path. */
    double vabs = hypot(base_re, base_im);
    double len = pow(vabs, ex_re);
    double at = atan2(base_im, base_re);
    double phase = at * ex_re;
    if (ex_im != 0.0) {
        len /= exp(at * ex_im);
        phase += ex_im * log(vabs);
    }
    return py_complex_new(len * cos(phase), len * sin(phase));
}
