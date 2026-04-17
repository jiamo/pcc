/* pcc/py_runtime/src/py_obj_dealloc.c
 *
 * Type-specific deallocators, split out of py_obj.c so the
 * dispatch (py_incref/py_decref) can be independently replaced by
 * the pcc-Python port in py_obj.py while these type-table entries
 * stay as C (they touch flexible-array-member tails and raw
 * struct fields that the current pcc-Python surface cannot
 * express without a lot of extra substrate helpers).
 *
 * Functions are no longer static — py_obj.py consumes them via
 * extern declarations when the refcount logic is ported. The
 * cc-C py_obj.c calls them via the same names.
 */

#include "py_internal.h"
#include <stdlib.h>

void py_dealloc_int(PyObject *o) {
    free(o);
}

void py_dealloc_float(PyObject *o) {
    free(o);
}

void py_dealloc_str(PyObject *o) {
    /* PyStrObject uses a flexible-array-member tail — one free() is
     * sufficient. */
    free(o);
}

void py_dealloc_list(PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    for (int64_t i = 0; i < l->length; i++) {
        py_decref(l->items[i]);
    }
    free(l->items);
    free(l);
}

void py_dealloc_tuple(PyObject *o) {
    PyTupleObject *t = (PyTupleObject *)o;
    for (int64_t i = 0; i < t->len; i++) {
        py_decref(t->items[i]);
    }
    free(t);
}

void py_dealloc_dict(PyObject *o) {
    PyDictObject *d = (PyDictObject *)o;
    if (d->entries != NULL) {
        for (int64_t i = 0; i < d->entries_used; i++) {
            DictEntry *e = &d->entries[i];
            if (e->key != NULL) {
                py_decref(e->key);
                py_decref(e->value);
            }
        }
        free(d->entries);
    }
    free(d->indices);
    free(d);
}

void py_dealloc_set(PyObject *o) {
    PySetObject *s = (PySetObject *)o;
    if (s->entries != NULL) {
        for (int64_t i = 0; i < s->capacity; i++) {
            PyObject *k = s->entries[i].key;
            if (k != NULL && k != py_set_dummy) {
                py_decref(k);
            }
        }
        free(s->entries);
    }
    free(s);
}

void py_dealloc_generic(PyObject *o) {
    /* Fallback for types whose dealloc isn't specialized yet. */
    free(o);
}
