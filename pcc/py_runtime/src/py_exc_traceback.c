/* pcc/py_runtime/src/py_exc_traceback.c
 *
 * Traceback frame growth and unhandled-exception pretty printing.
 * Split from py_exc_match.c so porting py_exc_matches to pcc-Python
 * doesn't force porting the stderr-formatting code at the same
 * time.
 *
 * Contains:
 *   py_exc_append_frame     (public)
 *   py_exc_print_unhandled  (public)
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>


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


void py_exc_print_unhandled(PyObject *exc) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) {
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
