/* pcc/py_runtime/src/py_obj_stubs.c
 *
 * Stubs for every ABI symbol not yet implemented. As phases land, entries
 * migrate out of this file into their own module:
 *
 *   Phase 2:
 *     str         -> py_str.c
 *     tuple       -> py_tuple.c
 *     dict        -> py_dict.c
 *     set         -> py_set.c
 *     eq/hash/len/truthy/getitem/setitem -> py_obj_ops.c
 *
 *   Phase 3 (pending):
 *     float       -> py_float.c
 *     call/getattr/setattr/repr/str/isinstance -> py_obj_ops.c (extended)
 *     exceptions  -> py_exc.c
 *
 * Every remaining stub is marked with the phase that should deliver the
 * real implementation. The file exists so the linker is happy while the
 * rest of the runtime compiles into libpy_runtime.a.
 */

#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* repr(exc) builder, shared C-only helper in py_format.c. */
extern PyObject *py_exc_repr(PyObject *o);
/* repr(complex) builder (== str), shared C-only helper in py_format.c. */
extern PyObject *py_complex_repr(PyObject *o);

/* ---- Float ------------------------------------------------------------ */

PyObject *py_float_from_f64(double v) {
    PyFloatObject *f = (PyFloatObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyFloatObject), PY_TYPE_FLOAT, 0);
    if (f == NULL) return NULL;
    f->value = v;
    return (PyObject *)f;
}

double py_float_to_f64(PyObject *o) {
    if (o == NULL) return 0.0;
    if (((uintptr_t)o & 1) == 1) {
        int64_t v = (int64_t)(((intptr_t)o) >> 1);
        return (double)v;
    }
    const PyObjectHeader *h = (const PyObjectHeader *)o;
    if (h->type_tag == PY_TYPE_FLOAT) {
        return ((const PyFloatObject *)o)->value;
    }
    if (h->type_tag == PY_TYPE_INT) {
        return py_bigint_to_double((const PyIntObject *)o);
    }
    if (h->type_tag == PY_TYPE_BOOL) {
        return o == py_True ? 1.0 : 0.0;
    }
    return 0.0;
}

/* float.is_integer(): True iff the value is finite and has no fractional part.
 * Avoids math.h: any |v| >= 2^53 has no fractional bits (always integral);
 * otherwise it fits exactly in int64 so the round-trip compare is exact. */
int64_t py_float_is_integer(PyObject *o) {
    double v = py_float_to_f64(o);
    if (v != v) return 0;                       /* nan */
    if (v != 0.0 && v == v * 2.0) return 0;     /* +/-inf */
    double a = v < 0.0 ? -v : v;
    if (a >= 9007199254740992.0) return 1;      /* >= 2^53: necessarily integral */
    return v == (double)(int64_t)v ? 1 : 0;
}

PyObject *py_float_add(PyObject *a, PyObject *b) {
    /* float + numeric -> float (CPython float.__add__/__radd__). The generic
     * py_obj_add path routes here when either operand is a float (e.g. a boxed
     * true-division result: ``obj.attr / n + m``). py_float_to_f64 coerces
     * int/bool/float; a non-numeric operand returns NULL so the caller surfaces
     * the error. Was an unimplemented stub (TODO phase3). */
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    int a_num = (at == PY_TYPE_INT || at == PY_TYPE_BOOL || at == PY_TYPE_FLOAT);
    int b_num = (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL || bt == PY_TYPE_FLOAT);
    if (a_num && b_num) {
        return py_float_from_f64(py_float_to_f64(a) + py_float_to_f64(b));
    }
    return NULL;
}

PyObject *py_float_sub(PyObject *a, PyObject *b) {
    /* float - numeric -> float (mirrors py_float_add). */
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    int a_num = (at == PY_TYPE_INT || at == PY_TYPE_BOOL || at == PY_TYPE_FLOAT);
    int b_num = (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL || bt == PY_TYPE_FLOAT);
    if (a_num && b_num) {
        return py_float_from_f64(py_float_to_f64(a) - py_float_to_f64(b));
    }
    return NULL;
}

PyObject *py_float_mul(PyObject *a, PyObject *b) {
    /* float * numeric -> float (mirrors py_float_add). */
    int32_t at = py_type_of(a);
    int32_t bt = py_type_of(b);
    int a_num = (at == PY_TYPE_INT || at == PY_TYPE_BOOL || at == PY_TYPE_FLOAT);
    int b_num = (bt == PY_TYPE_INT || bt == PY_TYPE_BOOL || bt == PY_TYPE_FLOAT);
    if (a_num && b_num) {
        return py_float_from_f64(py_float_to_f64(a) * py_float_to_f64(b));
    }
    return NULL;
}

PyObject *py_float_round_ndigits(double v, int64_t ndigits) {
    return py_float_from_f64(pcc_float_round_fixed_f64(v, ndigits));
}

PyObject *py_float_format_fixed(PyObject *o, int64_t precision) {
    if (precision < 0) precision = 6;
    if (precision > 32) precision = 32;
    double v = py_float_to_f64(o);
    char fmt[16];
    char buf[128];
    snprintf(fmt, sizeof(fmt), "%%.%df", (int)precision);
    int n = snprintf(buf, sizeof(buf), fmt, v);
    if (n < 0) return NULL;
    if (n >= (int)sizeof(buf)) n = (int)sizeof(buf) - 1;
    return py_str_new(buf, (int64_t)n);
}

/* ---- Complex ---------------------------------------------------------- */

PyObject *py_complex_new(double real, double imag) {
    PyComplexObject *z = (PyComplexObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyComplexObject), PY_TYPE_COMPLEX, 0);
    if (z == NULL) return NULL;
    z->real = real;
    z->imag = imag;
    return (PyObject *)z;
}

static double complex_real_part(PyObject *o) {
    if (o == NULL) return 0.0;
    if (PY_IS_TAGGED_INT(o)) return (double)py_untag_int(o);
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_COMPLEX) return ((PyComplexObject *)o)->real;
    if (tag == PY_TYPE_FLOAT) return ((PyFloatObject *)o)->value;
    if (tag == PY_TYPE_INT) return py_bigint_to_double((const PyIntObject *)o);
    if (tag == PY_TYPE_BOOL) return o == py_True ? 1.0 : 0.0;
    return 0.0;
}

static double complex_imag_part(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0.0;
    if (py_header(o)->type_tag == PY_TYPE_COMPLEX) {
        return ((PyComplexObject *)o)->imag;
    }
    return 0.0;
}

PyObject *py_complex_real(PyObject *o) {
    return py_float_from_f64(complex_real_part(o));
}

PyObject *py_complex_imag(PyObject *o) {
    return py_float_from_f64(complex_imag_part(o));
}

PyObject *py_complex_add(PyObject *a, PyObject *b) {
    return py_complex_new(
        complex_real_part(a) + complex_real_part(b),
        complex_imag_part(a) + complex_imag_part(b)
    );
}

/* ---- Str    (moved to py_str.c)     ---------------------------------- */
/* ---- Tuple  (moved to py_tuple.c)   ---------------------------------- */
/* ---- Dict   (moved to py_dict.c)    ---------------------------------- */
/* ---- Set    (moved to py_set.c)     ---------------------------------- */
/* ---- eq/hash/truthy/len/getitem/setitem (moved to py_obj_ops.c) ------ */

/* ---- Generic object ops still stubbed -------------------------------- */
/* py_obj_call / py_obj_getattr / py_obj_setattr / py_obj_isinstance moved
 * to py_obj_ops.c as of Phase 3 (class + MRO support). */

static char pcc_hex_digit(int v) {
    return (char)(v < 10 ? ('0' + v) : ('a' + (v - 10)));
}

static int64_t pcc_append_hex_escape(
    char *buf,
    int64_t n,
    char prefix,
    uint32_t value,
    int digits
) {
    buf[n++] = '\\';
    buf[n++] = prefix;
    for (int shift = (digits - 1) * 4; shift >= 0; shift -= 4) {
        buf[n++] = pcc_hex_digit((int)((value >> shift) & 0xf));
    }
    return n;
}

static uint32_t pcc_decode_utf8_codepoint(
    const char *data,
    int64_t byte_len,
    int64_t *index
) {
    int64_t i = *index;
    unsigned char c = (unsigned char)data[i];
    if (c < 0x80) {
        *index = i + 1;
        return (uint32_t)c;
    }
    if (
        (c & 0xe0) == 0xc0
        && i + 1 < byte_len
        && (((unsigned char)data[i + 1]) & 0xc0) == 0x80
    ) {
        *index = i + 2;
        return ((uint32_t)(c & 0x1f) << 6)
            | (uint32_t)(((unsigned char)data[i + 1]) & 0x3f);
    }
    if (
        (c & 0xf0) == 0xe0
        && i + 2 < byte_len
        && (((unsigned char)data[i + 1]) & 0xc0) == 0x80
        && (((unsigned char)data[i + 2]) & 0xc0) == 0x80
    ) {
        *index = i + 3;
        return ((uint32_t)(c & 0x0f) << 12)
            | ((uint32_t)(((unsigned char)data[i + 1]) & 0x3f) << 6)
            | (uint32_t)(((unsigned char)data[i + 2]) & 0x3f);
    }
    if (
        (c & 0xf8) == 0xf0
        && i + 3 < byte_len
        && (((unsigned char)data[i + 1]) & 0xc0) == 0x80
        && (((unsigned char)data[i + 2]) & 0xc0) == 0x80
        && (((unsigned char)data[i + 3]) & 0xc0) == 0x80
    ) {
        *index = i + 4;
        return ((uint32_t)(c & 0x07) << 18)
            | ((uint32_t)(((unsigned char)data[i + 1]) & 0x3f) << 12)
            | ((uint32_t)(((unsigned char)data[i + 2]) & 0x3f) << 6)
            | (uint32_t)(((unsigned char)data[i + 3]) & 0x3f);
    }
    *index = i + 1;
    return (uint32_t)c;
}

static PyObject *pcc_obj_repr_str(PyObject *o, int escape_non_ascii) {
    PyStrObject *s = (PyStrObject *)o;
    int64_t cap = 2;
    if (escape_non_ascii) {
        cap += s->byte_len * 10;
    } else {
        for (int64_t i = 0; i < s->byte_len; i++) {
            unsigned char c = (unsigned char)s->data[i];
            if (c == '\\' || c == '\'' || c == '\n' || c == '\r' || c == '\t') {
                cap += 2;
            } else {
                cap += 1;
            }
        }
    }
    char *buf = (char *)malloc((size_t)cap + 1);
    if (buf == NULL) return NULL;
    int64_t n = 0;
    buf[n++] = '\'';
    int64_t i = 0;
    while (i < s->byte_len) {
        unsigned char c = (unsigned char)s->data[i];
        if (c == '\\') {
            buf[n++] = '\\';
            buf[n++] = '\\';
            i++;
        } else if (c == '\'') {
            buf[n++] = '\\';
            buf[n++] = '\'';
            i++;
        } else if (c == '\n') {
            buf[n++] = '\\';
            buf[n++] = 'n';
            i++;
        } else if (c == '\r') {
            buf[n++] = '\\';
            buf[n++] = 'r';
            i++;
        } else if (c == '\t') {
            buf[n++] = '\\';
            buf[n++] = 't';
            i++;
        } else if (escape_non_ascii && (c < 0x20 || c == 0x7f)) {
            n = pcc_append_hex_escape(buf, n, 'x', (uint32_t)c, 2);
            i++;
        } else if (escape_non_ascii && c >= 0x80) {
            uint32_t cp = pcc_decode_utf8_codepoint(s->data, s->byte_len, &i);
            if (cp <= 0xff) {
                n = pcc_append_hex_escape(buf, n, 'x', cp, 2);
            } else if (cp <= 0xffff) {
                n = pcc_append_hex_escape(buf, n, 'u', cp, 4);
            } else {
                n = pcc_append_hex_escape(buf, n, 'U', cp, 8);
            }
        } else {
            buf[n++] = (char)c;
            i++;
        }
    }
    buf[n++] = '\'';
    buf[n] = '\0';
    PyObject *out = py_str_new(buf, n);
    free(buf);
    return out;
}

PyObject *py_obj_repr(PyObject *o) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_STR) {
        return pcc_obj_repr_str(o, 0);
    }
    if (tag == PY_TYPE_INT || tag == PY_TYPE_BOOL || tag == PY_TYPE_NONE) {
        return py_obj_str(o);
    }
    if (tag == PY_TYPE_FLOAT || tag == PY_TYPE_LIST ||
        tag == PY_TYPE_TUPLE || tag == PY_TYPE_DICT ||
        tag == PY_TYPE_SET || tag == PY_TYPE_BYTES) {
        return py_format_obj_to_str(o, 1);
    }
    if (tag == PY_TYPE_EXC) {
        /* repr(exc) == ClassName(repr(arg)); shared C helper (py_format.c). */
        return py_exc_repr(o);
    }
    if (tag == PY_TYPE_COMPLEX) {
        return py_complex_repr(o);
    }
    PyObject *dunder = py_user_repr_dispatch(o);
    if (dunder != NULL) return dunder;
    return NULL;
}

PyObject *py_obj_ascii(PyObject *o) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_STR) {
        return pcc_obj_repr_str(o, 1);
    }
    return py_obj_repr(o);
}

PyObject *py_obj_str(PyObject *o) {
    if (o == NULL) return NULL;
    o = pcc_gc_note_relocation_read(o);
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_STR) {
        py_incref(o);
        return o;
    }
    if (tag == PY_TYPE_INT) {
        return py_int_to_str_obj(o);
    }
    if (tag == PY_TYPE_BOOL) {
        if (o == py_True) {
            return py_str_new("True", 4);
        } else {
            return py_str_new("False", 5);
        }
    }
    if (tag == PY_TYPE_EXC) {
        PyObject *msg = py_exc_get_message(o);
        if (msg != NULL) {
            /* KeyError.__str__ is repr(key), not the bare key (CPython):
             * str(KeyError('x')) == "'x'". */
            if (py_exc_matches(o, (PyObject *)py_exc_builtin_class(PY_EXC_KEYERROR))) {
                return py_obj_repr(msg);
            }
            py_incref(msg);
            return msg;
        }
        /* Empty string fallback — currently represented as NULL so the
         * caller (py_print) prints ``<null>``; matching CPython's empty
         * ``str(ValueError())`` => "" needs a shared empty PyStrObject
         * singleton (future work). */
        return NULL;
    }
    if (tag == PY_TYPE_FLOAT || tag == PY_TYPE_NONE ||
        tag == PY_TYPE_LIST || tag == PY_TYPE_TUPLE ||
        tag == PY_TYPE_DICT || tag == PY_TYPE_SET ||
        tag == PY_TYPE_BYTES) {
        return py_format_obj_to_str(o, 0);
    }
    PyObject *dunder = py_user_str_dispatch(o);
    if (dunder != NULL) return dunder;
    if (py_err_occurred()) return NULL;
    /* A user exception subclass instance with no __str__ uses BaseException's
     * __str__: the message derived from ``args`` (args[0] if one, "" if none,
     * the args tuple repr otherwise). super().__init__(*args) stores ``args``
     * on the instance (see _emit_store_exception_args). */
    PyClassObject *exc_base = py_exc_builtin_class(PY_EXC_BASE);
    if (exc_base != NULL && py_isinstance(o, exc_base)) {
        PyObject *args = py_instance_getattr((PyInstanceObject *)o, "args");
        if (args == NULL) {
            if (py_err_occurred()) py_clear_exception();
            return py_str_new("", 0);
        }
        if (py_type_of(args) == PY_TYPE_TUPLE) {
            int64_t n = py_tuple_len(args);
            if (n == 0) return py_str_new("", 0);
            if (n == 1) return py_obj_str(py_tuple_get(args, 0));
            return py_obj_repr(args);
        }
        return py_obj_str(args);
    }
    /* No user __str__ (NULL, no pending error): object.__str__ falls back to
     * __repr__. */
    return py_obj_repr(o);
}

/* ---- Exceptions (Phase 3) -------------------------------------------- */
/* py_raise / py_current_exception / py_clear_exception / py_exc_new and
 * the exception type/table live in py_exc.c. */
