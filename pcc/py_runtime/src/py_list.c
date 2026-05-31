/* pcc/py_runtime/src/py_list.c
 *
 * Growable array of PyObject* with owned references.
 *
 * Phase 2 scope (complete Section 3 `py_list_*` ABI):
 *   - new, append, get, set, len, concat, contains
 *   - slice, extend, insert, pop, remove, index, count, reverse
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
    (void)pcc_gc_backend4_zpage_register_owner_payload_span(
        (PyObject *)l,
        l->items,
        cap * (int64_t)sizeof(PyObject *)
    );
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

static int normalize_slice(PyObject *lo, PyObject *hi, PyObject *step,
                           int64_t len, int64_t *lo_out,
                           int64_t *hi_out, int64_t *step_out) {
    int64_t step_v = 1;
    if (!is_none_or_null(step)) {
        step_v = py_obj_index_i64(step);
        if (py_err_occurred()) return -1;
        if (step_v == 0) return -1;
    }

    int64_t lo_v, hi_v;
    if (step_v > 0) {
        lo_v = is_none_or_null(lo) ? 0   : py_obj_index_i64(lo);
        if (py_err_occurred()) return -1;
        hi_v = is_none_or_null(hi) ? len : py_obj_index_i64(hi);
        if (py_err_occurred()) return -1;
    } else {
        lo_v = is_none_or_null(lo) ? len - 1 : py_obj_index_i64(lo);
        if (py_err_occurred()) return -1;
        hi_v = is_none_or_null(hi) ? -1      : py_obj_index_i64(hi);
        if (py_err_occurred()) return -1;
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
            if (is_none_or_null(hi)) {
                hi_v = -1;
            } else {
                hi_v += len;
                if (hi_v < 0) hi_v = -1;
            }
        }
        if (hi_v >= len) hi_v = len - 1;
    }

    *lo_out = lo_v;
    *hi_out = hi_v;
    *step_out = step_v;
    return 0;
}

static int64_t slice_count(int64_t lo, int64_t hi, int64_t step) {
    int64_t n = 0;
    if (step > 0) {
        for (int64_t i = lo; i < hi; i += step) n++;
    } else {
        for (int64_t i = lo; i > hi; i += step) {
            if (i < 0) break;
            n++;
        }
    }
    return n;
}

static int64_t seq_len(PyObject *seq) {
    if (seq == NULL) return -1;
    int32_t tag = py_type_of(seq);
    if (tag == PY_TYPE_LIST) return ((PyListObject *)seq)->length;
    if (tag == PY_TYPE_TUPLE) return ((PyTupleObject *)seq)->len;
    return -1;
}

static PyObject *seq_get_borrowed(PyObject *seq, int64_t i) {
    int32_t tag = py_type_of(seq);
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)seq;
        return pcc_gc_load_ptr(seq, &l->items[i]);
    }
    if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)seq;
        return pcc_gc_load_ptr(seq, &t->items[i]);
    }
    return NULL;
}

static inline int64_t list_int_to_i64_or_zero(PyObject *v) {
    if (v == NULL) return 0;
    if (PY_IS_TAGGED_INT(v)) return py_untag_int(v);
    if (py_header(v)->type_tag != PY_TYPE_INT) return 0;
    int overflow = 0;
    int64_t out = py_bigint_to_i64((const PyIntObject *)v, &overflow);
    return overflow ? 0 : out;
}

static void list_delete_index(PyListObject *l, int64_t idx) {
    PyObject *old = pcc_gc_load_ptr((PyObject *)l, &l->items[idx]);
    l->items[idx] = NULL;
    if (old != NULL) py_decref(old);
    if (idx < l->length - 1) {
        memmove(&l->items[idx],
                &l->items[idx + 1],
                (size_t)(l->length - idx - 1) * sizeof(PyObject *));
    }
    l->length--;
}

static int list_delete_range(PyListObject *l, int64_t lo, int64_t hi) {
    if (hi <= lo) return 0;
    for (int64_t i = lo; i < hi; i++) {
        PyObject *old = pcc_gc_load_ptr((PyObject *)l, &l->items[i]);
        l->items[i] = NULL;
        if (old != NULL) py_decref(old);
    }
    if (hi < l->length) {
        memmove(&l->items[lo],
                &l->items[hi],
                (size_t)(l->length - hi) * sizeof(PyObject *));
    }
    l->length -= hi - lo;
    return 0;
}

/* ---- Public API ------------------------------------------------------- */

PyObject *py_list_new(int64_t initial_capacity) {
    PyListObject *l = (PyListObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyListObject), PY_TYPE_LIST, 0
    );
    if (l == NULL) return NULL;
    l->length     = 0;
    l->items      = NULL;
    if (initial_capacity < 4) initial_capacity = 4;
    l->capacity = initial_capacity;
    l->items = (PyObject **)malloc((size_t)initial_capacity * sizeof(PyObject *));
    if (l->items == NULL) {
        py_decref((PyObject *)l);
        return NULL;
    }
    (void)pcc_gc_backend4_zpage_register_owner_payload_span(
        (PyObject *)l,
        l->items,
        initial_capacity * (int64_t)sizeof(PyObject *)
    );
    py_gc_track((PyObject *)l);
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
    l->items[l->length] = NULL;
    pcc_gc_store_ptr(lst, &l->items[l->length], item);
    l->length++;
}

PyObject *py_list_get(PyObject *lst, int64_t i) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        /* TODO(phase3): raise IndexError */
        return NULL;
    }
    PyObject *v = pcc_gc_load_ptr(lst, &l->items[idx]);
    py_incref(v);
    return v;
}

/* a[i] subscript: like py_list_get but raises IndexError on out-of-range so a
 * surrounding try/except can catch it. py_list_get stays non-raising for other
 * internal callers. Negative indices normalize like CPython. */
PyObject *py_list_getitem(PyObject *lst, int64_t i) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        py_raise(py_exc_new(PY_EXC_INDEXERROR, "list index out of range"));
        return NULL;
    }
    PyObject *v = pcc_gc_load_ptr(lst, &l->items[idx]);
    py_incref(v);
    return v;
}

int64_t py_list_get_i64(PyObject *lst, int64_t i) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        return 0;
    }
    PyObject *v = pcc_gc_load_ptr(lst, &l->items[idx]);
    return list_int_to_i64_or_zero(v);
}

int64_t py_list_get_i64_nonnegative(PyObject *lst, int64_t i) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    if (i < 0 || i >= l->length) {
        return 0;
    }
    PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
    return list_int_to_i64_or_zero(v);
}

void py_list_set(PyObject *lst, int64_t i, PyObject *item) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;
    int64_t idx = normalize_index(i, l->length, 0);
    if (idx < 0) {
        /* TODO(phase3): raise IndexError */
        return;
    }
    pcc_gc_store_ptr(lst, &l->items[idx], item);
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
        PyObject *v = pcc_gc_load_ptr(a, &la->items[i]);
        py_incref(v);
        lo->items[lo->length++] = v;
    }
    for (int64_t i = 0; i < lb->length; i++) {
        PyObject *v = pcc_gc_load_ptr(b, &lb->items[i]);
        py_incref(v);
        lo->items[lo->length++] = v;
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
            PyObject *v = pcc_gc_load_ptr(src, &ls->items[i]);
            py_incref(v);
            lo->items[lo->length++] = v;
        }
    }
    return out;
}

int64_t py_list_contains(PyObject *lst, PyObject *item) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    for (int64_t i = 0; i < l->length; i++) {
        PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
        if (py_obj_eq(v, item)) return 1;
    }
    return 0;
}

/* ---- Slice ------------------------------------------------------------ */

PyObject *py_list_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step) {
    if (lst == NULL) return NULL;
    PyListObject *l = (PyListObject *)lst;
    int64_t len = l->length;

    int64_t lo_v, hi_v, step_v;
    if (normalize_slice(lo, hi, step, len, &lo_v, &hi_v, &step_v) != 0) {
        return NULL;
    }

    /* Walk and build. */
    PyObject *out = py_list_new(4);
    if (out == NULL) return NULL;
    PyListObject *lo_obj = (PyListObject *)out;

    if (step_v > 0) {
        for (int64_t i = lo_v; i < hi_v; i += step_v) {
            if (grow_if_needed(lo_obj, lo_obj->length + 1) != 0) {
                py_decref(out);
                return NULL;
            }
            PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
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
            PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
            py_incref(v);
            lo_obj->items[lo_obj->length++] = v;
        }
    }

    return out;
}

int64_t py_list_set_slice(PyObject *lst, PyObject *lo, PyObject *hi,
                          PyObject *step, PyObject *replacement) {
    if (lst == NULL || replacement == NULL) return -1;
    PyListObject *l = (PyListObject *)lst;
    int64_t len = l->length;
    int64_t lo_v, hi_v, step_v;
    if (normalize_slice(lo, hi, step, len, &lo_v, &hi_v, &step_v) != 0) {
        return -1;
    }

    int64_t repl_len = seq_len(replacement);
    if (repl_len < 0) return -1;

    if (step_v == 1) {
        int64_t range_hi = hi_v;
        if (range_hi < lo_v) range_hi = lo_v;
        int64_t remove_len = range_hi - lo_v;
        int64_t new_len = len - remove_len + repl_len;
        if (grow_if_needed(l, new_len) != 0) return -1;
        for (int64_t i = lo_v; i < lo_v + remove_len; i++) {
            PyObject *old = pcc_gc_load_ptr(lst, &l->items[i]);
            l->items[i] = NULL;
            if (old != NULL) py_decref(old);
        }
        if (repl_len != remove_len && range_hi < len) {
            memmove(&l->items[lo_v + repl_len],
                    &l->items[range_hi],
                    (size_t)(len - range_hi) * sizeof(PyObject *));
        }
        for (int64_t i = 0; i < repl_len; i++) {
            PyObject *v = seq_get_borrowed(replacement, i);
            l->items[lo_v + i] = NULL;
            pcc_gc_store_ptr(lst, &l->items[lo_v + i], v);
        }
        l->length = new_len;
        return 0;
    }

    int64_t expected = slice_count(lo_v, hi_v, step_v);
    if (repl_len != expected) return -1;
    int64_t idx = lo_v;
    for (int64_t i = 0; i < repl_len; i++) {
        if (idx < 0 || idx >= len) return -1;
        PyObject *v = seq_get_borrowed(replacement, i);
        PyObject *old = pcc_gc_load_ptr(lst, &l->items[idx]);
        l->items[idx] = NULL;
        if (old != NULL) py_decref(old);
        pcc_gc_store_ptr(lst, &l->items[idx], v);
        idx += step_v;
    }
    return 0;
}

int64_t py_list_del_slice(PyObject *lst, PyObject *lo, PyObject *hi,
                          PyObject *step) {
    if (lst == NULL) return -1;
    PyListObject *l = (PyListObject *)lst;
    int64_t len = l->length;
    int64_t lo_v, hi_v, step_v;
    if (normalize_slice(lo, hi, step, len, &lo_v, &hi_v, &step_v) != 0) {
        return -1;
    }

    if (step_v == 1) {
        return list_delete_range(l, lo_v, hi_v);
    }

    int64_t count = slice_count(lo_v, hi_v, step_v);
    if (count <= 0) return 0;
    if (step_v > 0) {
        int64_t idx = lo_v + (count - 1) * step_v;
        for (int64_t n = 0; n < count; n++) {
            list_delete_index(l, idx);
            idx -= step_v;
        }
    } else {
        int64_t idx = lo_v;
        for (int64_t n = 0; n < count; n++) {
            list_delete_index(l, idx);
            idx += step_v;
        }
    }
    return 0;
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
            PyObject *v = pcc_gc_load_ptr(b, &lb->items[i]);
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
            PyObject *v = pcc_gc_load_ptr(b, &tb->items[i]);
            py_incref(v);
            la->items[la->length++] = v;
        }
        return;
    }

    PyObject *it = py_obj_iter(b);
    if (it == NULL) return;
    for (;;) {
        PyObject *item = py_obj_next(it);
        if (item == NULL) {
            if (py_err_occurred()) {
                PyObject *cur = py_current_exception();
                PyObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (py_exc_matches(cur, stop)) {
                    py_clear_exception();
                    break;
                }
            }
            py_decref(it);
            return;
        }
        py_list_append(a, item);
        py_decref(item);
    }
    py_decref(it);
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
    l->items[idx] = NULL;
    pcc_gc_store_ptr(lst, &l->items[idx], item);
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

    PyObject *v = pcc_gc_load_ptr(lst, &l->items[idx]);   /* ownership transferred to caller */

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
        PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
        if (py_obj_eq(v, item)) {
            l->items[i] = NULL;
            if (v != NULL) py_decref(v);
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

void py_list_clear(PyObject *lst) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;
    int64_t n = l->length;
    l->length = 0;
    for (int64_t i = 0; i < n; i++) {
        PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
        l->items[i] = NULL;
        if (v != NULL) py_decref(v);
    }
}

void py_obj_clear(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return;
    int32_t tag = py_type_of(obj);
    if (tag == PY_TYPE_LIST) {
        py_list_clear(obj);
    } else if (tag == PY_TYPE_DICT) {
        py_dict_clear(obj);
    }
}

/* ---- Index ------------------------------------------------------------ */

int64_t py_list_index(PyObject *lst, PyObject *item) {
    if (lst == NULL) return -1;
    PyListObject *l = (PyListObject *)lst;
    for (int64_t i = 0; i < l->length; i++) {
        PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
        if (py_obj_eq(v, item)) return i;
    }
    return -1;
}

int64_t py_list_count(PyObject *lst, PyObject *item) {
    if (lst == NULL) return 0;
    PyListObject *l = (PyListObject *)lst;
    int64_t count = 0;
    for (int64_t i = 0; i < l->length; i++) {
        PyObject *v = pcc_gc_load_ptr(lst, &l->items[i]);
        if (py_obj_eq(v, item)) count++;
    }
    return count;
}

void py_list_reverse(PyObject *lst) {
    if (lst == NULL) return;
    PyListObject *l = (PyListObject *)lst;
    int64_t i = 0;
    int64_t j = l->length - 1;
    while (i < j) {
        PyObject *left = pcc_gc_load_ptr(lst, &l->items[i]);
        PyObject *right = pcc_gc_load_ptr(lst, &l->items[j]);
        l->items[i] = right;
        l->items[j] = left;
        i++;
        j--;
    }
}
