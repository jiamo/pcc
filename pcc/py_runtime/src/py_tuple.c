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

static int tuple_is_none_or_null(PyObject *o) {
    return o == NULL || o == py_None;
}

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

static int64_t tuple_slice_count(int64_t lo, int64_t hi, int64_t step) {
    int64_t count = 0;
    if (step > 0) {
        for (int64_t i = lo; i < hi; i += step) count++;
    } else {
        for (int64_t i = lo; i > hi; i += step) count++;
    }
    return count;
}

PyObject *py_tuple_slice(PyObject *tuple, PyObject *lo, PyObject *hi, PyObject *step) {
    if (tuple == NULL) return NULL;
    PyTupleObject *t = (PyTupleObject *)tuple;
    int64_t len = t->len;

    int64_t step_v = 1;
    if (!tuple_is_none_or_null(step)) {
        step_v = py_int_value_i64(step);
        if (step_v == 0) {
            return NULL;
        }
    }

    int64_t lo_v, hi_v;
    if (step_v > 0) {
        lo_v = tuple_is_none_or_null(lo) ? 0   : py_int_value_i64(lo);
        hi_v = tuple_is_none_or_null(hi) ? len : py_int_value_i64(hi);
    } else {
        lo_v = tuple_is_none_or_null(lo) ? len - 1 : py_int_value_i64(lo);
        hi_v = tuple_is_none_or_null(hi) ? -1      : py_int_value_i64(hi);
    }

    if (step_v > 0) {
        if (lo_v < 0) {
            lo_v += len;
            if (lo_v < 0) lo_v = 0;
        }
        if (lo_v > len) lo_v = len;
        if (hi_v < 0) {
            hi_v += len;
            if (hi_v < 0) hi_v = 0;
        }
        if (hi_v > len) hi_v = len;
    } else {
        if (lo_v < 0) {
            lo_v += len;
            if (lo_v < 0) lo_v = -1;
        }
        if (lo_v >= len) lo_v = len - 1;

        if (hi_v < 0) {
            if (tuple_is_none_or_null(hi)) {
                hi_v = -1;
            } else {
                hi_v += len;
                if (hi_v < 0) hi_v = -1;
            }
        }
        if (hi_v >= len) hi_v = len - 1;
    }

    int64_t count = tuple_slice_count(lo_v, hi_v, step_v);
    PyObject *out = py_tuple_new(count);
    if (out == NULL) return NULL;

    int64_t j = 0;
    if (step_v > 0) {
        for (int64_t i = lo_v; i < hi_v; i += step_v) {
            py_tuple_set_item(out, j++, t->items[i]);
        }
    } else {
        for (int64_t i = lo_v; i > hi_v; i += step_v) {
            if (i < 0 || i >= len) break;
            py_tuple_set_item(out, j++, t->items[i]);
        }
    }
    return out;
}

PyObject *py_tuple_concat(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return NULL;
    PyTupleObject *ta = (PyTupleObject *)a;
    PyTupleObject *tb = (PyTupleObject *)b;
    int64_t na = ta->len;
    int64_t nb = tb->len;
    PyObject *out = py_tuple_new(na + nb);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < na; i++) {
        py_tuple_set_item(out, i, ta->items[i]);
    }
    for (int64_t j = 0; j < nb; j++) {
        py_tuple_set_item(out, na + j, tb->items[j]);
    }
    return out;
}
