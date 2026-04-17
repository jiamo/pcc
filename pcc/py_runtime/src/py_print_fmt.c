/* pcc/py_runtime/src/py_print_fmt.c
 *
 * Formatter + high-level print entry points split out of py_print.c.
 * py_format / py_print / py_print_many stay here (heavy on stdio
 * format strings that pcc-Python cannot yet express). The pure
 * sys-stdout.write / sys-stderr.write path lives in py_print_sys.c
 * so it can be independently replaced by the pcc-Python port.
 */

#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

static void py_format(FILE *fp, PyObject *o);
static void py_format_repr(FILE *fp, PyObject *o);

static void py_format_int(FILE *fp, PyObject *o) {
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
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", f->value);
    fputs(buf, fp);
    int needs_dot = 1;
    for (const char *p = buf; *p != '\0'; p++) {
        if (*p == '.' || *p == 'e' || *p == 'E') {
            needs_dot = 0;
            break;
        }
    }
    if (
        needs_dot
        && strcmp(buf, "nan") != 0
        && strcmp(buf, "inf") != 0
        && strcmp(buf, "-inf") != 0
    ) {
        fputs(".0", fp);
    }
}

static void py_format_str(FILE *fp, PyObject *o) {
    PyStrObject *s = (PyStrObject *)o;
    if (s->byte_len <= 0) return;
    fwrite(s->data, 1, (size_t)s->byte_len, fp);
}

static void py_format_str_repr(FILE *fp, PyObject *o) {
    PyStrObject *s = (PyStrObject *)o;
    fputc('\'', fp);
    for (int64_t i = 0; i < s->byte_len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        switch (c) {
            case '\\':
                fputs("\\\\", fp);
                break;
            case '\'':
                fputs("\\'", fp);
                break;
            case '\n':
                fputs("\\n", fp);
                break;
            case '\r':
                fputs("\\r", fp);
                break;
            case '\t':
                fputs("\\t", fp);
                break;
            default:
                if (c < 32 || c == 127) {
                    fprintf(fp, "\\x%02x", (unsigned)c);
                } else {
                    fputc((int)c, fp);
                }
                break;
        }
    }
    fputc('\'', fp);
}

static void py_format_list(FILE *fp, PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    fputc('[', fp);
    for (int64_t i = 0; i < l->length; i++) {
        if (i > 0) fputs(", ", fp);
        py_format_repr(fp, l->items[i]);
    }
    fputc(']', fp);
}

static void py_format_tuple(FILE *fp, PyObject *o) {
    PyTupleObject *t = (PyTupleObject *)o;
    fputc('(', fp);
    for (int64_t i = 0; i < t->len; i++) {
        if (i > 0) fputs(", ", fp);
        py_format_repr(fp, t->items[i]);
    }
    if (t->len == 1) fputc(',', fp);
    fputc(')', fp);
}

static void py_format_repr(FILE *fp, PyObject *o) {
    if (o == NULL) {
        fputs("<null>", fp);
        return;
    }
    if (PY_IS_TAGGED_INT(o)) {
        py_format_int(fp, o);
        return;
    }
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_STR) {
        py_format_str_repr(fp, o);
        return;
    }
    py_format(fp, o);
}

static void py_format(FILE *fp, PyObject *o) {
    if (o == NULL) {
        fputs("<null>", fp);
        return;
    }
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
            fprintf(fp, "<object tag=%d>", (int)tag);
            break;
    }
}

void py_print(PyObject *o) {
    py_format(stdout, o);
    fputc('\n', stdout);
}

void py_print_many(PyObject *args_tuple, PyObject *sep, PyObject *end) {
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
