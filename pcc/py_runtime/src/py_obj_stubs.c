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

/* ---- Float ------------------------------------------------------------ */

PyObject *py_float_from_f64(double v) {
    PyFloatObject *f = (PyFloatObject *)malloc(sizeof(PyFloatObject));
    if (f == NULL) return NULL;
    f->h.refcount = 1;
    f->h.type_tag = PY_TYPE_FLOAT;
    f->h.flags = 0;
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

PyObject *py_float_add(PyObject *a, PyObject *b) {
    (void)a; (void)b;
    /* TODO(phase3) */
    return NULL;
}

/* ---- Str    (moved to py_str.c)     ---------------------------------- */
/* ---- Tuple  (moved to py_tuple.c)   ---------------------------------- */
/* ---- Dict   (moved to py_dict.c)    ---------------------------------- */
/* ---- Set    (moved to py_set.c)     ---------------------------------- */
/* ---- eq/hash/truthy/len/getitem/setitem (moved to py_obj_ops.c) ------ */

/* ---- Generic object ops still stubbed -------------------------------- */
/* py_obj_call / py_obj_getattr / py_obj_setattr / py_obj_isinstance moved
 * to py_obj_ops.c as of Phase 3 (class + MRO support). */

PyObject *py_obj_repr(PyObject *o) {
    (void)o;
    /* TODO(phase3): produce a PyStrObject with repr text. */
    return NULL;
}

PyObject *py_obj_str(PyObject *o) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_STR) {
        py_incref(o);
        return o;
    }
    if (tag == PY_TYPE_INT) {
        return py_int_to_str_obj(o);
    }
    if (tag == PY_TYPE_EXC) {
        PyObject *msg = py_exc_get_message(o);
        if (msg != NULL) {
            py_incref(msg);
            return msg;
        }
        /* Empty string fallback — currently represented as NULL so the
         * caller (py_print) prints ``<null>``; matching CPython's empty
         * ``str(ValueError())`` => "" needs a shared empty PyStrObject
         * singleton (future work). */
        return NULL;
    }
    PyObject *dunder = py_user_str_dispatch(o);
    if (dunder != NULL) return dunder;
    return NULL;
}

/* ---- Exceptions (Phase 3) -------------------------------------------- */
/* py_raise / py_current_exception / py_clear_exception / py_exc_new and
 * the exception type/table live in py_exc.c. */
