/* pcc/py_runtime/src/py_tuple.c
 *
 * Fixed-length immutable sequence of PyObject*.
 *
 * Phase 2 layout:
 *
 *   typedef struct {
 *       PyObjectHeader h;
 *       int64_t  len;
 *       PyObject *items[];   // flexible array of owned refs
 *   } PyTupleObject;
 *
 * The header and payload live in one contiguous allocation, which matches
 * the contract exactly (see docs/plans/python-frontend-interfaces.md §3).
 * Deallocation is centralized in py_obj.c's dispatch table, so all we do
 * here is allocate, set (during construction), get (with INCREF) and
 * return len.
 *
 * Reference-counting rules:
 *   - py_tuple_new(n) zeros items[]; caller populates via set_item.
 *   - py_tuple_set_item INCREFs the stored item. The contract says "does
 *     NOT INCREF because tuple owns" in the sense that the caller is NOT
 *     donating its own ref — we take a fresh one so the caller still owns
 *     theirs. This matches Phase-1 behavior; simpler than a steal-protocol
 *     and makes construction from borrowed refs safe.
 *   - py_tuple_get returns a fresh INCREF'd ref that the caller owns.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

PyObject *py_tuple_new(int64_t n) {
    if (n < 0) n = 0;
    /* One contiguous allocation: header + n * sizeof(PyObject*). */
    size_t bytes = sizeof(PyTupleObject) + (size_t)n * sizeof(PyObject *);
    PyTupleObject *t = (PyTupleObject *)malloc(bytes);
    if (t == NULL) return NULL;
    t->h.refcount = 1;
    t->h.type_tag = PY_TYPE_TUPLE;
    t->h.flags    = 0;
    t->len        = n;
    if (n > 0) {
        memset(t->items, 0, (size_t)n * sizeof(PyObject *));
    }
    return (PyObject *)t;
}

void py_tuple_set_item(PyObject *tuple, int64_t i, PyObject *item) {
    /* Used during construction only. The slot is expected to be NULL —
     * we don't decref the old slot, only incref the new item so the
     * caller's ref is independent of the tuple's. */
    if (tuple == NULL) return;
    PyTupleObject *t = (PyTupleObject *)tuple;
    if (i < 0 || i >= t->len) return;
    py_incref(item);
    t->items[i] = item;
}

PyObject *py_tuple_get(PyObject *tuple, int64_t i) {
    if (tuple == NULL) return NULL;
    PyTupleObject *t = (PyTupleObject *)tuple;
    /* Python allows negative indices. */
    if (i < 0) i += t->len;
    if (i < 0 || i >= t->len) {
        /* TODO(phase3): raise IndexError. */
        return NULL;
    }
    PyObject *v = t->items[i];
    py_incref(v);
    return v;
}

int64_t py_tuple_len(PyObject *tuple) {
    if (tuple == NULL) return 0;
    return ((PyTupleObject *)tuple)->len;
}
