/* pcc/py_runtime/src/py_list.c
 *
 * Growable array of PyObject* with owned references.
 *
 * Phase 2 scope (complete Section 3 `py_list_*` ABI):
 *   - new, append, get, set, len, concat, contains
 *   - slice, extend, insert, pop, remove, index
 *
 * Growth policy: double on overflow, starting at max(4, initial_capacity).
 * Refcount ownership:
 *   - The list owns one ref per stored item.
 *   - append/insert/set: INCREF the new item; DECREF any displaced item.
 *   - get: returns a new (incref'd) ref.
 *   - pop/remove: slot's existing ref is transferred to the caller (no
 *     re-incref); shifted-over slots keep their existing refs.
 *   - slice: new list owns fresh INCREFs to the sliced items.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <assert.h>

/* ---- Internals -------------------------------------------------------- */

/* Reserve capacity for at least `want` items (total, not additional). */
static int grow_if_needed(PyListObject *l, int64_t want) {
    if (l->capacity >= want) return 0;
    int64_t cap = l->capacity > 0 ? l->capacity : 4;
    while (cap < want) cap *= 2;
    PyObject **nitems = (PyObject **)realloc(l->items, (size_t)cap * sizeof(PyObject *));
    if (nitems == NULL) return -1;
    l->items = nitems;
    l->capacity = cap;
    return 0;
}

/* Normalize a Python index.
 *   clip == 0 (wrap mode, for __getitem__/__setitem__):
 *     - Negative: add len. If still out-of-range, returns -1 (caller raises
 *       IndexError).
 *   clip == 1 (slice/insert mode):
 *     - Negative: add len, then saturate to 0.
 *     - Too large: saturate to len.
 */
static int64_t normalize_index(int64_t i, int64_t len, int clip) {
    if (clip) {
        if (i < 0) {
            i += len;
            if (i < 0) i = 0;
        }
        if (i > len) i = len;
        return i;
    }
    if (i < 0) i += len;
    if (i < 0 || i >= len) return -1;
    return i;
}

/* Return 1 if o is NULL or py_None (treated as "no argument"). */
static int is_none_or_null(PyObject *o) {
    return o == NULL || o == py_None;
}

/* ---- Public API ------------------------------------------------------- */

PyObject *py_list_new(int64_t initial_capacity) {
    PyListObject *l = (PyListObject *)malloc(sizeof(PyListObject));
    if (l == NULL) return NULL;
    l->h.refcount = 1;
    l->h.type_tag = PY_TYPE_LIST;
    l->h.flags    = 0;
    l->length     = 0;
    if (initial_capacity < 4) initial_capacity = 4;
    l->capacity = initial_capacity;
    l->items = (PyObject **)malloc((size_t)initial_capacity * sizeof(PyObject *));
    if (l->items == NULL) {
        free(l);
        return NULL;
    }
    return (PyObject *)l;
}

void py_list_append(PyObject *lst, PyObject *item) {
    if (lst == NULL) return;
    assert(!PY_IS_TAGGED_INT(lst));
    PyListObject *l = (PyListObject *)lst;
    assert(l->h.type_tag == PY_TYPE_LIST);
    if (grow_if_needed(l, l->length + 1) != 0) {
        /* TODO(phase3): raise MemoryError */
        return;
    }
    py_incref(item);
    l->items[l->length++] = item;
}

PyObject *py_list_get(PyObject *lst, int64_t i) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        /* TODO(phase3): raise IndexError */
        return NULL;
    }
    PyObject *v = l->items[idx];
    py_incref(v);
    return v;
}

void py_list_set(PyObject *lst, int64_t i, PyObject *item) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        /* TODO(phase3): raise IndexError */
        return;
    }
    py_incref(item);
    py_decref(l->items[idx]);
    l->items[idx] = item;
}

int64_t py_list_len(PyObject *lst) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    return l->length;
}

PyObject *py_list_concat(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return NULL;
    PyListObject *la = (PyListObject *)a;
    PyListObject *lb = (PyListObject *)b;
    int64_t n = la->length + lb->length;
    PyObject *out = py_list_new(n > 0 ? n : 4);
    if (out == NULL) return NULL;
    PyListObject *lo = (PyListObject *)out;
    for (int64_t i = 0; i < la->length; i++) {
        py_incref(la->items[i]);
        lo->items[lo->length++] = la->items[i];
    }
    for (int64_t i = 0; i < lb->length; i++) {
        py_incref(lb->items[i]);
        lo->items[lo->length++] = lb->items[i];
    }
    return out;
}

/* ``[x] * n`` / ``n * [x]`` — returns a fresh list of length
 * ``src->length * count`` with each source slot copied ``count`` times
 * in order. Matches CPython's list-repeat semantics (elements share
 * refs with the source; incref'd once per copy). ``count <= 0`` yields
 * an empty list. */
PyObject *py_list_repeat(PyObject *src, int64_t count) {
    if (src == NULL) return NULL;
    PyListObject *ls = (PyListObject *)src;
    int64_t out_len = count > 0 ? ls->length * count : 0;
    PyObject *out = py_list_new(out_len > 0 ? out_len : 4);
    if (out == NULL) return NULL;
    PyListObject *lo = (PyListObject *)out;
    for (int64_t k = 0; k < (count > 0 ? count : 0); k++) {
        for (int64_t i = 0; i < ls->length; i++) {
            py_incref(ls->items[i]);
            lo->items[lo->length++] = ls->items[i];
        }
    }
    return out;
}

int64_t py_list_contains(PyObject *lst, PyObject *item) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    for (int64_t i = 0; i < l->length; i++) {
        if (py_obj_eq(l->items[i], item)) return 1;
    }
    return 0;
}

/* ---- Slice ------------------------------------------------------------ */

PyObject *py_list_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;
    int64_t len = l->length;

    /* 1. Resolve step. */
    int64_t step_v = 1;
    if (!is_none_or_null(step)) {
        step_v = py_int_value_i64(step);
        if (step_v == 0) {
            /* TODO(phase3): raise ValueError("slice step cannot be zero"). */
            return NULL;
        }
    }

    /* 2. Resolve lo / hi defaults per step sign (Python's
     *    slice.indices() semantics, simplified). */
    int64_t lo_v, hi_v;
    if (step_v > 0) {
        lo_v = is_none_or_null(lo) ? 0   : py_int_value_i64(lo);
        hi_v = is_none_or_null(hi) ? len : py_int_value_i64(hi);
    } else {
        lo_v = is_none_or_null(lo) ? len - 1 : py_int_value_i64(lo);
        hi_v = is_none_or_null(hi) ? -1      : py_int_value_i64(hi);
        /* "hi == -1" here is Python's conceptual "one before index 0" for
         * negative step; we keep it as a plain -1 sentinel below. */
    }

    /* 3. Clip bounds. For positive step: [0, len]; for negative step:
     *    [-1, len-1] (where -1 is exclusive lower bound meaning
     *    "before the start"). */
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
        /* Negative step. */
        if (lo_v < 0) {
            lo_v += len;
            if (lo_v < 0) lo_v = -1;  /* means "before start" */
        }
        if (lo_v >= len) lo_v = len - 1;

        if (hi_v < 0) {
            /* Allow the -1 explicit default to stay as -1. Otherwise clip. */
            if (is_none_or_null(hi)) {
                hi_v = -1;
            } else {
                hi_v += len;
                if (hi_v < 0) hi_v = -1;
            }
        }
        if (hi_v >= len) hi_v = len - 1;
    }

    /* 4. Walk and build. */
    PyObject *out = py_list_new(4);
    if (out == NULL) return NULL;
    PyListObject *lo_obj = (PyListObject *)out;

    if (step_v > 0) {
        for (int64_t i = lo_v; i < hi_v; i += step_v) {
            if (grow_if_needed(lo_obj, lo_obj->length + 1) != 0) {
                py_decref(out);
                return NULL;
            }
            PyObject *v = l->items[i];
            py_incref(v);
            lo_obj->items[lo_obj->length++] = v;
        }
    } else {
        for (int64_t i = lo_v; i > hi_v; i += step_v) {
            if (i < 0 || i >= len) break;
            if (grow_if_needed(lo_obj, lo_obj->length + 1) != 0) {
                py_decref(out);
                return NULL;
            }
            PyObject *v = l->items[i];
            py_incref(v);
            lo_obj->items[lo_obj->length++] = v;
        }
    }

    return out;
}

/* ---- Extend ----------------------------------------------------------- */

void py_list_extend(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return;
    PyListObject *la = (PyListObject *)a;

    int32_t btag = py_type_of(b);
    if (btag == PY_TYPE_LIST) {
        PyListObject *lb = (PyListObject *)b;
        /* Guard against self-extend: snapshot length before iterating. */
        int64_t bl = lb->length;
        if (grow_if_needed(la, la->length + bl) != 0) {
            /* TODO(phase3): raise MemoryError. */
            return;
        }
        for (int64_t i = 0; i < bl; i++) {
            PyObject *v = lb->items[i];
            py_incref(v);
            la->items[la->length++] = v;
        }
        return;
    }

    if (btag == PY_TYPE_TUPLE) {
        PyTupleObject *tb = (PyTupleObject *)b;
        int64_t bl = tb->len;
        if (grow_if_needed(la, la->length + bl) != 0) {
            return;
        }
        for (int64_t i = 0; i < bl; i++) {
            PyObject *v = tb->items[i];
            py_incref(v);
            la->items[la->length++] = v;
        }
        return;
    }

    /* TODO(phase2): generic iterable protocol (str, dict, set, user). */
}

/* ---- Insert ----------------------------------------------------------- */

void py_list_insert(PyObject *lst, int64_t i, PyObject *item) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;

    /* Python's list.insert clips: negative wraps, out-of-range saturates. */
    int64_t idx = normalize_index(i, l->length, 1);

    if (grow_if_needed(l, l->length + 1) != 0) {
        /* TODO(phase3): raise MemoryError. */
        return;
    }

    /* Shift tail [idx, length) right by one. */
    if (idx < l->length) {
        memmove(&l->items[idx + 1],
                &l->items[idx],
                (size_t)(l->length - idx) * sizeof(PyObject *));
    }
    py_incref(item);
    l->items[idx] = item;
    l->length++;
}

/* ---- Pop -------------------------------------------------------------- */

PyObject *py_list_pop(PyObject *lst, int64_t i) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;

    if (l->length == 0) {
        /* TODO(phase3): raise IndexError("pop from empty list"). */
        return NULL;
    }

    /* Sentinel: -1 means "pop last" (contract uses the common Python
     * default). Any other negative wraps normally. */
    int64_t idx;
    if (i == -1) {
        idx = l->length - 1;
    } else {
        idx = normalize_index(i, l->length, 0);
        if (idx < 0) {
            /* TODO(phase3): raise IndexError. */
            return NULL;
        }
    }

    PyObject *v = l->items[idx];   /* ownership transferred to caller */

    /* Shift tail (idx+1, length) left by one. */
    if (idx < l->length - 1) {
        memmove(&l->items[idx],
                &l->items[idx + 1],
                (size_t)(l->length - idx - 1) * sizeof(PyObject *));
    }
    l->length--;
    return v;
}

/* ---- Remove ----------------------------------------------------------- */

void py_list_remove(PyObject *lst, PyObject *item) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;

    for (int64_t i = 0; i < l->length; i++) {
        if (py_obj_eq(l->items[i], item)) {
            py_decref(l->items[i]);
            if (i < l->length - 1) {
                memmove(&l->items[i],
                        &l->items[i + 1],
                        (size_t)(l->length - i - 1) * sizeof(PyObject *));
            }
            l->length--;
            return;
        }
    }

    /* Not found: raise ValueError. py_exc_new is a Phase-3 stub that
     * returns NULL today; py_raise accepts NULL gracefully. When Phase 3
     * lands, this becomes a real exception. */
    PyObject *exc = py_exc_new(PY_TYPE_EXC, "list.remove(x): x not in list");
    py_raise(exc);
    /* py_raise incref's; drop our construction ref. */
    if (exc) py_decref(exc);
}

/* ---- Index ------------------------------------------------------------ */

int64_t py_list_index(PyObject *lst, PyObject *item) {
    if (lst == NULL) return -1;
    PyListObject *l = (PyListObject *)lst;
    for (int64_t i = 0; i < l->length; i++) {
        if (py_obj_eq(l->items[i], item)) return i;
    }
    return -1;
}
