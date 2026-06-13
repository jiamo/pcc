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
#include <unistd.h>

extern int py_format_try_cpy_object_into_fd(int fd, void *obj, int32_t tag);
extern PyObject *py_exc_get_message(PyObject *exc);
extern PyObject *py_obj_str(PyObject *o);
extern PyObject *py_obj_repr(PyObject *o);
extern int64_t py_exc_matches(PyObject *exc, PyObject *type);

static void py_format(FILE *fp, PyObject *o);
static void py_format_repr(FILE *fp, PyObject *o);
static void py_format_bytes(FILE *fp, PyObject *o);

static PyObject *fmt_list_item(PyObject *owner, PyListObject *l, int64_t i) {
    return pcc_gc_load_ptr(owner, &l->items[i]);
}

static PyObject *fmt_tuple_item(PyObject *owner, PyTupleObject *t, int64_t i) {
    return pcc_gc_load_ptr(owner, &t->items[i]);
}

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
    /* CPython shortest-round-trip repr via the shared helper (mirrors the
     * pcc-Python ports). The old path used "%g" (6 significant figures), which
     * truncated e.g. 10/3 to "3.33333". */
    PyObject *s = py_float_repr_shortest(o);
    if (s == NULL) {
        return;
    }
    fwrite(py_str_utf8(s), 1, (size_t)py_str_byte_len(s), fp);
    py_decref(s);
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

static void py_format_bytes(FILE *fp, PyObject *o) {
    PyBytesObject *b = (PyBytesObject *)o;
    fputs("b'", fp);
    for (int64_t i = 0; i < b->byte_len; i++) {
        unsigned char c = (unsigned char)b->data[i];
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
                /* bytes repr shows only printable ASCII (32..126) raw;
                 * control bytes (<32), DEL (127) and all high bytes (>=128)
                 * escape as \xNN. The old ``c == 127`` missed 128..255, so
                 * b'\xcf\x80' printed the raw UTF-8 instead of the escapes. */
                if (c < 32 || c >= 127) {
                    fprintf(fp, "\\x%02x", (unsigned)c);
                } else {
                    fputc((int)c, fp);
                }
                break;
        }
    }
    fputc('\'', fp);
}

/* bytearray repr: ``bytearray(b'...')`` — the byte escaping is identical to
 * bytes (same layout: byte_len + data), wrapped in ``bytearray( ... )``. */
static void py_format_bytearray(FILE *fp, PyObject *o) {
    fputs("bytearray(", fp);
    py_format_bytes(fp, o);
    fputc(')', fp);
}

static void py_format_list(FILE *fp, PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    fputc('[', fp);
    for (int64_t i = 0; i < l->length; i++) {
        if (i > 0) fputs(", ", fp);
        py_format_repr(fp, fmt_list_item(o, l, i));
    }
    fputc(']', fp);
}

static void py_format_tuple(FILE *fp, PyObject *o) {
    PyTupleObject *t = (PyTupleObject *)o;
    fputc('(', fp);
    for (int64_t i = 0; i < t->len; i++) {
        if (i > 0) fputs(", ", fp);
        py_format_repr(fp, fmt_tuple_item(o, t, i));
    }
    if (t->len == 1) fputc(',', fp);
    fputc(')', fp);
}

static void py_format_dict(FILE *fp, PyObject *o) {
    PyDictObject *d = (PyDictObject *)o;
    fputc('{', fp);
    int64_t emitted = 0;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        if (e->key == NULL) continue;  /* dead slot from a prior delete */
        if (emitted > 0) fputs(", ", fp);
        py_format_repr(fp, pcc_gc_load_ptr(o, &e->key));
        fputs(": ", fp);
        py_format_repr(fp, pcc_gc_load_ptr(o, &e->value));
        emitted++;
    }
    fputc('}', fp);
}

static void py_format_set(FILE *fp, PyObject *o) {
    PySetObject *s = (PySetObject *)o;
    if (s->size == 0) {
        fputs("set()", fp);   /* empty set is set(), not {} */
        return;
    }
    fputc('{', fp);
    int64_t emitted = 0;
    for (int64_t i = 0; i < s->capacity; i++) {
        PyObject *key = s->entries[i].key;
        if (key == NULL || key == py_set_dummy) continue;  /* empty/tombstone */
        if (emitted > 0) fputs(", ", fp);
        py_format_repr(fp, pcc_gc_load_ptr(o, &s->entries[i].key));
        emitted++;
    }
    fputc('}', fp);
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
    if (tag == PY_TYPE_BYTES) {
        py_format_bytes(fp, o);
        return;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        py_format_bytearray(fp, o);
        return;
    }
    if (tag == PY_TYPE_INSTANCE || tag == PY_TYPE_EXC || tag >= PY_TYPE_USER) {
        /* repr() of a user instance must dispatch __repr__, not __str__.
         * Container elements (list/tuple/dict/set) recurse through here, so a
         * class with both __str__ and __repr__ would otherwise show __str__
         * inside a list. Falling through to py_format would call py_obj_str.
         * PY_TYPE_EXC: repr([KeyError('m')]) == [KeyError('m')], not the str.
         * On NULL (no __repr__) fall through to py_format's default handling. */
        PyObject *s = py_obj_repr(o);
        if (s != NULL) {
            py_format_str(fp, s);
            py_decref(s);
            return;
        }
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
        case PY_TYPE_BYTES:
            py_format_bytes(fp, o);
            break;
        case PY_TYPE_BYTEARRAY:
            py_format_bytearray(fp, o);
            break;
        case PY_TYPE_LIST:
            py_format_list(fp, o);
            break;
        case PY_TYPE_TUPLE:
            py_format_tuple(fp, o);
            break;
        case PY_TYPE_DICT:
            py_format_dict(fp, o);
            break;
        case PY_TYPE_SET:
            py_format_set(fp, o);
            break;
        case PY_TYPE_COROUTINE:
            fputs("<coroutine object>", fp);
            break;
        case PY_TYPE_CONTINUATION:
            fputs("<continuation object>", fp);
            break;
        case PY_TYPE_VIRTUAL_THREAD:
            fputs("<virtual thread object>", fp);
            break;
        case PY_TYPE_EXC: {
            /* str(exc) == str of its single message value; py_exc_get_message
             * returns a borrowed ref (no decref). An arg-less exception (NULL
             * message) renders as the empty string. KeyError is special: its
             * __str__ is repr(key) (CPython str(KeyError('x'))=="'x'"). */
            PyObject *msg = py_exc_get_message(o);
            if (msg != NULL) {
                if (py_exc_matches(
                        o, (PyObject *)py_exc_builtin_class(PY_EXC_KEYERROR))) {
                    PyObject *r = py_obj_repr(msg);
                    if (r != NULL) {
                        py_format(fp, r);
                        py_decref(r);
                    }
                } else {
                    py_format(fp, msg);
                }
            }
            break;
        }
        default: {
            /* User-class instances and other objects: str(x) via py_obj_str
             * dispatches __str__ (then __repr__) — what print() uses in
             * CPython. Without it print(<instance>) rendered "<object tag=N>"
             * even when the class defines __str__. Safe from recursion: the
             * tags py_obj_str routes back through py_format_obj_to_str
             * (float/none/list/tuple/dict/set/bytes) are all handled above
             * this default, so the default only reaches the py_user_str_dispatch
             * path. NULL (no __str__/__repr__) falls back to the hook then
             * "<object tag=N>". */
            PyObject *s = py_obj_str(o);
            if (s != NULL) {
                py_format_str(fp, s);
                py_decref(s);
            } else {
                int fd = fileno(fp);
                if (fd < 0 || !py_format_try_cpy_object_into_fd(fd, o, tag)) {
                    fprintf(fp, "<object tag=%d>", (int)tag);
                }
            }
            break;
        }
    }
}

void py_print(PyObject *o) {
    py_format(stdout, o);
    fputc('\n', stdout);
}

/* Render any object to a freshly owned PyStr using the same formatting the
 * print path uses.  ``use_repr`` selects the repr form (quoted strings); for
 * containers/float str and repr coincide.  Used by ``py_obj_str`` /
 * ``py_obj_repr`` for the non-scalar types those paths do not handle inline.
 * Returns NULL on allocation failure (caller degrades gracefully). */
PyObject *py_format_obj_to_str(PyObject *o, int use_repr) {
    char *buf = NULL;
    size_t len = 0;
    FILE *ms = open_memstream(&buf, &len);
    if (ms == NULL) {
        return NULL;
    }
    if (use_repr) {
        py_format_repr(ms, o);
    } else {
        py_format(ms, o);
    }
    fclose(ms);
    PyObject *out = py_str_new(buf, (int64_t)len);
    free(buf);
    return out;
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
        py_format(stdout, fmt_tuple_item(args_tuple, t, i));
    }
    fwrite(end_str, 1, end_len, stdout);
}
