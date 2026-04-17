/* pcc/py_runtime/src/py_exc_tls.c
 *
 * TLS-slot management + raise / err_occurred / current / clear.
 *
 * Split out of py_exc.c so pcc-Python ports can replace this
 * single object file without losing the rest of the exception
 * surface (py_exc_alloc, py_exc_matches, py_exc_builtin_class, etc.
 * live in sibling py_exc_*.c files).
 *
 * Strategy (return-code style, CPython-inspired):
 *   - `py_raise(exc)` just stashes `exc` in a thread-local slot and
 *     returns normally. No unwinder, no Itanium ABI.
 *   - Callers check `py_err_occurred()` after every call that could
 *     raise; on true, branch to error propagation.
 *
 * Public symbols:
 *   py_raise, py_err_occurred, py_current_exception, py_clear_exception
 *   py_tls_exc_get, py_tls_exc_set  -- raw slot accessors for future
 *   pcc-Python ports that want to reimplement the four above.
 */
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>


/* The TLS slot storage + accessors (py_tls_exc_get / py_tls_exc_set)
 * live in py_substrate.c so the Python port (py_exc_tls.py) can reach
 * them via extern without needing to declare the TLS slot itself. */
extern void *py_tls_exc_get(void);
extern void  py_tls_exc_set(void *exc);


static int py_raise_instance_like(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    int32_t tag = py_type_of(o);
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}


static const char *py_raise_message_from_object(PyObject *o,
                                                PyObject **owned_attr,
                                                PyObject **owned_str) {
    *owned_attr = NULL;
    *owned_str = NULL;

    if (py_raise_instance_like(o)) {
        PyObject *msg = py_instance_getattr((PyInstanceObject *)o, "message");
        if (msg != NULL) {
            *owned_attr = msg;
            if (py_type_of(msg) == PY_TYPE_STR) {
                return py_str_utf8(msg);
            }
            PyObject *msg_str = py_obj_str(msg);
            if (msg_str != NULL) {
                *owned_str = msg_str;
                return py_str_utf8(msg_str);
            }
        }
    }

    PyObject *as_str = py_obj_str(o);
    if (as_str != NULL) {
        *owned_str = as_str;
        return py_str_utf8(as_str);
    }
    return NULL;
}


static PyObject *py_raise_normalize(PyObject *exc, int *owned) {
    *owned = 0;
    if (exc == NULL) {
        *owned = 1;
        return py_exc_new(PY_EXC_RUNTIMEERROR,
                          "no active exception to reraise");
    }
    if (py_type_of(exc) == PY_TYPE_EXC) {
        return exc;
    }

    PyClassObject *base = py_exc_builtin_class(PY_EXC_BASE);
    if (base != NULL && py_isinstance(exc, base)) {
        PyClassObject *cls = NULL;
        if (py_raise_instance_like(exc)) {
            cls = ((PyInstanceObject *)exc)->cls;
        }

        PyObject *owned_attr = NULL;
        PyObject *owned_str = NULL;
        const char *msg = py_raise_message_from_object(
            exc, &owned_attr, &owned_str);
        PyObject *normalized = py_exc_new_with_class((PyObject *)cls, msg);
        if (owned_str != NULL) py_decref(owned_str);
        if (owned_attr != NULL) py_decref(owned_attr);
        if (normalized != NULL) {
            *owned = 1;
            return normalized;
        }
    }

    *owned = 1;
    return py_exc_new(PY_EXC_TYPEERROR,
                      "exceptions must derive from BaseException");
}


void py_raise(PyObject *exc) {
    int exc_owned = 0;
    exc = py_raise_normalize(exc, &exc_owned);
    PyObject *cur = (PyObject *)py_tls_exc_get();
    /* Auto-chain context: if a prior exception is still active
     * (we're inside an except block), stash it as __context__ on
     * the new one. Matches CPython's implicit chaining. */
    if (cur != NULL && exc != NULL &&
        cur != exc &&
        py_type_of(exc) == PY_TYPE_EXC) {
        PyExceptionObject *new_exc = (PyExceptionObject *)exc;
        if (new_exc->context == NULL) {
            py_incref(cur);
            new_exc->context = cur;
        }
    }
    if (exc != NULL && !exc_owned) py_incref(exc);
    if (cur != NULL) py_decref(cur);
    py_tls_exc_set(exc);
    /* Caller is responsible for propagation via a post-call
     * py_err_occurred() check. */
}


int64_t py_err_occurred(void) {
    return py_tls_exc_get() != NULL ? 1 : 0;
}


PyObject *py_current_exception(void) {
    return (PyObject *)py_tls_exc_get();   /* borrowed */
}


void py_clear_exception(void) {
    PyObject *cur = (PyObject *)py_tls_exc_get();
    if (cur != NULL) {
        py_decref(cur);
        py_tls_exc_set(NULL);
    }
}
