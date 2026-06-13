/* pcc/py_runtime/src/py_exc_traceback.c
 *
 * Traceback frame growth and unhandled-exception pretty printing.
 * Split from py_exc_match.c so porting py_exc_matches to pcc-Python
 * doesn't force porting the stderr-formatting code at the same
 * time.
 *
 * Contains:
 *   py_exc_append_frame            (public)
 *   py_exc_print_unhandled         (public)
 *   py_exc_traceback_format_exc    (public, traceback.format_exc)
 *   py_exc_traceback_print_exc     (public, traceback.print_exc)
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern void *py_tls_exc_get(void);
extern void  py_tls_exc_set(void *exc);


void py_exc_append_frame(PyObject *exc,
                         const char *func_name,
                         const char *filename,
                         int32_t line) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    if (e->n_frames == e->cap_frames) {
        int32_t new_cap = e->cap_frames ? e->cap_frames * 2 : 8;
        PyFrameRecord *newbuf = (PyFrameRecord *)realloc(
            e->traceback, (size_t)new_cap * sizeof(PyFrameRecord));
        if (newbuf == NULL) return;  /* silently drop — out of memory */
        e->traceback  = newbuf;
        e->cap_frames = new_cap;
    }
    PyFrameRecord *fr = &e->traceback[e->n_frames++];
    fr->func_name = func_name;
    fr->filename  = filename;
    fr->line      = line;
    fr->_pad      = 0;
}


static void print_exc_heading(PyExceptionObject *e) {
    PyObject *exc_class_obj = pcc_gc_load_ptr((PyObject *)e, (PyObject **)&e->exc_class);
    PyClassObject *exc_class = (PyClassObject *)exc_class_obj;
    PyObject *message = pcc_gc_load_ptr((PyObject *)e, &e->message);
    const char *cls_name = (exc_class && exc_class->name)
        ? exc_class->name : "Exception";
    if (message != NULL && message != py_None &&
        py_type_of(message) == PY_TYPE_STR) {
        const char *msg = py_str_utf8(message);
        fprintf(stderr, "%s: %s\n", cls_name, msg ? msg : "");
    } else {
        fprintf(stderr, "%s\n", cls_name);
    }
}


static int is_user_exception_instance(PyObject *exc) {
    if (exc == NULL || PY_IS_TAGGED_INT(exc)) return 0;
    exc = pcc_gc_note_relocation_read(exc);
    int32_t tag = py_type_of(exc);
    if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) return 0;
    PyClassObject *base = py_exc_builtin_class(PY_EXC_BASE);
    if (base == NULL) return 0;
    return py_isinstance(exc, base) != 0;
}


static void print_user_exc_heading(PyObject *exc) {
    exc = pcc_gc_note_relocation_read(exc);
    PyInstanceObject *inst = (PyInstanceObject *)exc;
    PyObject *cls_obj = pcc_gc_load_ptr(exc, (PyObject **)&inst->cls);
    PyClassObject *cls = (PyClassObject *)cls_obj;
    const char *cls_name = (cls && cls->name) ? cls->name : "Exception";

    PyObject *saved_exc = py_current_exception();
    if (saved_exc != NULL) {
        py_incref(saved_exc);
        py_tls_exc_set(NULL);
    }
    PyObject *msg_obj = py_obj_str(exc);
    if (py_tls_exc_get() != NULL) {
        py_clear_exception();
    }
    if (saved_exc != NULL) {
        py_tls_exc_set(saved_exc);
        py_decref(saved_exc);
    }
    if (msg_obj != NULL && py_type_of(msg_obj) == PY_TYPE_STR) {
        const char *msg = py_str_utf8(msg_obj);
        if (msg != NULL && msg[0] != '\0') {
            fprintf(stderr, "%s: %s\n", cls_name, msg);
            py_decref(msg_obj);
            return;
        }
    }
    if (msg_obj != NULL) py_decref(msg_obj);
    fprintf(stderr, "%s\n", cls_name);
}


void py_exc_print_unhandled(PyObject *exc) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) {
        if (is_user_exception_instance(exc)) {
            print_user_exc_heading(exc);
            return;
        }
        if (exc == NULL) {
            fprintf(stderr, "Unhandled non-exception object (null)\n");
        } else if (PY_IS_TAGGED_INT(exc)) {
            fprintf(stderr, "Unhandled non-exception object (tagged int)\n");
        } else {
            int32_t tag = py_type_of(exc);
            fprintf(stderr, "Unhandled non-exception object (tag=%d)", tag);
            if (tag == PY_TYPE_STR) {
                fprintf(stderr, ": %s", py_str_utf8(exc));
            }
            fprintf(stderr, "\n");
        }
        return;
    }
    PyExceptionObject *e = (PyExceptionObject *)exc;

    /* Emit chained causes oldest-first, CPython-style. */
    PyObject *cause = pcc_gc_load_ptr(exc, &e->cause);
    PyObject *context = pcc_gc_load_ptr(exc, &e->context);
    if (cause != NULL && py_type_of(cause) == PY_TYPE_EXC) {
        py_exc_print_unhandled(cause);
        fprintf(stderr,
                "\nThe above exception was the direct cause of the "
                "following exception:\n\n");
    } else if (context != NULL && py_type_of(context) == PY_TYPE_EXC) {
        py_exc_print_unhandled(context);
        fprintf(stderr,
                "\nDuring handling of the above exception, another "
                "exception occurred:\n\n");
    }

    fprintf(stderr, "Traceback (most recent call last):\n");
    for (int32_t i = 0; i < e->n_frames; i++) {
        PyFrameRecord *fr = &e->traceback[i];
        fprintf(stderr, "  File \"%s\", line %d, in %s\n",
                fr->filename ? fr->filename : "<unknown>",
                fr->line,
                fr->func_name ? fr->func_name : "<module>");
    }
    print_exc_heading(e);
}


/* ---- traceback.format_exc() / traceback.print_exc() ----------------
 *
 * Build the CPython-style traceback text for a handled exception into
 * a heap string. The frontend passes the exception being handled (the
 * retained handler exception); NULL means "no exception is being
 * handled", which formats as "NoneType: None\n" like CPython.
 *
 * Frames are emitted in reverse trail order: pcc appends the raise
 * site first and outer call sites as the exception propagates, while
 * CPython prints the outermost frame first ("most recent call last").
 */

typedef struct {
    char   *buf;
    size_t  len;
    size_t  cap;
} PccTbBuf;

static int pcc_tb_reserve(PccTbBuf *b, size_t extra) {
    if (b->len + extra + 1 <= b->cap) return 1;
    size_t new_cap = b->cap ? b->cap : 256;
    while (new_cap < b->len + extra + 1) new_cap *= 2;
    char *nb = (char *)realloc(b->buf, new_cap);
    if (nb == NULL) return 0;  /* silently stop growing — OOM */
    b->buf = nb;
    b->cap = new_cap;
    return 1;
}

static void pcc_tb_append_n(PccTbBuf *b, const char *s, size_t n) {
    if (s == NULL || n == 0) return;
    if (!pcc_tb_reserve(b, n)) return;
    memcpy(b->buf + b->len, s, n);
    b->len += n;
    b->buf[b->len] = '\0';
}

static void pcc_tb_append(PccTbBuf *b, const char *s) {
    if (s == NULL) return;
    pcc_tb_append_n(b, s, strlen(s));
}

static void pcc_tb_append_i64(PccTbBuf *b, int64_t v) {
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    pcc_tb_append(b, tmp);
}

static void pcc_tb_append_exc_heading(PccTbBuf *b, PyExceptionObject *e) {
    PyObject *exc_class_obj = pcc_gc_load_ptr((PyObject *)e, (PyObject **)&e->exc_class);
    PyClassObject *exc_class = (PyClassObject *)exc_class_obj;
    PyObject *message = pcc_gc_load_ptr((PyObject *)e, &e->message);
    const char *cls_name = (exc_class && exc_class->name)
        ? exc_class->name : "Exception";
    if (message != NULL && message != py_None &&
        py_type_of(message) == PY_TYPE_STR) {
        const char *msg = py_str_utf8(message);
        pcc_tb_append(b, cls_name);
        pcc_tb_append(b, ": ");
        pcc_tb_append(b, msg ? msg : "");
        pcc_tb_append(b, "\n");
        return;
    }
    pcc_tb_append(b, cls_name);
    pcc_tb_append(b, "\n");
}

static void pcc_tb_append_user_exc_heading(PccTbBuf *b, PyObject *exc) {
    exc = pcc_gc_note_relocation_read(exc);
    PyInstanceObject *inst = (PyInstanceObject *)exc;
    PyObject *cls_obj = pcc_gc_load_ptr(exc, (PyObject **)&inst->cls);
    PyClassObject *cls = (PyClassObject *)cls_obj;
    const char *cls_name = (cls && cls->name) ? cls->name : "Exception";

    /* py_obj_str may raise; shield the ambient TLS exception exactly
     * like print_user_exc_heading does. */
    PyObject *saved_exc = py_current_exception();
    if (saved_exc != NULL) {
        py_incref(saved_exc);
        py_tls_exc_set(NULL);
    }
    PyObject *msg_obj = py_obj_str(exc);
    if (py_tls_exc_get() != NULL) {
        py_clear_exception();
    }
    if (saved_exc != NULL) {
        py_tls_exc_set(saved_exc);
        py_decref(saved_exc);
    }
    if (msg_obj != NULL && py_type_of(msg_obj) == PY_TYPE_STR) {
        const char *msg = py_str_utf8(msg_obj);
        if (msg != NULL && msg[0] != '\0') {
            pcc_tb_append(b, cls_name);
            pcc_tb_append(b, ": ");
            pcc_tb_append(b, msg);
            pcc_tb_append(b, "\n");
            py_decref(msg_obj);
            return;
        }
    }
    if (msg_obj != NULL) py_decref(msg_obj);
    pcc_tb_append(b, cls_name);
    pcc_tb_append(b, "\n");
}

static void pcc_tb_format_into(PccTbBuf *b, PyObject *exc, int32_t depth) {
    if (exc == NULL || PY_IS_TAGGED_INT(exc)) {
        pcc_tb_append(b, "NoneType: None\n");
        return;
    }
    exc = pcc_gc_note_relocation_read(exc);
    if (py_type_of(exc) != PY_TYPE_EXC) {
        if (is_user_exception_instance(exc)) {
            /* User exception subclass instances raised as-is carry no
             * PyFrameRecord trail; emit the heading under the CPython
             * banner so callers still see the exception identity. */
            pcc_tb_append(b, "Traceback (most recent call last):\n");
            pcc_tb_append_user_exc_heading(b, exc);
            return;
        }
        pcc_tb_append(b, "NoneType: None\n");
        return;
    }
    PyExceptionObject *e = (PyExceptionObject *)exc;

    /* Chained causes oldest-first, CPython-style. Depth-capped so a
     * pathological __context__ cycle cannot recurse forever. */
    if (depth < 8) {
        PyObject *cause = pcc_gc_load_ptr(exc, &e->cause);
        PyObject *context = pcc_gc_load_ptr(exc, &e->context);
        if (cause != NULL && py_type_of(cause) == PY_TYPE_EXC) {
            pcc_tb_format_into(b, cause, depth + 1);
            pcc_tb_append(b,
                          "\nThe above exception was the direct cause of the "
                          "following exception:\n\n");
        } else if (context != NULL && py_type_of(context) == PY_TYPE_EXC) {
            pcc_tb_format_into(b, context, depth + 1);
            pcc_tb_append(b,
                          "\nDuring handling of the above exception, another "
                          "exception occurred:\n\n");
        }
    }

    pcc_tb_append(b, "Traceback (most recent call last):\n");
    for (int32_t i = e->n_frames - 1; i >= 0; i--) {
        PyFrameRecord *fr = &e->traceback[i];
        pcc_tb_append(b, "  File \"");
        pcc_tb_append(b, fr->filename ? fr->filename : "<unknown>");
        pcc_tb_append(b, "\", line ");
        pcc_tb_append_i64(b, (int64_t)fr->line);
        pcc_tb_append(b, ", in ");
        pcc_tb_append(b, fr->func_name ? fr->func_name : "<module>");
        pcc_tb_append(b, "\n");
    }
    pcc_tb_append_exc_heading(b, e);
}

PyObject *py_exc_traceback_format_exc(PyObject *exc) {
    PccTbBuf b = {NULL, 0, 0};
    pcc_tb_format_into(&b, exc, 0);
    PyObject *result = py_str_new(b.buf ? b.buf : "", (int64_t)b.len);
    free(b.buf);
    return result;
}

void py_exc_traceback_print_exc(PyObject *exc) {
    PccTbBuf b = {NULL, 0, 0};
    pcc_tb_format_into(&b, exc, 0);
    if (b.buf != NULL && b.len > 0) {
        fwrite(b.buf, 1, b.len, stderr);
    }
    free(b.buf);
}
