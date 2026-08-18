/* float.fromhex(s) — no-libpython native implementation.
 *
 * CPython's ``float.fromhex`` does NOT reuse the decimal float parser; it
 * accepts a *hexadecimal* floating-point string with an optional ``0x``
 * prefix and a binary (``p``) exponent, e.g. ``float.fromhex("0x1.8p3")``
 * -> 12.0.  A bare string such as ``"1.5"`` is therefore parsed as the hex
 * value 1 + 5/16 = 1.3125, NOT decimal 1.5.  This mirrors CPython
 * ``Objects/floatobject.c::float_fromhex`` (grammar and rounding), so the
 * two agree bit-for-bit including the ``"invalid hexadecimal
 * floating-point string"`` ValueError message.
 *
 * Grammar (whitespace only at the ends; underscores rejected):
 *
 *   [ws] [sign] ['0x'|'0X'] hexdigits ['.' hexdigits] [('p'|'P') [sign] decdigits] [ws]
 *   [ws] [sign] ('inf' | 'infinity' | 'nan')                                        [ws]
 *
 * There must be at least one hex digit across the integer/fraction parts,
 * and if a ``p`` exponent marker is present at least one decimal digit must
 * follow it.
 *
 * This C implementation remains the host-C and pcc-C oracle.  The production
 * pcc-Python archive owns the same ABI in py_obj_stubs.py and does not archive
 * this hand-written C object.
 */
#include "py_internal.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int pcc_fh_isspace(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r'
        || c == '\v' || c == '\f';
}

/* hex digit value, or -1 */
static int pcc_fh_hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int pcc_fh_ci_match(const char *s, const char *word) {
    /* Case-insensitive prefix compare of ASCII letters; returns the number
     * of chars consumed, or 0 if not a full match. */
    size_t i = 0;
    for (; word[i] != '\0'; i++) {
        char a = s[i];
        char b = word[i];
        if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
        if (a != b) return 0;
    }
    return (int)i;
}

static void pcc_fh_error(void) {
    py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
        "invalid hexadecimal floating-point string"));
}

/* Parse ``[sign] inf|infinity|nan`` starting at ``s`` (already sign-stripped
 * caller passes sign separately). Returns 1 and sets *out on match, else 0. */
static int pcc_fh_special(const char *s, double sign, double *out) {
    int n = pcc_fh_ci_match(s, "infinity");
    if (n == 0) n = pcc_fh_ci_match(s, "inf");
    if (n != 0) {
        const char *p = s + n;
        while (pcc_fh_isspace(*p)) p++;
        if (*p != '\0') return -1; /* trailing junk */
        *out = sign * (double)INFINITY;
        return 1;
    }
    n = pcc_fh_ci_match(s, "nan");
    if (n != 0) {
        const char *p = s + n;
        while (pcc_fh_isspace(*p)) p++;
        if (*p != '\0') return -1;
        *out = (double)NAN;
        return 1;
    }
    return 0;
}

/* Core hex-float parse mirroring CPython float_fromhex. On success stores the
 * value in *out and returns 1; on a grammar error returns 0 (no exception set
 * — caller raises); *out is undefined on error. */
static int pcc_fh_parse_hexfloat(const char *s, double sign, double *out) {
    /* Collect significant hex digits into coeff_start..coeff_end.
     * digits before the point count as integer part; after as fraction. */
    const char *p = s;

    /* Skip optional 0x / 0X prefix. */
    if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        p += 2;
    }

    const char *coeff_start = p;
    long ndigits_int = 0;   /* hex digits before '.' */
    long ndigits_frac = 0;  /* hex digits after '.' */

    while (pcc_fh_hexval(*p) >= 0) {
        p++;
        ndigits_int++;
    }
    const char *coeff_dot = p;      /* position of '.' or exponent/end */
    if (*p == '.') {
        p++;
        while (pcc_fh_hexval(*p) >= 0) {
            p++;
            ndigits_frac++;
        }
    }
    const char *coeff_end = p;      /* one past last hex digit / dot region */

    long total_digits = ndigits_int + ndigits_frac;
    if (total_digits == 0) {
        return 0; /* need at least one hex digit */
    }

    /* Parse optional binary exponent. */
    long exp = 0;   /* value of the 'p' exponent field */
    if (*p == 'p' || *p == 'P') {
        p++;
        int exp_sign = 1;
        if (*p == '+') {
            p++;
        } else if (*p == '-') {
            exp_sign = -1;
            p++;
        }
        if (*p < '0' || *p > '9') {
            return 0; /* 'p' with no digits */
        }
        long e = 0;
        int overflow = 0;
        while (*p >= '0' && *p <= '9') {
            if (e < 100000000L) {
                e = e * 10 + (*p - '0');
            } else {
                overflow = 1;
            }
            p++;
        }
        exp = exp_sign * e;
        if (overflow) {
            /* Enormous exponent -> +/-0 or +/-inf, decided by sign of exp. */
            exp = (exp_sign < 0) ? -1000000000L : 1000000000L;
        }
    }

    /* Trailing whitespace then end-of-string. */
    while (pcc_fh_isspace(*p)) p++;
    if (*p != '\0') {
        return 0; /* trailing junk */
    }

    /* To stay bit-exact with CPython across the full range (including
     * subnormals and ties-to-even), route through the C library's own
     * correctly-rounded hex-float reader: normalize the (prefix-optional,
     * exponent-optional) input into the canonical C99 ``0x<int>.<frac>p<exp>``
     * form and hand it to strtod, which every supported libc parses with
     * round-to-nearest-even. */

    /* Build canonical C99 hex-float string in a bounded buffer. Layout:
     *   "0x" + up to MAX_HEXDIG int digits + "." + frac digits + "p" + exp
     * We cap the digit count; CPython also rejects absurdly long inputs only
     * via memory, but for correctness we keep every provided digit (strtod
     * handles rounding of excess precision). */
    /* buffer: 0x (2) + digits + . (1) + p (1) + sign (1) + exp (11) + NUL */
    long buf_needed = total_digits + 24;
    if (buf_needed < 64) buf_needed = 64;
    char stackbuf[512];
    char *buf = stackbuf;
    char *heapbuf = NULL;
    if (buf_needed > (long)sizeof(stackbuf)) {
        heapbuf = (char *)malloc((size_t)buf_needed);
        if (heapbuf == NULL) {
            return -2; /* signal MemoryError to caller */
        }
        buf = heapbuf;
    }

    long bi = 0;
    buf[bi++] = '0';
    buf[bi++] = 'x';
    /* integer hex digits */
    for (const char *q = coeff_start; q < coeff_dot; q++) {
        buf[bi++] = *q;
    }
    if (ndigits_int == 0) {
        buf[bi++] = '0'; /* strtod wants at least one digit before '.' */
    }
    buf[bi++] = '.';
    if (ndigits_frac > 0) {
        /* fraction digits live just after the dot in the source */
        const char *frac_start = coeff_dot + 1; /* skip '.' */
        for (const char *q = frac_start; q < coeff_end; q++) {
            buf[bi++] = *q;
        }
    } else {
        buf[bi++] = '0';
    }
    /* binary exponent */
    buf[bi++] = 'p';
    {
        long e = exp;
        if (e < 0) {
            buf[bi++] = '-';
            e = -e;
        } else {
            buf[bi++] = '+';
        }
        char digs[16];
        int dn = 0;
        if (e == 0) {
            digs[dn++] = '0';
        } else {
            while (e > 0 && dn < 15) {
                digs[dn++] = (char)('0' + (e % 10));
                e /= 10;
            }
        }
        while (dn > 0) {
            buf[bi++] = digs[--dn];
        }
    }
    buf[bi] = '\0';

    char *end = NULL;
    double v = strtod(buf, &end);
    int ok = (end != NULL && *end == '\0' && end != buf);
    if (heapbuf != NULL) free(heapbuf);
    if (!ok) {
        return 0;
    }
    /* The mantissa was finite (inf/nan handled by the caller before we run),
     * so any infinite result here is an out-of-range magnitude. CPython
     * raises OverflowError rather than returning inf. Underflow to 0.0 is
     * fine (CPython returns 0.0, no error). */
    if (isinf(v)) {
        return -3; /* signal OverflowError to caller */
    }
    *out = sign * v;
    return 1;
}

PyObject *py_float_fromhex(PyObject *text) {
    if (text == NULL || PY_IS_TAGGED_INT(text)
        || py_type_of(text) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR,
            "float.fromhex() argument must be str"));
        return NULL;
    }
    const char *s = py_str_utf8(text);
    if (s == NULL) {
        py_raise_owned(py_exc_new(PY_EXC_VALUEERROR,
            "invalid hexadecimal floating-point string"));
        return NULL;
    }

    /* Leading whitespace. */
    const char *p = s;
    while (pcc_fh_isspace(*p)) p++;

    /* Optional sign. */
    double sign = 1.0;
    if (*p == '+') {
        p++;
    } else if (*p == '-') {
        sign = -1.0;
        p++;
    }

    /* inf / infinity / nan (case-insensitive). */
    double special = 0.0;
    int sp = pcc_fh_special(p, sign, &special);
    if (sp == 1) {
        return py_float_from_f64(special);
    }
    if (sp == -1) {
        pcc_fh_error();
        return NULL;
    }

    double val = 0.0;
    int rc = pcc_fh_parse_hexfloat(p, sign, &val);
    if (rc == -2) {
        /* malloc failure building the canonical strtod buffer. Raise so the
         * py_err_occurred() contract holds and generated code branches to the
         * error path (there is no PY_EXC_MEMORYERROR tag yet). */
        py_raise_owned(py_exc_new(PY_EXC_RUNTIMEERROR,
            "out of memory parsing hexadecimal float"));
        return NULL;
    }
    if (rc == -3) {
        py_raise_owned(py_exc_new(PY_EXC_OVERFLOWERROR,
            "hexadecimal value too large to represent as a float"));
        return NULL;
    }
    if (rc != 1) {
        pcc_fh_error();
        return NULL;
    }
    return py_float_from_f64(val);
}
