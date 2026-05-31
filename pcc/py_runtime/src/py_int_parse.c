/* pcc/py_runtime/src/py_int_parse.c
 *
 * Small string-to-int helper split from py_int.c so Phase 4c can replace
 * it with the pcc-Python port in py_int_parse.py.
 */

#include "py_internal.h"
#include <stdint.h>

static int is_parse_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

static int digit_value(unsigned char c) {
    if (c >= '0' && c <= '9') return (int)(c - '0');
    if (c >= 'a' && c <= 'z') return (int)(c - 'a') + 10;
    if (c >= 'A' && c <= 'Z') return (int)(c - 'A') + 10;
    return -1;
}

static int has_prefix(const char *s, int64_t i, unsigned char lo, unsigned char hi) {
    return s[i] == '0' && (s[i + 1] == (char)lo || s[i + 1] == (char)hi);
}

/* Bigint fallback for values exceeding int64; accumulates with the general
 * int ops (which handle bignum).  ``start`` is the first digit position. */
static PyObject *parse_bigint_cstr(const char *s, int64_t start, int base, int negative) {
    PyObject *base_obj = py_int_from_i64(base);
    PyObject *acc = py_int_from_i64(0);
    int64_t i = start;
    while (s[i] != '\0') {
        int d = digit_value((unsigned char)s[i]);
        if (d < 0 || d >= base) break;
        PyObject *prod = py_int_mul(acc, base_obj);
        py_decref(acc);
        PyObject *dobj = py_int_from_i64(d);
        acc = py_int_add(prod, dobj);
        py_decref(prod);
        py_decref(dobj);
        i++;
    }
    py_decref(base_obj);
    while (is_parse_space((unsigned char)s[i])) i++;
    if (s[i] != '\0') {
        py_decref(acc);
        return NULL;
    }
    if (negative) {
        PyObject *neg_one = py_int_from_i64(-1);
        PyObject *res = py_int_mul(acc, neg_one);
        py_decref(acc);
        py_decref(neg_one);
        return res;
    }
    return acc;
}

PyObject *py_int_from_cstr(const char *s, int base) {
    if (s == NULL) return NULL;
    int64_t i = 0;
    while (is_parse_space((unsigned char)s[i])) i++;

    int negative = 0;
    if (s[i] == '+' || s[i] == '-') {
        negative = s[i] == '-';
        i++;
    }

    if (base == 0) {
        if (has_prefix(s, i, 'x', 'X')) {
            base = 16;
            i += 2;
        } else if (has_prefix(s, i, 'b', 'B')) {
            base = 2;
            i += 2;
        } else if (has_prefix(s, i, 'o', 'O')) {
            base = 8;
            i += 2;
        } else if (s[i] == '0') {
            base = 8;
        } else {
            base = 10;
        }
    } else if (base == 16 && has_prefix(s, i, 'x', 'X')) {
        i += 2;
    } else if (base == 2 && has_prefix(s, i, 'b', 'B')) {
        i += 2;
    } else if (base == 8 && has_prefix(s, i, 'o', 'O')) {
        i += 2;
    }

    if (base < 2 || base > 36) return NULL;

    uint64_t limit = negative ? ((uint64_t)INT64_MAX + 1ULL) : (uint64_t)INT64_MAX;
    uint64_t value = 0;
    int saw_digit = 0;
    int64_t digits_start = i;
    while (s[i] != '\0') {
        int d = digit_value((unsigned char)s[i]);
        if (d < 0 || d >= base) break;
        uint64_t ud = (uint64_t)d;
        if (value > (limit - ud) / (uint64_t)base) {
            return parse_bigint_cstr(s, digits_start, base, negative);
        }
        value = value * (uint64_t)base + ud;
        saw_digit = 1;
        i++;
    }
    if (!saw_digit) return NULL;

    while (is_parse_space((unsigned char)s[i])) i++;
    if (s[i] != '\0') return NULL;

    if (negative) {
        if (value == ((uint64_t)INT64_MAX + 1ULL)) return py_int_from_i64(INT64_MIN);
        return py_int_from_i64(-(int64_t)value);
    }
    return py_int_from_i64((int64_t)value);
}

/* int(str) builtin: parse like py_int_from_cstr but raise ValueError on invalid
 * input instead of returning NULL (which the frontend would otherwise unbox to
 * 0 -> int('xyz') silently became 0). py_int_from_cstr stays NULL-returning for
 * other callers. */
PyObject *py_int_from_cstr_or_raise(const char *s, int base) {
    PyObject *v = py_int_from_cstr(s, base);
    if (v == NULL) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "invalid literal for int()"));
    }
    return v;
}
