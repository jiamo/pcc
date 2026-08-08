/* Host-C oracle; production libpy_runtime_pcc_py.a owns these ABIs in
 * py/py_format_runtime.py. */
#include "py_internal.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* CPython object render hook (NULL when libpython isn't linked).
 *
 * pcc's print path renders the default branch of py_format() as
 * ``<object tag=N>`` because the tag at obj+8 doesn't match any
 * pcc-native ``PY_TYPE_*`` enum. For CPython PyObjects passed in
 * via the libpython fallback path (e.g. fmt.format(...) results
 * that pcc could not lower natively), that "tag" is actually the
 * truncated low-32-bits of ob_type — pointing to a real
 * PyTypeObject. Renderable via PyObject_Str. The hook lets the
 * libpython tier (py_libpython.c) install that fallback without
 * making the no-libpython archive depend on CPython.
 *
 * Hook signature: ``int(int fd, void *obj)`` — writes to ``fd``,
 * returns 1 on success, 0 to fall through to ``<object tag=N>``.
 * Defined here so the symbol is part of OBJ_PY_CC_HELPERS and is
 * linked into both LIB_PCC_PY (no-libpython) and
 * LIB_PCC_PY_LIBPYTHON; the hook variable stays NULL in the former.
 */
int (*py_format_cpy_object_hook)(int fd, void *obj) = NULL;

int py_format_try_cpy_object_into_fd(int fd, void *obj, int32_t tag) {
    if (py_format_cpy_object_hook == NULL) return 0;
    if (obj == NULL) return 0;
    /* pcc-native PyTypeTag values currently top out at
     * PY_TYPE_VALUEBOX = 200; allocated user-class tags start at
     * PY_TYPE_USER_CLASS_START. Any tag below ~1024 is plausibly
     * pcc-native; anything
     * above is almost certainly a CPython ob_type pointer
     * truncation (heap addresses are typically in the millions).
     * The guard avoids invoking PyObject_Str on a value pcc itself
     * allocated. */
    if (tag >= 0 && tag <= 1023) return 0;
    return py_format_cpy_object_hook(fd, obj);
}

static int ptr_can_have_header(void *ptr) {
    return pcc_gc_pointer_is_managed((PyObject *)ptr) != 0;
}

static PyObject *format_require_result(
    PyObject *result,
    const char *helper_name,
    const char *message
) {
    if (result == NULL) {
        py_runtime_error_if_unset(helper_name, message);
    }
    return result;
}

static PyObject *call_format_method(PyObject *method, PyObject *self,
                                    PyObject *spec) {
    if (method == NULL) return NULL;
    int made_spec = spec == NULL;
    if (made_spec) {
        spec = py_str_new("", 0);
        if (spec == NULL) {
            return format_require_result(
                NULL,
                "py_str_new",
                "format callback could not allocate an empty format spec"
            );
        }
    }
    if (ptr_can_have_header(method) && !PY_IS_TAGGED_INT(method)
        && py_type_of(method) == PY_TYPE_FUNC) {
        /* Bound PyFunc whose captures already hold ``self``. User-code
         * call convention is "args excludes self"; the bound-method
         * entry adds ``self`` from captures when invoking the raw
         * ``__format__(self, spec)``. Prepending ``self`` here would
         * shift the spec into the redundant-self slot and the raw fn
         * would see spec = self_redundant (an instance object), which
         * shows up as ``TypeError: unsupported operand type(s) for +``
         * when the user body does ``"fmt:" + spec``. */
        PyObject *args = py_tuple_new(1);
        if (args == NULL) {
            if (made_spec) py_decref(spec);
            return format_require_result(
                NULL,
                "py_tuple_new",
                "format callback argument tuple allocation failed"
            );
        }
        py_tuple_set_item(args, 0, spec);
        if (made_spec) py_decref(spec);
        PyObject *out = py_func_call(method, args);
        format_require_result(
            out,
            "__format__",
            "format callback returned NULL without setting an exception"
        );
        py_decref(args);
        return out;
    }
    typedef PyObject *(*FormatMethod)(PyObject *, PyObject *);
    FormatMethod fn = (FormatMethod)(uintptr_t)method;
    PyObject *out = fn(self, spec);
    format_require_result(
        out,
        "__format__",
        "format callback returned NULL without setting an exception"
    );
    if (made_spec) py_decref(spec);
    return out;
}

static int spec_is(PyObject *spec, const char *text) {
    if (spec == NULL || spec == py_None) return text[0] == '\0';
    if (py_type_of(spec) != PY_TYPE_STR) return text[0] == '\0';
    const char *s = py_str_utf8(spec);
    if (s == NULL) return 0;
    return strcmp(s, text) == 0;
}

static const char *spec_text(PyObject *spec) {
    if (spec == NULL || spec == py_None) return "";
    if (py_type_of(spec) != PY_TYPE_STR) return "";
    const char *s = py_str_utf8(spec);
    return s != NULL ? s : "";
}

static int parse_i64_digits(const char **p, int64_t *out) {
    int64_t v = 0;
    int any = 0;
    while (**p >= '0' && **p <= '9') {
        any = 1;
        v = v * 10 + (int64_t)(**p - '0');
        (*p)++;
    }
    *out = v;
    return any;
}

static PyObject *pad_ascii_text(const char *text, int64_t len, int64_t width,
                                char align, int zero_pad, char fill) {
    if (text == NULL) text = "";
    if (len < 0) len = (int64_t)strlen(text);
    if (width < len) width = len;
    int64_t pad = width - len;
    int64_t left = 0;
    int64_t right = 0;
    if (align == '<') {
        right = pad;
    } else if (align == '^') {
        left = pad / 2;
        right = pad - left;
    } else {
        left = pad;
    }

    char pad_char = zero_pad ? '0' : fill;
    char *buf = (char *)malloc((size_t)width + 1u);
    if (buf == NULL) return NULL;
    int64_t pos = 0;
    for (int64_t i = 0; i < left; i++) buf[pos++] = pad_char;
    memcpy(buf + pos, text, (size_t)len);
    pos += len;
    for (int64_t i = 0; i < right; i++) buf[pos++] = pad_char;
    PyObject *out = py_str_new(buf, pos);
    free(buf);
    return out;
}

static PyObject *pad_signed_ascii_text(const char *text, int64_t len,
                                       int64_t width, char align,
                                       int zero_pad, char fill) {
    if (!zero_pad || align != '>') {
        return pad_ascii_text(text, len, width, align, 0, fill);
    }
    if (len >= width) return py_str_new(text, len);
    char sign = 0;
    if (len > 0 && (text[0] == '-' || text[0] == '+')) sign = text[0];
    int64_t zeros = width - len;
    char *buf = (char *)malloc((size_t)width + 1u);
    if (buf == NULL) return NULL;
    int64_t pos = 0;
    int64_t start = 0;
    if (sign) {
        buf[pos++] = sign;
        start = 1;
    }
    for (int64_t i = 0; i < zeros; i++) buf[pos++] = '0';
    memcpy(buf + pos, text + start, (size_t)(len - start));
    pos += len - start;
    PyObject *out = py_str_new(buf, pos);
    free(buf);
    return out;
}

static PyObject *format_string_builtin(PyObject *o, const char *spec) {
    const char *p = spec;
    char align = '<';   /* CPython str default alignment is left */
    char fill = ' ';
    /* Optional [[fill]align] (CPython format mini-language): a leading fill
     * character is present only when the *second* char is an alignment char,
     * e.g. "*^11" -> fill='*', align='^'. A lone alignment char keeps the
     * default space fill. */
    if (*p != '\0' && (p[1] == '<' || p[1] == '>' || p[1] == '^')) {
        fill = p[0];
        align = p[1];
        p += 2;
    } else if (*p == '<' || *p == '>' || *p == '^') {
        align = *p++;
    }
    int64_t width = 0;
    (void)parse_i64_digits(&p, &width);
    int64_t precision = -1;
    if (*p == '.') {
        p++;
        if (!parse_i64_digits(&p, &precision)) return NULL;
    }
    if (*p != '\0') return NULL;

    const char *text = py_str_utf8(o);
    int64_t len = py_str_byte_len(o);
    if (precision >= 0 && precision < len) len = precision;
    return pad_ascii_text(text, len, width, align, 0, fill);
}

static PyObject *format_int_builtin(PyObject *o, const char *spec) {
    const char *p = spec;
    char align = '>';
    char fill = ' ';
    int sign_plus = 0;
    int sign_space = 0;
    int zero_pad = 0;
    int comma = 0;
    int alt = 0;
    /* Optional [[fill]align]: a fill char is present only when the second
     * char is an alignment char (CPython format mini-language). */
    if (*p != '\0' && (p[1] == '<' || p[1] == '>' || p[1] == '^')) {
        fill = p[0];
        align = p[1];
        p += 2;
    } else if (*p == '<' || *p == '>' || *p == '^') {
        align = *p++;
    }
    if (*p == '+') {
        sign_plus = 1;
        p++;
    } else if (*p == ' ') {
        sign_space = 1;
        p++;
    }
    if (*p == '#') {
        alt = 1;
        p++;
    }
    if (*p == '0') {
        zero_pad = 1;
        p++;
    }
    int64_t width = 0;
    (void)parse_i64_digits(&p, &width);
    if (*p == ',') {
        comma = ',';
        p++;
    } else if (*p == '_') {
        comma = '_';
        p++;
    }
    char conv = 'd';
    if (*p == 'd' || *p == 'x' || *p == 'X' || *p == 'o' || *p == 'b') {
        conv = *p++;
    }
    if (*p != '\0') return NULL;
    if (alt && conv == 'd') return NULL;   /* # has no effect on 'd' */

    PyObject *raw;
    if (conv == 'x' || conv == 'X') {
        PyObject *h = py_int_format_hex(o, 0, 0);   /* lowercase, maybe '-' */
        if (h == NULL) return NULL;
        const char *hs = py_str_utf8(h);
        int64_t hl = py_str_byte_len(h);
        int neg = (hl > 0 && hs[0] == '-');
        int64_t dstart = neg ? 1 : 0;
        char *buf = (char *)malloc((size_t)hl + 4u);
        if (buf == NULL) { py_decref(h); return NULL; }
        int64_t pos = 0;
        if (neg) buf[pos++] = '-';
        if (alt) {
            buf[pos++] = '0';
            buf[pos++] = (conv == 'X') ? 'X' : 'x';
        }
        for (int64_t k = dstart; k < hl; k++) {
            char c = hs[k];
            if (conv == 'X' && c >= 'a' && c <= 'f') c = (char)(c - 'a' + 'A');
            buf[pos++] = c;
        }
        raw = py_str_new(buf, pos);
        free(buf);
        py_decref(h);
    } else if (conv == 'o' || conv == 'b') {
        int overflow = 0;
        int64_t sv = py_int_to_i64(o, &overflow);
        if (overflow) return NULL;          /* bignum oct/bin: fall back */
        unsigned base = (conv == 'o') ? 8u : 2u;
        int neg = sv < 0;
        uint64_t mag = neg ? (uint64_t)(-(sv + 1)) + 1u : (uint64_t)sv;
        char rev[72];
        int nd = 0;
        do {
            rev[nd++] = (char)('0' + (int)(mag % base));
            mag /= base;
        } while (mag != 0 && nd < (int)sizeof(rev));
        char buf[96];
        int pos = 0;
        if (neg) buf[pos++] = '-';
        if (alt) {
            buf[pos++] = '0';
            buf[pos++] = (conv == 'o') ? 'o' : 'b';
        }
        for (int k = nd - 1; k >= 0; k--) buf[pos++] = rev[k];
        raw = py_str_new(buf, pos);
    } else {
        raw = py_int_format_decimal(o, 0, 0, comma);
    }
    if (raw == NULL) return NULL;
    const char *text = py_str_utf8(raw);
    int64_t len = py_str_byte_len(raw);
    PyObject *signed_raw = raw;
    if ((sign_plus || sign_space) && len > 0 && text[0] != '-') {
        char *buf = (char *)malloc((size_t)len + 2u);
        if (buf == NULL) {
            py_decref(raw);
            return NULL;
        }
        buf[0] = sign_plus ? '+' : ' ';   /* ' ' = the space sign option */
        memcpy(buf + 1, text, (size_t)len);
        signed_raw = py_str_new(buf, len + 1);
        free(buf);
        py_decref(raw);
        if (signed_raw == NULL) return NULL;
        text = py_str_utf8(signed_raw);
        len = py_str_byte_len(signed_raw);
    }
    if (alt && zero_pad && align == '>' && width > len) {
        /* Zero-pad AFTER the 0x/0o/0b prefix (and any sign), CPython-style:
         * ``f"{42:#06x}"`` -> ``0x002a`` (not ``000x2a``). text is
         * ``[sign]0[xob]<digits>``; insert the fill zeros between the 2-char
         * base prefix and the digits. */
        int64_t pfx = 0;
        if (len > 0 && (text[0] == '-' || text[0] == '+')) pfx = 1;
        pfx += 2;  /* "0x" / "0o" / "0b" */
        int64_t zeros = width - len;
        char *zbuf = (char *)malloc((size_t)width);
        if (zbuf == NULL) {
            py_decref(signed_raw);
            return NULL;
        }
        memcpy(zbuf, text, (size_t)pfx);
        for (int64_t k = 0; k < zeros; k++) zbuf[pfx + k] = '0';
        memcpy(zbuf + pfx + zeros, text + pfx, (size_t)(len - pfx));
        PyObject *padded = py_str_new(zbuf, width);
        free(zbuf);
        py_decref(signed_raw);
        return padded;
    }
    PyObject *out = pad_signed_ascii_text(text, len, width, align, zero_pad, fill);
    py_decref(signed_raw);
    return out;
}

/* CPython-style float repr: the shortest decimal string that round-trips back
 * to the same double. Used by str()/repr()/print() of a float in BOTH runtime
 * tiers (the pcc-Python ports py_print_fmt.py/py_obj_stubs.py call this instead
 * of the old fixed-6-decimal py_float_format_fixed, which printed e.g.
 * "3.333333" for 10/3 rather than CPython's "3.3333333333333335"). Portable
 * (no Grisu/Ryu): try increasing significant-digit precision until strtod of
 * the formatted text equals the original value; 17 sig digits always round-
 * trips an IEEE-754 double. Appends ".0" when the result has no '.'/'e' so
 * integer-valued floats render as "5.0" not "5". */
PyObject *py_float_repr_shortest(PyObject *o) {
    double v = py_float_to_f64(o);
    if (v != v) {
        return py_str_new("nan", 3);
    }
    if (v != 0.0 && v == v * 2.0) {  /* +/-inf: x == 2*x only for 0 and inf */
        return v < 0.0 ? py_str_new("-inf", 4) : py_str_new("inf", 3);
    }
    if (v == 0.0) {  /* preserve the sign of zero, like CPython repr(-0.0) */
        return (1.0 / v < 0.0) ? py_str_new("-0.0", 4) : py_str_new("0.0", 3);
    }
    /* Shortest round-trip: smallest significant-digit count (1..17) whose
     * "%.*e" formatting parses back (strtod) to the exact same double. Using
     * "%.*e" gives a format-independent digit count and a clean decimal
     * exponent; 17 significant digits always round-trip an IEEE-754 double. */
    char ebuf[64];
    int sig;
    for (sig = 1; sig <= 17; sig++) {
        snprintf(ebuf, sizeof(ebuf), "%.*e", sig - 1, v);
        if (strtod(ebuf, NULL) == v) {
            break;
        }
    }
    if (sig > 17) {
        sig = 17;
    }
    /* Decimal exponent E from the "d.ddde+XX" form. */
    const char *epos = strchr(ebuf, 'e');
    int E = (epos != NULL) ? atoi(epos + 1) : 0;
    char out[80];
    if (E >= -4 && E < 16) {
        /* Fixed notation (CPython uses scientific only for E < -4 or E >= 16). */
        int decimals = sig - 1 - E;
        if (decimals < 0) {
            decimals = 0;
        }
        snprintf(out, sizeof(out), "%.*f", decimals, v);
        if (strchr(out, '.') == NULL) {  /* integer-valued -> append ".0" */
            size_t len = strlen(out);
            if (len + 2 < sizeof(out)) {
                out[len] = '.';
                out[len + 1] = '0';
                out[len + 2] = '\0';
            }
        }
    } else {
        /* Scientific notation: mantissa (sig-1 decimals) with trailing zeros
         * stripped, then "e", a sign, and >=2 exponent digits (CPython style:
         * "1e+16", "1e-05", "1.5e+300"). */
        char mant[48];
        size_t mlen = (epos != NULL) ? (size_t)(epos - ebuf) : strlen(ebuf);
        if (mlen >= sizeof(mant)) {
            mlen = sizeof(mant) - 1;
        }
        memcpy(mant, ebuf, mlen);
        mant[mlen] = '\0';
        if (strchr(mant, '.') != NULL) {
            size_t j = strlen(mant);
            while (j > 0 && mant[j - 1] == '0') {
                mant[--j] = '\0';
            }
            if (j > 0 && mant[j - 1] == '.') {
                mant[--j] = '\0';
            }
        }
        snprintf(out, sizeof(out), "%se%+03d", mant, E);
    }
    return py_str_new(out, (int64_t)strlen(out));
}

/* float(x): when x is a str, parse it via strtod (correctly rounded, handles
 * inf/nan and scientific notation) and raise ValueError on a bad/partial
 * string; otherwise delegate to py_float_to_f64. C-only helper, linked into
 * both runtime tiers, so float(<str>) no longer wrongly returns 0.0 (the old
 * py_float_to_f64 path had no PY_TYPE_STR case). */
double py_float_value_of(PyObject *o) {
    if (o != NULL && !PY_IS_TAGGED_INT(o)
        && py_header(o)->type_tag == PY_TYPE_STR) {
        const char *s = ((const PyStrObject *)o)->data;
        while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r'
               || *s == '\v' || *s == '\f') {
            s++;
        }
        char *end = NULL;
        double v = strtod(s, &end);
        if (end == s) {
            py_raise(py_exc_new(PY_EXC_VALUEERROR,
                "could not convert string to float"));
            return 0.0;
        }
        while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r'
               || *end == '\v' || *end == '\f') {
            end++;
        }
        if (*end != '\0') {
            py_raise(py_exc_new(PY_EXC_VALUEERROR,
                "could not convert string to float"));
            return 0.0;
        }
        return v;
    }
    return py_float_to_f64(o);
}

double pcc_float_round_fixed_f64(double v, int64_t ndigits) {
    if (v != v) return v;
    if (v != 0.0 && v == v * 2.0) return v;

    if (ndigits >= 0) {
        if (ndigits > 200) return v;
        char fmt[16];
        char buf[768];
        snprintf(fmt, sizeof(fmt), "%%.%df", (int)ndigits);
        int n = snprintf(buf, sizeof(buf), fmt, v);
        if (n < 0 || n >= (int)sizeof(buf)) return v;
        return strtod(buf, NULL);
    }

    if (ndigits < -308) {
        return copysign(0.0, v);
    }
    double scale = pow(10.0, (double)(-ndigits));
    if (scale == 0.0 || scale != scale || scale == scale * 2.0) {
        return v;
    }
    return rint(v / scale) * scale;
}

static PyObject *add_float_commas(const char *text, char sep) {
    const char *dot = strchr(text, '.');
    int64_t int_len = dot ? (int64_t)(dot - text) : (int64_t)strlen(text);
    int sign = int_len > 0 && (text[0] == '-' || text[0] == '+');
    int64_t digits = int_len - sign;
    int64_t commas = digits > 3 ? (digits - 1) / 3 : 0;
    int64_t tail_len = (int64_t)strlen(text) - int_len;
    int64_t out_len = int_len + commas + tail_len;
    char *buf = (char *)malloc((size_t)out_len + 1u);
    if (buf == NULL) return NULL;
    int64_t pos = 0;
    int64_t start = 0;
    if (sign) buf[pos++] = text[start++];
    for (int64_t i = 0; i < digits; i++) {
        if (i > 0 && ((digits - i) % 3) == 0) buf[pos++] = sep;
        buf[pos++] = text[start + i];
    }
    if (tail_len > 0) {
        memcpy(buf + pos, text + int_len, (size_t)tail_len);
        pos += tail_len;
    }
    PyObject *out = py_str_new(buf, pos);
    free(buf);
    return out;
}

static PyObject *format_float_builtin(PyObject *o, const char *spec) {
    /* Float spec grammar (subset):
     *   [align][sign][0][width][,][.precision][f|F|e|E|g|G]
     * Mirrors format_int_builtin's align/sign/zero-pad/width handling so
     * width+precision specs like "8.3f", ">10.2f", "08.2f", "+.2f" work
     * (previously only ",", ".Nf", "f"/"e" with no width were supported). */
    const char *p = spec;
    char align = '>';
    char fill = ' ';
    int sign_plus = 0;
    int sign_space = 0;
    int zero_pad = 0;
    int comma = 0;
    /* Optional [[fill]align] (see format_int_builtin). */
    if (*p != '\0' && (p[1] == '<' || p[1] == '>' || p[1] == '^')) {
        fill = p[0];
        align = p[1];
        p += 2;
    } else if (*p == '<' || *p == '>' || *p == '^') {
        align = *p++;
    }
    if (*p == '+') {
        sign_plus = 1;
        p++;
    } else if (*p == ' ') {
        sign_space = 1;
        p++;
    }
    if (*p == '0') {
        zero_pad = 1;
        p++;
    }
    int64_t width = 0;
    (void)parse_i64_digits(&p, &width);
    if (*p == ',') {
        comma = ',';
        p++;
    } else if (*p == '_') {
        comma = '_';
        p++;
    }
    int64_t precision = 6;
    int has_precision = 0;
    if (*p == '.') {
        p++;
        if (!parse_i64_digits(&p, &precision)) return NULL;
        has_precision = 1;
    }
    char conv = 0;
    if (*p == 'f' || *p == 'F' || *p == 'e' || *p == 'E' ||
        *p == 'g' || *p == 'G' || *p == '%') {
        conv = *p++;
    }
    if (*p != '\0') return NULL;
    if (precision < 0 || precision > 64) return NULL;

    PyObject *raw;
    if (conv == '%') {
        /* percent: value*100 formatted as fixed-point with a trailing '%'
         * (e.g. f"{0.5:.1%}" -> "50.0%", f"{0.5:%}" -> "50.000000%"). */
        char fmt[16];
        snprintf(fmt, sizeof(fmt), "%%.%lldf", (long long)precision);
        char numbuf[240];
        snprintf(numbuf, sizeof(numbuf), fmt, py_float_to_f64(o) * 100.0);
        char buf[256];
        snprintf(buf, sizeof(buf), "%s%%", numbuf);
        raw = py_str_new(buf, (int64_t)strlen(buf));
    } else if (conv == 0 && !has_precision) {
        /* Bare spec with no type and no precision (e.g. just a width like
         * "8"): use str(float) for the value text, then pad. Matches CPython
         * (e.g. f"{3.14:8}" -> "    3.14"), and is a strict improvement over
         * the previous behaviour, which raised on any width here. */
        raw = py_obj_str(o);
        if (raw != NULL && comma) {
            PyObject *c = add_float_commas(py_str_utf8(raw), (char)comma);
            py_decref(raw);
            raw = c;
        }
    } else {
        char fmt[16];
        snprintf(fmt, sizeof(fmt), "%%.%lld%c", (long long)precision,
                 conv ? conv : 'f');
        char buf[256];
        snprintf(buf, sizeof(buf), fmt, py_float_to_f64(o));
        if (comma && (conv == 'f' || conv == 'F' || conv == 0)) {
            raw = add_float_commas(buf, (char)comma);
        } else {
            raw = py_str_new(buf, (int64_t)strlen(buf));
        }
    }
    if (raw == NULL) return NULL;

    const char *text = py_str_utf8(raw);
    int64_t len = py_str_byte_len(raw);
    PyObject *signed_raw = raw;
    if ((sign_plus || sign_space) && len > 0 && text[0] != '-') {
        char *sbuf = (char *)malloc((size_t)len + 2u);
        if (sbuf == NULL) {
            py_decref(raw);
            return NULL;
        }
        sbuf[0] = sign_plus ? '+' : ' ';
        memcpy(sbuf + 1, text, (size_t)len);
        signed_raw = py_str_new(sbuf, len + 1);
        free(sbuf);
        py_decref(raw);
        if (signed_raw == NULL) return NULL;
        text = py_str_utf8(signed_raw);
        len = py_str_byte_len(signed_raw);
    }
    PyObject *out = pad_signed_ascii_text(text, len, width, align, zero_pad, fill);
    py_decref(signed_raw);
    return out;
}

/* repr(exc) for a builtin exception: ``ClassName(repr(arg))`` for a single
 * message, ``ClassName()`` when arg-less (CPython exception repr). Lives in this
 * always-linked C-only formatting helper so both py_obj_repr tiers (the C
 * py_obj_stubs.c and the pcc-Python port) share ONE implementation instead of
 * the port awkwardly re-reading the exc_class/name struct fields. */
extern PyObject *py_obj_repr(PyObject *o);
PyObject *py_exc_repr(PyObject *o) {
    if (o == NULL || py_type_of(o) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)o;
    PyObject *cls_obj = pcc_gc_load_ptr(o, (PyObject **)&e->exc_class);
    PyClassObject *cls = (PyClassObject *)cls_obj;
    const char *name = (cls != NULL && cls->name != NULL) ? cls->name
                                                          : "Exception";
    int64_t nlen = (int64_t)strlen(name);
    PyObject *msg = py_exc_get_message(o);
    /* pcc stores a single stringified ``message`` (args[0]), so an arg-less
     * exception and one built with "" are indistinguishable; treat an empty
     * message as arg-less -> ClassName() (the common case, e.g. raise
     * StopIteration()). A genuine ``Exc("")`` then also renders as ClassName().
     * Non-string args (e.g. KeyError(5)) are stored stringified, so their repr
     * shows the string form ('5'); faithful arg-type repr needs the original
     * args tuple (a deeper exc-object change). */
    int msg_empty = (msg != NULL && msg != py_None
                     && py_type_of(msg) == PY_TYPE_STR
                     && py_str_byte_len(msg) == 0);
    if (msg == NULL || msg == py_None || msg_empty) {
        char *buf = (char *)malloc((size_t)nlen + 3u);
        if (buf == NULL) return NULL;
        memcpy(buf, name, (size_t)nlen);
        buf[nlen] = '(';
        buf[nlen + 1] = ')';
        PyObject *out = py_str_new(buf, nlen + 2);
        free(buf);
        return out;
    }
    PyObject *r = py_obj_repr(msg);
    if (r == NULL) return NULL;
    const char *rtext = py_str_utf8(r);
    int64_t rlen = py_str_byte_len(r);
    char *buf = (char *)malloc((size_t)(nlen + rlen) + 3u);
    if (buf == NULL) {
        py_decref(r);
        return NULL;
    }
    int64_t pos = 0;
    memcpy(buf, name, (size_t)nlen);
    pos = nlen;
    buf[pos++] = '(';
    memcpy(buf + pos, rtext, (size_t)rlen);
    pos += rlen;
    buf[pos++] = ')';
    PyObject *out = py_str_new(buf, pos);
    free(buf);
    py_decref(r);
    return out;
}

/* Complex arithmetic (sub/mul/div/neg/conjugate/abs). C-only, always-linked
 * here so there is no port mirror and abs can use sqrt (py_complex_add already
 * lives in py_obj_stubs.c). Operands may be complex/int/float/bool — Python
 * coerces the non-complex side to a real. */
extern PyObject *py_complex_new(double real, double imag);
extern double py_bigint_to_double(const PyIntObject *o);
static double pcc_cx_re(PyObject *o) {
    if (o == NULL) return 0.0;
    if (PY_IS_TAGGED_INT(o)) return (double)py_untag_int(o);
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_COMPLEX) return ((PyComplexObject *)o)->real;
    if (tag == PY_TYPE_FLOAT) return ((PyFloatObject *)o)->value;
    if (tag == PY_TYPE_INT) return py_bigint_to_double((const PyIntObject *)o);
    if (tag == PY_TYPE_BOOL) return o == py_True ? 1.0 : 0.0;
    return 0.0;
}
static double pcc_cx_im(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0.0;
    if (py_header(o)->type_tag == PY_TYPE_COMPLEX)
        return ((PyComplexObject *)o)->imag;
    return 0.0;
}
PyObject *py_complex_sub(PyObject *a, PyObject *b) {
    return py_complex_new(pcc_cx_re(a) - pcc_cx_re(b),
                          pcc_cx_im(a) - pcc_cx_im(b));
}
PyObject *py_complex_mul(PyObject *a, PyObject *b) {
    double ar = pcc_cx_re(a), ai = pcc_cx_im(a);
    double br = pcc_cx_re(b), bi = pcc_cx_im(b);
    return py_complex_new(ar * br - ai * bi, ar * bi + ai * br);
}
PyObject *py_complex_div(PyObject *a, PyObject *b) {
    double ar = pcc_cx_re(a), ai = pcc_cx_im(a);
    double br = pcc_cx_re(b), bi = pcc_cx_im(b);
    double den = br * br + bi * bi;
    if (den == 0.0) {
        PyObject *e = py_exc_new(PY_EXC_ZERODIVISIONERROR,
                                 "complex division by zero");
        py_raise(e);
        if (e) py_decref(e);
        return NULL;
    }
    return py_complex_new((ar * br + ai * bi) / den,
                          (ai * br - ar * bi) / den);
}
PyObject *py_complex_neg(PyObject *a) {
    return py_complex_new(-pcc_cx_re(a), -pcc_cx_im(a));
}
PyObject *py_complex_conjugate(PyObject *a) {
    return py_complex_new(pcc_cx_re(a), -pcc_cx_im(a));
}
PyObject *py_complex_abs(PyObject *a) {
    double r = pcc_cx_re(a), i = pcc_cx_im(a);
    return py_float_from_f64(sqrt(r * r + i * i));
}

/* Format one complex component as CPython does: shortest float repr with a
 * trailing ".0" stripped (4.0 -> "4", 2.5 -> "2.5", inf/nan kept). */
static void pcc_cx_component(double v, char *buf, size_t cap) {
    PyObject *f = py_float_from_f64(v);
    PyObject *s = (f != NULL) ? py_float_repr_shortest(f) : NULL;
    if (s == NULL) {
        snprintf(buf, cap, "0");
        if (f != NULL) py_decref(f);
        return;
    }
    const char *t = py_str_utf8(s);
    int64_t n = py_str_byte_len(s);
    if (n >= 2 && t[n - 2] == '.' && t[n - 1] == '0') n -= 2;
    if ((size_t)n >= cap) n = (int64_t)cap - 1;
    memcpy(buf, t, (size_t)n);
    buf[n] = '\0';
    py_decref(s);
    py_decref(f);
}

/* repr(complex) == str(complex): "(re+imj)" / "(re-imj)", or "imj" when the
 * real part is +0.0 (CPython: complex(0,1) -> "1j"). */
PyObject *py_complex_repr(PyObject *o) {
    if (o == NULL || py_type_of(o) != PY_TYPE_COMPLEX) return NULL;
    double re = ((PyComplexObject *)o)->real;
    double im = ((PyComplexObject *)o)->imag;
    char rbuf[64], ibuf[64], out[160];
    if (re == 0.0 && !signbit(re)) {
        pcc_cx_component(im, ibuf, sizeof ibuf);
        snprintf(out, sizeof out, "%sj", ibuf);
    } else {
        pcc_cx_component(re, rbuf, sizeof rbuf);
        char sign = (im < 0.0 || (im == 0.0 && signbit(im))) ? '-' : '+';
        pcc_cx_component(im < 0.0 ? -im : im, ibuf, sizeof ibuf);
        snprintf(out, sizeof out, "(%s%c%sj)", rbuf, sign, ibuf);
    }
    return py_str_new(out, (int64_t)strlen(out));
}

PyObject *py_obj_format(PyObject *o, PyObject *spec) {
    if (o == NULL) return NULL;
    if (!PY_IS_TAGGED_INT(o)) {
        PyObject *method = py_obj_getattr(o, "__format__");
        if (method != NULL) {
            PyObject *out = call_format_method(method, o, spec);
            if (out != NULL || py_err_occurred()) return out;
        }
        if (py_err_occurred()) py_clear_exception();
    }

    const char *text = spec_text(spec);
    if (spec == NULL || spec == py_None || text[0] == '\0') {
        return py_obj_str(o);
    }
    int32_t tag = PY_IS_TAGGED_INT(o) ? PY_TYPE_INT : py_type_of(o);
    PyObject *builtin = NULL;
    if (tag == PY_TYPE_INT) {
        builtin = format_int_builtin(o, text);
    } else if (tag == PY_TYPE_STR) {
        builtin = format_string_builtin(o, text);
    } else if (tag == PY_TYPE_FLOAT) {
        builtin = format_float_builtin(o, text);
    }
    if (builtin != NULL) return builtin;
    py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format specifier"));
    return NULL;
}

typedef struct {
    char *data;
    int64_t len;
    int64_t cap;
} PercentFormatBuf;

static int pfbuf_reserve(PercentFormatBuf *b, int64_t extra) {
    if (extra < 0) return -1;
    int64_t need = b->len + extra;
    if (need <= b->cap) return 0;
    int64_t next = b->cap > 0 ? b->cap : 64;
    while (next < need) {
        if (next > (INT64_MAX / 2)) return -1;
        next *= 2;
    }
    char *data = (char *)realloc(b->data, (size_t)next + 1u);
    if (data == NULL) return -1;
    b->data = data;
    b->cap = next;
    return 0;
}

static int pfbuf_append(PercentFormatBuf *b, const char *s, int64_t n) {
    if (n <= 0) return 0;
    if (pfbuf_reserve(b, n) != 0) return -1;
    memcpy(b->data + b->len, s, (size_t)n);
    b->len += n;
    b->data[b->len] = '\0';
    return 0;
}

static int pfbuf_append_char(PercentFormatBuf *b, char c) {
    return pfbuf_append(b, &c, 1);
}

static PyObject *percent_get_arg(PyObject *args, int64_t *index, int *tuple_mode) {
    if (args == NULL) return NULL;
    int32_t tag = py_type_of(args);
    if (tag == PY_TYPE_TUPLE) {
        *tuple_mode = 1;
        PyTupleObject *t = (PyTupleObject *)args;
        if (*index >= t->len) {
            py_raise(py_exc_new(PY_EXC_TYPEERROR, "not enough arguments for format string"));
            return NULL;
        }
        PyObject *item = pcc_gc_load_ptr(args, &t->items[*index]);
        *index += 1;
        return item;
    }
    *tuple_mode = 0;
    if (*index != 0) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "not enough arguments for format string"));
        return NULL;
    }
    *index = 1;
    return args;
}

static int percent_append_pystr(PercentFormatBuf *out, PyObject *s) {
    if (s == NULL || py_type_of(s) != PY_TYPE_STR) return -1;
    return pfbuf_append(out, py_str_utf8(s), py_str_byte_len(s));
}

static int percent_copy_spec(
    char *dst,
    int dst_cap,
    const char *src,
    int64_t spec_len,
    char conv,
    const char *prefix
) {
    int pos = 0;
    if (dst_cap <= 0 || spec_len < 2) return -1;
    dst[pos++] = '%';
    for (int64_t i = 1; i < spec_len - 1; i++) {
        if (pos + 1 >= dst_cap) return -1;
        if (src[i] == '*') return -1;
        dst[pos++] = src[i];
    }
    if (prefix != NULL) {
        for (const char *p = prefix; *p != '\0'; p++) {
            if (pos + 1 >= dst_cap) return -1;
            dst[pos++] = *p;
        }
    }
    if (pos + 1 >= dst_cap) return -1;
    dst[pos++] = conv;
    dst[pos] = '\0';
    return 0;
}

static int percent_append_cfmt(PercentFormatBuf *out, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int need = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (need < 0) {
        va_end(ap2);
        return -1;
    }
    if (pfbuf_reserve(out, (int64_t)need) != 0) {
        va_end(ap2);
        return -1;
    }
    vsnprintf(out->data + out->len, (size_t)need + 1u, fmt, ap2);
    va_end(ap2);
    out->len += (int64_t)need;
    return 0;
}

static int percent_append_formatted_str(
    PercentFormatBuf *out,
    const char *spec,
    int64_t spec_len,
    PyObject *arg,
    int repr
) {
    PyObject *s = repr ? py_obj_repr(arg) : py_obj_str(arg);
    if (s == NULL) s = py_obj_repr(arg);
    if (s == NULL || py_type_of(s) != PY_TYPE_STR) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "format argument cannot be converted to string"));
        return -1;
    }
    const char *text = py_str_utf8(s);
    int64_t len = py_str_byte_len(s);
    int64_t width = 0;
    int64_t precision = -1;
    int left = 0;
    for (int64_t i = 1; i < spec_len - 1; i++) {
        char c = spec[i];
        if (c == '-') {
            left = 1;
        } else if (c >= '0' && c <= '9') {
            width = width * 10 + (int64_t)(c - '0');
        } else if (c == '.') {
            precision = 0;
            i++;
            while (i < spec_len - 1 && spec[i] >= '0' && spec[i] <= '9') {
                precision = precision * 10 + (int64_t)(spec[i] - '0');
                i++;
            }
            i--;
        }
    }
    if (precision >= 0 && precision < len) len = precision;
    int64_t pad = width > len ? width - len : 0;
    int ok = 0;
    if (!left) {
        for (int64_t i = 0; i < pad; i++) ok |= pfbuf_append_char(out, ' ');
    }
    ok |= pfbuf_append(out, text, len);
    if (left) {
        for (int64_t i = 0; i < pad; i++) ok |= pfbuf_append_char(out, ' ');
    }
    py_decref(s);
    return ok == 0 ? 0 : -1;
}

PyObject *py_str_mod(PyObject *fmt_obj, PyObject *args) {
    if (fmt_obj == NULL || py_type_of(fmt_obj) != PY_TYPE_STR) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "left operand of % must be str"));
        return NULL;
    }
    PyStrObject *fmt = (PyStrObject *)fmt_obj;
    PercentFormatBuf out = {0};
    int64_t arg_index = 0;
    int tuple_mode = 0;

    for (int64_t i = 0; i < fmt->byte_len; i++) {
        char c = fmt->data[i];
        if (c != '%') {
            if (pfbuf_append_char(&out, c) != 0) goto oom;
            continue;
        }
        if (i + 1 < fmt->byte_len && fmt->data[i + 1] == '%') {
            if (pfbuf_append_char(&out, '%') != 0) goto oom;
            i++;
            continue;
        }

        int64_t start = i;
        i++;
        /* Mapping form ``%(name)conv``: the argument is dict[name]. */
        PyObject *mapping_arg = NULL;
        int has_mapping = 0;
        int64_t body_start = i;
        if (i < fmt->byte_len && fmt->data[i] == '(') {
            int64_t ks = i + 1;
            int64_t ke = ks;
            while (ke < fmt->byte_len && fmt->data[ke] != ')') ke++;
            if (ke >= fmt->byte_len) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "incomplete format key"));
                goto fail;
            }
            if (py_type_of(args) != PY_TYPE_DICT) {
                py_raise(py_exc_new(PY_EXC_TYPEERROR, "format requires a mapping"));
                goto fail;
            }
            PyObject *key = py_str_new(fmt->data + ks, ke - ks);
            if (key == NULL) goto oom;
            PyObject *val = py_dict_get(args, key);
            py_decref(key);
            if (val == NULL) {
                py_raise(py_exc_new(PY_EXC_KEYERROR, "format key not found"));
                goto fail;
            }
            py_decref(val);            /* dict retains ownership; borrow it */
            mapping_arg = val;
            has_mapping = 1;
            i = ke + 1;
            body_start = i;
        }
        while (i < fmt->byte_len && strchr("#0- +", fmt->data[i]) != NULL) i++;
        while (i < fmt->byte_len && fmt->data[i] >= '0' && fmt->data[i] <= '9') i++;
        if (i < fmt->byte_len && fmt->data[i] == '.') {
            i++;
            while (i < fmt->byte_len && fmt->data[i] >= '0' && fmt->data[i] <= '9') i++;
        }
        while (i < fmt->byte_len && strchr("hlLzjt", fmt->data[i]) != NULL) i++;
        if (i >= fmt->byte_len) {
            py_raise(py_exc_new(PY_EXC_VALUEERROR, "incomplete format"));
            goto fail;
        }
        char conv = fmt->data[i];
        int64_t spec_len = i - start + 1;
        /* Spec passed to the conversion helpers, with any ``(name)`` removed. */
        char specbuf[160];
        const char *specptr = fmt->data + start;
        int64_t spec_use_len = spec_len;
        if (has_mapping) {
            int64_t body_len = i - body_start + 1;   /* flags .. conv */
            if (body_len + 1 >= (int64_t)sizeof(specbuf)) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "format spec too long"));
                goto fail;
            }
            specbuf[0] = '%';
            memcpy(specbuf + 1, fmt->data + body_start, (size_t)body_len);
            specptr = specbuf;
            spec_use_len = body_len + 1;
        }
        PyObject *arg = has_mapping
            ? mapping_arg
            : percent_get_arg(args, &arg_index, &tuple_mode);
        if (arg == NULL) goto fail;

        if (conv == 's' || conv == 'r') {
            if (percent_append_formatted_str(&out, specptr, spec_use_len, arg, conv == 'r') != 0) {
                goto fail;
            }
            continue;
        }
        if (conv == 'd' || conv == 'i' || conv == 'u' || conv == 'x' || conv == 'X' || conv == 'o') {
            char cfmt[96];
            if (percent_copy_spec(cfmt, (int)sizeof(cfmt), specptr, spec_use_len, conv, "ll") != 0) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format specifier"));
                goto fail;
            }
            int64_t v = 0;
            int overflow = 0;
            int32_t tag = py_type_of(arg);
            if (tag == PY_TYPE_BOOL) {
                v = arg == py_True ? 1 : 0;
            } else if (tag == PY_TYPE_INT) {
                v = py_int_to_i64(arg, &overflow);
                if (overflow) v = py_int_value_i64(arg);
            } else {
                py_raise(py_exc_new(PY_EXC_TYPEERROR, "integer format requires a number"));
                goto fail;
            }
            if (conv == 'u' || conv == 'x' || conv == 'X' || conv == 'o') {
                if (percent_append_cfmt(&out, cfmt, (unsigned long long)v) != 0) goto oom;
            } else {
                if (percent_append_cfmt(&out, cfmt, (long long)v) != 0) goto oom;
            }
            continue;
        }
        if (conv == 'e' || conv == 'E' || conv == 'f' || conv == 'F' || conv == 'g' || conv == 'G') {
            char cfmt[96];
            if (percent_copy_spec(cfmt, (int)sizeof(cfmt), specptr, spec_use_len, conv, NULL) != 0) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format specifier"));
                goto fail;
            }
            double v = py_float_to_f64(arg);
            if (percent_append_cfmt(&out, cfmt, v) != 0) goto oom;
            continue;
        }
        if (conv == 'c') {
            int64_t v = 0;
            if (py_type_of(arg) == PY_TYPE_STR) {
                if (py_str_byte_len(arg) != 1) {
                    py_raise(py_exc_new(PY_EXC_TYPEERROR, "%c requires int or char"));
                    goto fail;
                }
                if (pfbuf_append_char(&out, py_str_utf8(arg)[0]) != 0) goto oom;
                continue;
            }
            if (py_type_of(arg) == PY_TYPE_BOOL) {
                v = arg == py_True ? 1 : 0;
            } else if (py_type_of(arg) == PY_TYPE_INT) {
                v = py_int_value_i64(arg);
            } else {
                py_raise(py_exc_new(PY_EXC_TYPEERROR, "%c requires int or char"));
                goto fail;
            }
            char ch = (char)v;
            if (pfbuf_append_char(&out, ch) != 0) goto oom;
            continue;
        }
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format character"));
        goto fail;
    }

    if (tuple_mode && py_type_of(args) == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)args;
        if (arg_index < t->len) {
            py_raise(py_exc_new(PY_EXC_TYPEERROR, "not all arguments converted during string formatting"));
            goto fail;
        }
    }
    PyObject *result = py_str_new(out.data, out.len);
    free(out.data);
    return result;

oom:
    py_raise(py_exc_new(PY_EXC_RUNTIMEERROR, "out of memory"));
fail:
    free(out.data);
    return NULL;
}

/* Read the raw byte payload of a bytes/bytearray/memoryview object.
 *
 * bytes / bytearray share the same {header, int64 byte_len, char data[]}
 * layout (see PyBytesObject / PyByteArrayObject in py_internal.h), so a
 * single read path covers both. A memoryview forwards to its base. Returns
 * the pointer and stores the length via ``*n``; returns NULL for anything
 * that is not bytes-like. */
static const char *bytes_mod_payload(PyObject *o, int64_t *n) {
    *n = 0;
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *b = (PyByteArrayObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *m = (PyMemoryViewObject *)o;
        PyObject *base = pcc_gc_load_ptr(o, &m->base);
        return bytes_mod_payload(base, n);
    }
    return NULL;
}

/* Parse ``-`` flag / width / ``.precision`` out of a percent spec whose
 * conversion char is at ``spec[spec_len - 1]``. Shared by the bytes
 * ``%s``/``%b``/``%r``/``%a`` renderers, which pad/truncate on byte length. */
static void bytes_mod_parse_width(
    const char *spec,
    int64_t spec_len,
    int64_t *width,
    int64_t *precision,
    int *left
) {
    *width = 0;
    *precision = -1;
    *left = 0;
    for (int64_t i = 1; i < spec_len - 1; i++) {
        char c = spec[i];
        if (c == '-') {
            *left = 1;
        } else if (c >= '0' && c <= '9') {
            *width = *width * 10 + (int64_t)(c - '0');
        } else if (c == '.') {
            *precision = 0;
            i++;
            while (i < spec_len - 1 && spec[i] >= '0' && spec[i] <= '9') {
                *precision = *precision * 10 + (int64_t)(spec[i] - '0');
                i++;
            }
            i--;
        }
    }
}

/* Append ``text[0:len]`` to ``out`` honoring width / precision / left-align.
 * ``precision`` (>= 0) truncates; ``width`` pads with spaces. */
static int bytes_mod_append_padded(
    PercentFormatBuf *out,
    const char *text,
    int64_t len,
    int64_t width,
    int64_t precision,
    int left
) {
    if (precision >= 0 && precision < len) len = precision;
    int64_t pad = width > len ? width - len : 0;
    int ok = 0;
    if (!left) {
        for (int64_t i = 0; i < pad; i++) ok |= pfbuf_append_char(out, ' ');
    }
    ok |= pfbuf_append(out, text, len);
    if (left) {
        for (int64_t i = 0; i < pad; i++) ok |= pfbuf_append_char(out, ' ');
    }
    return ok == 0 ? 0 : -1;
}

/* bytes/bytearray ``%``-formatting: ``b"%d-%s" % (5, b"x")`` -> ``b"5-x"``.
 *
 * Mirrors py_str_mod but with bytes semantics from CPython's
 * bytes.__mod__ / _PyBytes_Format:
 *   - ``%s`` / ``%b`` require a bytes-like object (bytes/bytearray/memoryview);
 *     a str raises TypeError (unlike str %s which stringifies anything).
 *   - ``%r`` / ``%a`` both emit ``ascii(arg)`` (ASCII-only bytes).
 *   - ``%d %i %u %x %X %o`` require an int/bool.
 *   - ``%c`` accepts an int 0..255 or a length-1 bytes/bytearray.
 *   - ``%(key)conv`` maps against a dict keyed by bytes.
 * The result type follows the format operand: bytes -> bytes,
 * bytearray -> bytearray. */
PyObject *py_bytes_mod(PyObject *fmt_obj, PyObject *args) {
    int32_t fmt_tag = fmt_obj == NULL ? -1 : py_type_of(fmt_obj);
    if (fmt_tag != PY_TYPE_BYTES && fmt_tag != PY_TYPE_BYTEARRAY) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR,
                            "left operand of % must be bytes or bytearray"));
        return NULL;
    }
    int64_t fmt_len = 0;
    const char *fmt = bytes_mod_payload(fmt_obj, &fmt_len);
    if (fmt == NULL) fmt_len = 0;

    PercentFormatBuf out = {0};
    int64_t arg_index = 0;
    int tuple_mode = 0;

    for (int64_t i = 0; i < fmt_len; i++) {
        char c = fmt[i];
        if (c != '%') {
            if (pfbuf_append_char(&out, c) != 0) goto oom;
            continue;
        }
        if (i + 1 < fmt_len && fmt[i + 1] == '%') {
            if (pfbuf_append_char(&out, '%') != 0) goto oom;
            i++;
            continue;
        }

        int64_t start = i;
        i++;
        /* Mapping form ``%(name)conv``: the argument is dict[name] with a
         * bytes key. */
        PyObject *mapping_arg = NULL;
        int has_mapping = 0;
        int64_t body_start = i;
        if (i < fmt_len && fmt[i] == '(') {
            int64_t ks = i + 1;
            int64_t ke = ks;
            while (ke < fmt_len && fmt[ke] != ')') ke++;
            if (ke >= fmt_len) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "incomplete format key"));
                goto fail;
            }
            if (py_type_of(args) != PY_TYPE_DICT) {
                py_raise(py_exc_new(PY_EXC_TYPEERROR, "format requires a mapping"));
                goto fail;
            }
            PyObject *key = py_bytes_new(fmt + ks, ke - ks);
            if (key == NULL) goto oom;
            PyObject *val = py_dict_get(args, key);
            py_decref(key);
            if (val == NULL) {
                py_raise(py_exc_new(PY_EXC_KEYERROR, "format key not found"));
                goto fail;
            }
            py_decref(val);            /* dict retains ownership; borrow it */
            mapping_arg = val;
            has_mapping = 1;
            i = ke + 1;
            body_start = i;
        }
        while (i < fmt_len && strchr("#0- +", fmt[i]) != NULL) i++;
        while (i < fmt_len && fmt[i] >= '0' && fmt[i] <= '9') i++;
        if (i < fmt_len && fmt[i] == '.') {
            i++;
            while (i < fmt_len && fmt[i] >= '0' && fmt[i] <= '9') i++;
        }
        while (i < fmt_len && strchr("hlLzjt", fmt[i]) != NULL) i++;
        if (i >= fmt_len) {
            py_raise(py_exc_new(PY_EXC_VALUEERROR, "incomplete format"));
            goto fail;
        }
        char conv = fmt[i];
        int64_t spec_len = i - start + 1;
        char specbuf[160];
        const char *specptr = fmt + start;
        int64_t spec_use_len = spec_len;
        if (has_mapping) {
            int64_t body_len = i - body_start + 1;   /* flags .. conv */
            if (body_len + 1 >= (int64_t)sizeof(specbuf)) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "format spec too long"));
                goto fail;
            }
            specbuf[0] = '%';
            memcpy(specbuf + 1, fmt + body_start, (size_t)body_len);
            specptr = specbuf;
            spec_use_len = body_len + 1;
        }
        PyObject *arg = has_mapping
            ? mapping_arg
            : percent_get_arg(args, &arg_index, &tuple_mode);
        if (arg == NULL) goto fail;

        if (conv == 's' || conv == 'b') {
            int64_t alen = 0;
            const char *adata = bytes_mod_payload(arg, &alen);
            if (adata == NULL) {
                /* CPython: str (and any non-bytes-like) is a TypeError for
                 * bytes %s / %b. */
                py_raise(py_exc_new(PY_EXC_TYPEERROR,
                    "%b requires a bytes-like object, or an object that "
                    "implements __bytes__"));
                goto fail;
            }
            int64_t width, precision;
            int left;
            bytes_mod_parse_width(specptr, spec_use_len, &width, &precision, &left);
            if (bytes_mod_append_padded(&out, adata, alen, width, precision, left) != 0) {
                goto fail;
            }
            continue;
        }
        if (conv == 'r' || conv == 'a') {
            /* Both emit ascii(arg) as ASCII-only bytes. */
            PyObject *s = py_obj_ascii(arg);
            if (s == NULL || py_type_of(s) != PY_TYPE_STR) {
                if (s != NULL) py_decref(s);
                if (py_err_occurred() == 0) {
                    py_raise(py_exc_new(PY_EXC_TYPEERROR,
                        "format argument cannot be converted"));
                }
                goto fail;
            }
            const char *text = py_str_utf8(s);
            int64_t tlen = py_str_byte_len(s);
            int64_t width, precision;
            int left;
            bytes_mod_parse_width(specptr, spec_use_len, &width, &precision, &left);
            int rc = bytes_mod_append_padded(&out, text, tlen, width, precision, left);
            py_decref(s);
            if (rc != 0) goto fail;
            continue;
        }
        if (conv == 'd' || conv == 'i' || conv == 'u' || conv == 'x' || conv == 'X' || conv == 'o') {
            char cfmt[96];
            if (percent_copy_spec(cfmt, (int)sizeof(cfmt), specptr, spec_use_len, conv, "ll") != 0) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format specifier"));
                goto fail;
            }
            int64_t v = 0;
            int overflow = 0;
            int32_t tag = py_type_of(arg);
            if (tag == PY_TYPE_BOOL) {
                v = arg == py_True ? 1 : 0;
            } else if (tag == PY_TYPE_INT) {
                v = py_int_to_i64(arg, &overflow);
                if (overflow) v = py_int_value_i64(arg);
            } else {
                py_raise(py_exc_new(PY_EXC_TYPEERROR,
                    "%d format: a real number is required"));
                goto fail;
            }
            if (conv == 'u' || conv == 'x' || conv == 'X' || conv == 'o') {
                if (percent_append_cfmt(&out, cfmt, (unsigned long long)v) != 0) goto oom;
            } else {
                if (percent_append_cfmt(&out, cfmt, (long long)v) != 0) goto oom;
            }
            continue;
        }
        if (conv == 'e' || conv == 'E' || conv == 'f' || conv == 'F' || conv == 'g' || conv == 'G') {
            char cfmt[96];
            if (percent_copy_spec(cfmt, (int)sizeof(cfmt), specptr, spec_use_len, conv, NULL) != 0) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format specifier"));
                goto fail;
            }
            double v = py_float_to_f64(arg);
            if (percent_append_cfmt(&out, cfmt, v) != 0) goto oom;
            continue;
        }
        if (conv == 'c') {
            int64_t alen = 0;
            const char *adata = bytes_mod_payload(arg, &alen);
            if (adata != NULL) {
                if (alen != 1) {
                    py_raise(py_exc_new(PY_EXC_TYPEERROR,
                        "%c requires an integer in range(256) or a single byte"));
                    goto fail;
                }
                if (pfbuf_append_char(&out, adata[0]) != 0) goto oom;
                continue;
            }
            int64_t v = 0;
            int32_t tag = py_type_of(arg);
            if (tag == PY_TYPE_BOOL) {
                v = arg == py_True ? 1 : 0;
            } else if (tag == PY_TYPE_INT) {
                v = py_int_value_i64(arg);
            } else {
                py_raise(py_exc_new(PY_EXC_TYPEERROR,
                    "%c requires an integer in range(256) or a single byte"));
                goto fail;
            }
            if (v < 0 || v > 255) {
                py_raise(py_exc_new(PY_EXC_VALUEERROR,
                    "%c arg not in range(256)"));
                goto fail;
            }
            char ch = (char)(v & 0xff);
            if (pfbuf_append_char(&out, ch) != 0) goto oom;
            continue;
        }
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported format character"));
        goto fail;
    }

    if (tuple_mode && py_type_of(args) == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)args;
        if (arg_index < t->len) {
            py_raise(py_exc_new(PY_EXC_TYPEERROR, "not all arguments converted during bytes formatting"));
            goto fail;
        }
    }
    PyObject *result = py_bytes_new(out.data, out.len);
    free(out.data);
    if (result != NULL && fmt_tag == PY_TYPE_BYTEARRAY) {
        /* bytearray % ... yields a bytearray (CPython). Materialize the
         * payload as bytes, then copy into a fresh bytearray. */
        PyObject *ba = py_bytearray_from_obj(result);
        py_decref(result);
        return ba;
    }
    return result;

oom:
    py_raise(py_exc_new(PY_EXC_RUNTIMEERROR, "out of memory"));
fail:
    free(out.data);
    return NULL;
}
