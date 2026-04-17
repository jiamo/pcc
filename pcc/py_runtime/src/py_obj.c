/* pcc/py_runtime/src/py_obj.c
 *
 * PyObject reference counting + dealloc dispatch.
 *
 * Phase 4c.11 split:
 *   - Immortal singletons (py_None/py_True/py_False) live in
 *     py_substrate.c so they remain exported when this module is
 *     replaced by the pcc-Python port.
 *   - Type-specific deallocators live in py_obj_dealloc.c so the
 *     dispatch here can be independently ported while the dealloc
 *     details (flexible-array-member free, child ref drop, etc.)
 *     stay C.
 */

#include "py_internal.h"
#include <assert.h>

PyObject *py_bool_from_bit(int b) {
    return b ? py_True : py_False;
}

void py_incref(PyObject *o) {
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;  /* tagged ints carry no refcount */
    PyObjectHeader *h = py_header(o);
    if (h->flags & PY_FLAG_IMMORTAL) return;
    h->refcount++;
}

void py_decref(PyObject *o) {
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;
    PyObjectHeader *h = py_header(o);
    if (h->flags & PY_FLAG_IMMORTAL) return;
    assert(h->refcount > 0 && "py_decref: refcount underflow");
    if (--h->refcount > 0) return;

    switch (h->type_tag) {
        case PY_TYPE_INT:      py_dealloc_int(o);      break;
        case PY_TYPE_FLOAT:    py_dealloc_float(o);    break;
        case PY_TYPE_STR:      py_dealloc_str(o);      break;
        case PY_TYPE_LIST:     py_dealloc_list(o);     break;
        case PY_TYPE_TUPLE:    py_dealloc_tuple(o);    break;
        case PY_TYPE_DICT:     py_dealloc_dict(o);     break;
        case PY_TYPE_SET:      py_dealloc_set(o);      break;
        case PY_TYPE_CLASS:    py_class_dealloc(o);    break;
        case PY_TYPE_INSTANCE: py_instance_dealloc(o); break;
        case PY_TYPE_EXC:      py_dealloc_exc(o);      break;
        default:
            /* PY_TYPE_USER+N tag: still an instance. */
            if (h->type_tag >= PY_TYPE_USER) {
                py_instance_dealloc(o);
            } else {
                py_dealloc_generic(o);
            }
            break;
    }
}
