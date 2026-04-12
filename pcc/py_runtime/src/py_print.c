/* pcc/py_runtime/src/py_print.c
 *
 * Implements py_print and py_print_many.
 *
 * Formatting matches CPython repr/str for the Phase 1 types:
 *   - None        -> "None"
 *   - True/False  -> "True" / "False"
 *   - int         -> decimal digits, with '-' for negatives
 *   - float       -> %g-ish; Phase 1 uses "%g" as a first pass
 *   - str         -> raw utf8 (str-style, not repr-with-quotes)
 *   - list        -> "[e0, e1, ...]"
 *   - tuple       -> "(e0, e1)" / "(e0,)" for 1-element
 *
 * py_print writes a single object followed by '\n'.
 * py_print_many matches Python's print(*args, sep=..., end=...).
 */

#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

/* ---- Forward decls ---------------------------------------------------- */
static void py_format(FILE *fp, PyObject *o);

/* ---- Scalar formatters ------------------------------------------------ */

static void py_format_int(FILE *fp, PyObject *o) {
    /* Tagged ints print via the direct int64 path. A heap-int PyIntObject
     * might still fit in int64 — in which case we format it via %PRId64 for
     * speed; only true bignums (magnitude > INT64_MAX) go through the
     * decimal-string converter. */
    if (PY_IS_TAGGED_INT(o)) {
        fprintf(fp, "%" PRId64, py_untag_int(o));
        return;
    }
    const PyIntObject *b = (const PyIntObject *)o;
    int overflow = 0;
    int64_t v = py_bigint_to_i64(b, &overflow);
    if (!overflow) {
        fprintf(fp, "%" PRId64, v);
        return;
    }
    char *s = py_bigint_to_cstr(b);
    if (s == NULL) return;
    fputs(s, fp);
    free(s);
}

static void py_format_float(FILE *fp, PyObject *o) {
    PyFloatObject *f = (PyFloatObject *)o;
    /* TODO(phase2): match CPython's float repr (shortest round-trip). */
    fprintf(fp, "%g", f->value);
}

static void py_format_str(FILE *fp, PyObject *o) {
    PyStrObject *s = (PyStrObject *)o;
    if (s->byte_len <= 0) return;
    fwrite(s->data, 1, (size_t)s->byte_len, fp);
}

static void py_format_list(FILE *fp, PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    fputc('[', fp);
    for (int64_t i = 0; i < l->length; i++) {
        if (i > 0) fputs(", ", fp);
        py_format(fp, l->items[i]);
    }
    fputc(']', fp);
}

static void py_format_tuple(FILE *fp, PyObject *o) {
    PyTupleObject *t = (PyTupleObject *)o;
    fputc('(', fp);
    for (int64_t i = 0; i < t->len; i++) {
        if (i > 0) fputs(", ", fp);
        py_format(fp, t->items[i]);
    }
    /* CPython emits a trailing comma for 1-tuples. */
    if (t->len == 1) fputc(',', fp);
    fputc(')', fp);
}

/* ---- Dispatch --------------------------------------------------------- */

static void py_format(FILE *fp, PyObject *o) {
    if (o == NULL) {
        fputs("<null>", fp);
        return;
    }

    /* Tagged int fast path. */
    if (PY_IS_TAGGED_INT(o)) {
        py_format_int(fp, o);
        return;
    }

    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_NONE:
            fputs("None", fp);
            break;
        case PY_TYPE_BOOL:
            fputs(o == py_True ? "True" : "False", fp);
            break;
        case PY_TYPE_INT:
            py_format_int(fp, o);
            break;
        case PY_TYPE_FLOAT:
            py_format_float(fp, o);
            break;
        case PY_TYPE_STR:
            py_format_str(fp, o);
            break;
        case PY_TYPE_LIST:
            py_format_list(fp, o);
            break;
        case PY_TYPE_TUPLE:
            py_format_tuple(fp, o);
            break;
        default:
            /* TODO(phase3): call __repr__ via dispatch table. */
            fprintf(fp, "<object tag=%d>", (int)tag);
            break;
    }
}

/* ---- Public API ------------------------------------------------------- */

void py_print(PyObject *o) {
    py_format(stdout, o);
    fputc('\n', stdout);
}

void py_print_many(PyObject *args_tuple, PyObject *sep, PyObject *end) {
    /* Defaults: sep=" ", end="\n". If a separator/end is passed as py_None
     * we also use the defaults (matching CPython's print behavior). */
    const char *sep_str = " ";
    size_t sep_len = 1;
    const char *end_str = "\n";
    size_t end_len = 1;

    if (sep != NULL && sep != py_None && py_type_of(sep) == PY_TYPE_STR) {
        PyStrObject *s = (PyStrObject *)sep;
        sep_str = s->data;
        sep_len = (size_t)s->byte_len;
    }
    if (end != NULL && end != py_None && py_type_of(end) == PY_TYPE_STR) {
        PyStrObject *s = (PyStrObject *)end;
        end_str = s->data;
        end_len = (size_t)s->byte_len;
    }

    if (args_tuple == NULL) {
        /* Just print the end string. */
        fwrite(end_str, 1, end_len, stdout);
        return;
    }

    PyTupleObject *t = (PyTupleObject *)args_tuple;
    for (int64_t i = 0; i < t->len; i++) {
        if (i > 0) fwrite(sep_str, 1, sep_len, stdout);
        py_format(stdout, t->items[i]);
    }
    fwrite(end_str, 1, end_len, stdout);
}
