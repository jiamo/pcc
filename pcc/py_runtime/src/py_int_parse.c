/* pcc/py_runtime/src/py_int_parse.c
 *
 * Small string-to-int helper split from py_int.c so Phase 4c can replace
 * it with the pcc-Python port in py_int_parse.py.
 */

#include "py_internal.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

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

/* Append the CPython-style repr of the source byte ``c`` into ``buf`` at
 * position ``n``, using ``quote`` as the active quote char (so only the active
 * quote is backslash-escaped, matching CPython's quote-selection). ``buf`` must
 * have room for the worst case (4 bytes: ``\xHH``). Returns the new length. */
static int64_t repr_append_byte(char *buf, int64_t n, unsigned char c, char quote) {
    static const char hexdigits[] = "0123456789abcdef";
    if (c == '\\' || (char)c == quote) {
        buf[n++] = '\\';
        buf[n++] = (char)c;
    } else if (c == '\n') {
        buf[n++] = '\\';
        buf[n++] = 'n';
    } else if (c == '\r') {
        buf[n++] = '\\';
        buf[n++] = 'r';
    } else if (c == '\t') {
        buf[n++] = '\\';
        buf[n++] = 't';
    } else if (c < 0x20 || c == 0x7f) {
        buf[n++] = '\\';
        buf[n++] = 'x';
        buf[n++] = hexdigits[(c >> 4) & 0xf];
        buf[n++] = hexdigits[c & 0xf];
    } else {
        /* Printable ASCII and (byte-oriented) high bytes pass through, matching
         * CPython's default repr which only escapes non-printables. */
        buf[n++] = (char)c;
    }
    return n;
}

/* Build "invalid literal for int() with base <base>: <repr(s)>" into a freshly
 * malloc'd NUL-terminated buffer; caller frees. ``base`` is the ORIGINAL base
 * argument (0 renders as "base 0"), matching CPython's message. Returns NULL on
 * OOM. */
static char *build_bad_literal_message(const char *s, int base) {
    int64_t slen = (int64_t)strlen(s);
    /* Choose the quote char the way CPython does: single quote unless the
     * string contains a single quote but no double quote. */
    char quote = '\'';
    int has_single = 0;
    int has_double = 0;
    for (int64_t i = 0; i < slen; i++) {
        if (s[i] == '\'') has_single = 1;
        else if (s[i] == '"') has_double = 1;
    }
    if (has_single && !has_double) quote = '"';

    const char *prefix = "invalid literal for int() with base ";
    /* Worst case: each source byte expands to 4 chars (\\xHH). */
    int64_t cap = (int64_t)strlen(prefix) + 24 /* base digits + ": " */
                  + 2 /* quotes */ + slen * 4 + 1;
    char *buf = (char *)malloc((size_t)cap);
    if (buf == NULL) return NULL;
    int64_t n = 0;
    for (const char *p = prefix; *p != '\0'; p++) buf[n++] = *p;

    /* Render base as a signed decimal (handles 0 and, defensively, any value
     * that reached here). */
    char digits[24];
    int64_t d = 0;
    int b = base;
    int neg = 0;
    if (b < 0) { neg = 1; }
    unsigned int ub = neg ? (unsigned int)(-(long)b) : (unsigned int)b;
    if (ub == 0) {
        digits[d++] = '0';
    } else {
        while (ub != 0) { digits[d++] = (char)('0' + (ub % 10)); ub /= 10; }
    }
    if (neg) buf[n++] = '-';
    while (d > 0) buf[n++] = digits[--d];

    buf[n++] = ':';
    buf[n++] = ' ';
    buf[n++] = quote;
    for (int64_t i = 0; i < slen; i++) {
        n = repr_append_byte(buf, n, (unsigned char)s[i], quote);
    }
    buf[n++] = quote;
    buf[n] = '\0';
    return buf;
}

/* int(str) builtin: parse like py_int_from_cstr but raise ValueError on invalid
 * input instead of returning NULL (which the frontend would otherwise unbox to
 * 0 -> int('xyz') silently became 0). py_int_from_cstr stays NULL-returning for
 * other callers.
 *
 * CPython raises two distinct ValueError messages here, which this reproduces:
 *   - a bad base (not 0 and outside 2..36):
 *       "int() base must be >= 2 and <= 36, or 0"
 *   - an unparseable literal (valid base):
 *       "invalid literal for int() with base <base>: <repr(s)>"
 * ``base`` is the ORIGINAL argument (0 stays "base 0"; the resolved base is not
 * shown), and the repr is a CPython-accurate repr of the whole original string
 * (whitespace and all), with CPython quote selection and \\x/\\n/\\r/\\t escapes. */
/* int(o[, base]) with an OBJECT result.
 *
 * The frontend's `int(<dyn>)` lowering unboxes every branch to i64 and phis
 * them, which truncates a bignum to 0.  Callers that want the object
 * projection emit ONE call to this instead, so no basic blocks are created in
 * the frontend probe that asks for it.
 *
 * Returns a NEW reference, or NULL with a pending exception.  `base` applies
 * only to the string case, matching the existing lowering.
 */
PyObject *py_obj_as_int_object(PyObject *o, int base) {
    if (o == NULL) return NULL;
    int64_t tag = py_obj_type_tag(o);
    if (tag == PY_TYPE_STR) {
        return py_int_from_cstr_or_raise(py_str_utf8(o), base);
    }
    if (tag == PY_TYPE_FLOAT) {
        return py_int_from_i64((int64_t)py_float_to_f64(o));
    }
    if (tag == PY_TYPE_BOOL) {
        return py_int_from_i64(py_obj_truthy(o));
    }
    py_incref(o);
    return o;
}

PyObject *py_int_from_cstr_or_raise(const char *s, int base) {
    if (base != 0 && (base < 2 || base > 36)) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR,
                            "int() base must be >= 2 and <= 36, or 0"));
        return NULL;
    }
    PyObject *v = py_int_from_cstr(s, base);
    if (v == NULL) {
        const char *src = (s != NULL) ? s : "";
        char *msg = build_bad_literal_message(src, base);
        if (msg != NULL) {
            py_raise(py_exc_new(PY_EXC_VALUEERROR, msg));
            free(msg);
        } else {
            py_raise(py_exc_new(PY_EXC_VALUEERROR,
                                "invalid literal for int()"));
        }
    }
    return v;
}
