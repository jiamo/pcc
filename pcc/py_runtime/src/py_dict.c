/* pcc/py_runtime/src/py_dict.c
 *
 * Hash table with split probe table + insertion-ordered entries array.
 *
 * Design (compact-dict, PEP 468 / PEP 509):
 *
 *   indices : int64_t[capacity]
 *       Open-addressing probe slots. Each holds an index into entries[],
 *       or PY_DICT_EMPTY (-1) or PY_DICT_TOMBSTONE (-2).
 *
 *   entries : DictEntry[capacity]
 *       Append-only insertion order. Iteration walks this directly, so
 *       dict preserves insertion order. Deletion sets key=value=NULL and
 *       leaves a hole; rehash compacts. capacity * 2/3 is the soft cap.
 *
 *   entries_used is the high-water mark into entries[]. New inserts land
 *   at entries_used and then bump it. `size` counts only live entries.
 *
 * Invariants:
 *   - capacity is a power of 2 (lets us do `& (capacity - 1)` for modulo).
 *   - For every live entry at entries[j], exactly one cell in indices[]
 *     holds the value j; all other indices cells are EMPTY or TOMBSTONE.
 *   - For every dead entry (key == NULL), there may exist TOMBSTONE cells
 *     in indices[] pointing at it until the next rehash.
 *   - 0 <= size <= entries_used <= capacity.
 *
 * Probing: we do the classic CPython-style perturbation that mixes the
 * high bits back in, yielding a decent random-ish probe sequence without
 * paying for a full second hash:
 *     perturb = hash;
 *     j = hash & mask;
 *     while (!match) {
 *         perturb >>= 5;
 *         j = (j * 5 + perturb + 1) & mask;
 *     }
 *
 * Hash/equality:
 *   - key hash via py_obj_hash(k).
 *   - key equality via py_obj_eq(a, b).
 *   We cache `hash` in the entry to avoid recomputing on rehash.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdint.h>

#define PY_DICT_INITIAL_CAPACITY  8   /* must be power of 2 */

/* ---- Forward decls ---------------------------------------------------- */
static int  py_dict_rehash(PyDictObject *d, int64_t new_capacity);

static int py_dict_pointer_can_have_header(PyObject *obj) {
    return pcc_gc_pointer_is_managed(obj) != 0;
}

static int py_object_is_dict(PyObject *obj) {
    if (!py_dict_pointer_can_have_header(obj)) return 0;
    return py_header(obj)->type_tag == PY_TYPE_DICT;
}

static PyObject *py_dict_entry_key(PyDictObject *d, DictEntry *e) {
    if (e->key == NULL) return NULL;
    if (__atomic_load_n(&pcc_gc_read_barrier_enabled, __ATOMIC_ACQUIRE) == 0) {
        return e->key;
    }
    return pcc_gc_load_ptr((PyObject *)d, &e->key);
}

static PyObject *py_dict_entry_value(PyDictObject *d, DictEntry *e) {
    if (e->value == NULL) return NULL;
    if (__atomic_load_n(&pcc_gc_read_barrier_enabled, __ATOMIC_ACQUIRE) == 0) {
        return e->value;
    }
    return pcc_gc_load_ptr((PyObject *)d, &e->value);
}

static int py_dict_keys_equal(PyObject *entry_key, PyObject *key) {
    if (entry_key == key) return 1;
    if (PY_IS_TAGGED_INT(entry_key) && PY_IS_TAGGED_INT(key)) return 0;
    if (!PY_IS_TAGGED_INT(entry_key)
        && !PY_IS_TAGGED_INT(key)
        && py_header(entry_key)->type_tag == PY_TYPE_STR
        && py_header(key)->type_tag == PY_TYPE_STR) {
        return py_str_eq(entry_key, key) != 0;
    }
    return py_obj_eq(entry_key, key) != 0;
}

/* ---- Allocation ------------------------------------------------------- */

static int py_dict_alloc_tables(PyDictObject *d, int64_t capacity) {
    int64_t *indices = (int64_t *)malloc((size_t)capacity * sizeof(int64_t));
    if (indices == NULL) return -1;
    DictEntry *entries = (DictEntry *)malloc((size_t)capacity * sizeof(DictEntry));
    if (entries == NULL) {
        free(indices);
        return -1;
    }
    for (int64_t i = 0; i < capacity; i++) indices[i] = PY_DICT_EMPTY;
    /* entries[] is only read for 0..entries_used, so no need to init. */
    d->indices      = indices;
    d->entries      = entries;
    d->capacity     = capacity;
    d->size         = 0;
    d->entries_used = 0;
    (void)pcc_gc_backend4_zpage_register_owner_payload_span(
        (PyObject *)d,
        d->entries,
        capacity * (int64_t)sizeof(DictEntry)
    );
    return 0;
}

PyObject *py_dict_new(void) {
    PyDictObject *d = (PyDictObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyDictObject), PY_TYPE_DICT, 0
    );
    if (d == NULL) return NULL;
    d->indices      = NULL;
    d->entries      = NULL;
    d->capacity     = 0;
    d->size         = 0;
    d->entries_used = 0;
    if (py_dict_alloc_tables(d, PY_DICT_INITIAL_CAPACITY) != 0) {
        py_decref((PyObject *)d);
        return NULL;
    }
    py_gc_track((PyObject *)d);
    return (PyObject *)d;
}

/* ---- Probing ---------------------------------------------------------- */

/* Locate the indices[] slot for `key`.
 *
 * On return:
 *   *out_slot      = index into indices[] where the key's entry would sit.
 *   *out_entry_idx = entries[] index if the key is live; -1 if missing.
 *
 * If the key is missing and there is a reusable TOMBSTONE slot, *out_slot
 * points at the first tombstone seen (so a subsequent insert can reuse it
 * without extra work). Otherwise *out_slot points at the EMPTY slot that
 * terminated the probe, which is also a valid insert target.
 */
static void py_dict_lookup(PyDictObject *d, int64_t hash, PyObject *key,
                           int64_t *out_slot, int64_t *out_entry_idx) {
    if (!py_object_is_dict((PyObject *)d)) {
        *out_slot = 0;
        *out_entry_idx = -1;
        return;
    }
    if (d->capacity <= 0 || d->indices == NULL || d->entries == NULL) {
        *out_slot = 0;
        *out_entry_idx = -1;
        return;
    }
    int64_t mask = d->capacity - 1;
    uint64_t perturb = (uint64_t)hash;
    int64_t j = (int64_t)((uint64_t)hash & (uint64_t)mask);
    int64_t first_tombstone = -1;
    int64_t probes = 0;
    int64_t limit = d->capacity * 2;

    while (probes < limit) {
        int64_t ix = d->indices[j];
        if (ix == PY_DICT_EMPTY) {
            /* Key not present; insertion target is the earliest tombstone
             * we saw, else this empty slot. */
            *out_slot = (first_tombstone >= 0) ? first_tombstone : j;
            *out_entry_idx = -1;
            return;
        }
        if (ix == PY_DICT_TOMBSTONE) {
            if (first_tombstone < 0) first_tombstone = j;
        } else {
            /* Live entry: compare hash cheap-first, then py_obj_eq. */
            DictEntry *e = &d->entries[ix];
            PyObject *entry_key = py_dict_entry_key(d, e);
            if (entry_key != NULL && e->hash == hash &&
                py_dict_keys_equal(entry_key, key)) {
                *out_slot = j;
                *out_entry_idx = ix;
                return;
            }
        }
        /* Advance probe. */
        perturb >>= 5;
        j = (int64_t)(((uint64_t)j * 5u + perturb + 1u) & (uint64_t)mask);
        probes++;
    }

    /* A healthy table always reaches an EMPTY slot before this. Treat a
     * bounded-out probe as a miss instead of letting a corrupt probe cycle
     * hang the self-hosted compiler indefinitely. */
    *out_slot = (first_tombstone >= 0) ? first_tombstone : 0;
    *out_entry_idx = -1;
}

/* Insert a (hash, key, value) into indices[] + entries[]. Assumes there
 * is room (load factor already checked) and that `key` is not yet
 * present (caller verified via lookup). INCREFs key and value. */
static void py_dict_insert_fresh(PyDictObject *d, int64_t hash,
                                 PyObject *key, PyObject *value) {
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    /* ix must be -1 here; caller's contract. */
    (void)ix;
    int64_t ei = d->entries_used++;
    DictEntry *e = &d->entries[ei];
    e->hash  = hash;
    e->key   = NULL;
    e->value = NULL;
    pcc_gc_store_ptr((PyObject *)d, &e->key, key);
    pcc_gc_store_ptr((PyObject *)d, &e->value, value);
    d->indices[slot] = ei;
    d->size++;
}

/* Rebuild indices[] and compact entries[] into a new capacity. `new_capacity`
 * must be a power of 2 and large enough to hold all live entries at load
 * factor < 2/3. */
static int py_dict_rehash(PyDictObject *d, int64_t new_capacity) {
    DictEntry *old_entries      = d->entries;
    int64_t    old_entries_used = d->entries_used;

    DictEntry *new_entries = (DictEntry *)malloc((size_t)new_capacity * sizeof(DictEntry));
    int64_t   *new_indices = (int64_t *)malloc((size_t)new_capacity * sizeof(int64_t));
    if (new_entries == NULL || new_indices == NULL) {
        free(new_entries);
        free(new_indices);
        return -1;
    }
    for (int64_t i = 0; i < new_capacity; i++) new_indices[i] = PY_DICT_EMPTY;

    /* Install the new buffers on the dict, free the old ones, then walk
     * the old entries in insertion order copying only live slots. This
     * compacts out any tombstoned holes. Refs are moved over — no
     * incref/decref — so the overall refcount stays balanced. */
    int64_t *old_indices = d->indices;
    d->indices      = new_indices;
    d->entries      = new_entries;
    d->capacity     = new_capacity;
    d->size         = 0;
    d->entries_used = 0;
    (void)pcc_gc_backend4_zpage_register_owner_payload_span(
        (PyObject *)d,
        d->entries,
        new_capacity * (int64_t)sizeof(DictEntry)
    );
    free(old_indices);

    for (int64_t i = 0; i < old_entries_used; i++) {
        DictEntry *e = &old_entries[i];
        PyObject *entry_key = py_dict_entry_key(d, e);
        if (entry_key == NULL) continue;  /* skip dead entries */
        PyObject *entry_value = py_dict_entry_value(d, e);
        /* Insert without incref'ing again — we're moving refs over. We
         * can't reuse insert_fresh which would double-count. Inline the
         * move: find slot via lookup, copy entry, bump counters. */
        int64_t slot, ix;
        py_dict_lookup(d, e->hash, entry_key, &slot, &ix);
        (void)ix;  /* guaranteed -1 during rehash */
        int64_t ei = d->entries_used++;
        DictEntry *ne = &d->entries[ei];
        ne->hash  = e->hash;
        ne->key   = entry_key;
        ne->value = entry_value;
        pcc_gc_note_slot_write_barrier((PyObject *)d, &ne->key, entry_key);
        pcc_gc_note_slot_write_barrier((PyObject *)d, &ne->value, entry_value);
        d->indices[slot] = ei;
        d->size++;
    }
    free(old_entries);
    return 0;
}

/* Decide the next capacity when we need to grow (or shrink). CPython uses
 * `capacity <<= (size > 50000 ? 1 : 2)`; we keep it simple — double when
 * we exceed 2/3 fill, hold otherwise. */
static int py_dict_maybe_grow(PyDictObject *d) {
    /* entries_used counts both live and tombstoned slots; once we're past
     * 2/3 fill of capacity, rehash. Choose new capacity = capacity * 2 if
     * the table is actually dense with live entries, else keep the same
     * capacity (just compact the tombstones). */
    int64_t threshold = (d->capacity * 2) / 3;
    if (d->entries_used <= threshold) return 0;

    int64_t new_cap = d->capacity;
    if (d->size > threshold / 2) {
        /* Most of the fill is real data → grow. */
        new_cap = d->capacity * 2;
    }
    return py_dict_rehash(d, new_cap);
}

/* ---- Public API ------------------------------------------------------- */

void py_dict_set(PyObject *dict, PyObject *key, PyObject *value) {
    if (!py_object_is_dict(dict) || key == NULL) return;
    PyDictObject *d = (PyDictObject *)dict;

    int64_t hash = py_obj_hash(key);
    if (py_err_occurred()) return;
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix >= 0) {
        /* Update existing. Replace value; key stays (Python semantics:
         * dict[k] = v keeps the original key object). */
        DictEntry *e = &d->entries[ix];
        pcc_gc_store_ptr(dict, &e->value, value);
        return;
    }
    /* Fresh insert. */
    py_dict_insert_fresh(d, hash, key, value);
    (void)py_dict_maybe_grow(d);
    /* If maybe_grow fails, we've already inserted; the table just gets
     * denser. A future op will try again. */
}

PyObject *py_dict_get(PyObject *dict, PyObject *key) {
    if (!py_object_is_dict(dict) || key == NULL) return NULL;
    PyDictObject *d = (PyDictObject *)dict;

    int64_t hash = py_obj_hash(key);
    if (py_err_occurred()) return NULL;
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix < 0) return NULL;
    PyObject *v = pcc_gc_load_ptr(dict, &d->entries[ix].value);
    if (v == NULL) return NULL;
    py_incref(v);
    return v;
}

PyObject *py_dict_get_default(PyObject *dict, PyObject *key, PyObject *def) {
    PyObject *v = py_dict_get(dict, key);
    if (v != NULL) return v;
    if (py_err_occurred()) return NULL;
    py_incref(def);
    return def;
}

/* d[key] subscript: like py_dict_get but raises KeyError (carrying the key,
 * like CPython) when the key is absent, so a surrounding try/except can catch
 * it. py_dict_get stays non-raising for dict.get()/setdefault()/etc. */
PyObject *py_dict_getitem(PyObject *dict, PyObject *key) {
    PyObject *v = py_dict_get(dict, key);
    if (v == NULL) {
        if (py_err_occurred()) return NULL;
        PyObject *exc = py_exc_new_with_value(PY_EXC_KEYERROR, key);
        py_raise(exc);
        if (exc) py_decref(exc);
        return NULL;
    }
    return v;
}

/* dict.fromkeys(iterable, value): new dict with each element of iterable as a
 * key mapped to value (caller passes None when omitted). Iterator protocol;
 * clears a terminal StopIteration like the sorted() iterator path. */
PyObject *py_dict_fromkeys(PyObject *iterable, PyObject *value) {
    PyObject *d = py_dict_new();
    if (d == NULL) return NULL;
    PyObject *it = py_obj_iter(iterable);
    if (it == NULL) {
        py_runtime_error_if_unset(
            "py_obj_iter",
            "dict.fromkeys could not create an iterator"
        );
        py_decref(d);
        return NULL;
    }
    for (;;) {
        PyObject *k = py_obj_next(it);
        if (k == NULL) {
            if (py_err_occurred()) {
                PyObject *cur = py_current_exception();
                PyObject *stop = py_exc_builtin_class(PY_EXC_STOPITERATION);
                if (py_exc_matches(cur, stop)) {
                    py_clear_exception();
                    break;
                }
            } else {
                py_runtime_error_if_unset(
                    "py_obj_next",
                    "dict.fromkeys iterator returned NULL without an exception"
                );
            }
            py_decref(it);
            py_decref(d);
            return NULL;
        }
        py_dict_set(d, k, value);
        if (py_err_occurred()) {
            py_decref(k);
            py_decref(it);
            py_decref(d);
            return NULL;
        }
        py_decref(k);
    }
    py_decref(it);
    return d;
}

PyObject *py_dict_pop(PyObject *dict, PyObject *key) {
    PyObject *v = py_dict_get(dict, key);
    if (v == NULL) {
        if (py_err_occurred()) return NULL;
        PyObject *exc = py_exc_new_with_value(PY_EXC_KEYERROR, key);
        py_raise(exc);
        if (exc) py_decref(exc);
        return NULL;
    }
    (void)py_dict_del(dict, key);
    return v;
}

/* dict.popitem(): remove and return the LAST-inserted (key, value) pair as a
 * 2-tuple (dicts are insertion-ordered). Raises KeyError when empty. The tuple
 * increfs key/value (py_tuple_set_item), so py_dict_del's decref leaves them
 * owned by the returned tuple. */
PyObject *py_dict_popitem(PyObject *dict) {
    if (!py_object_is_dict(dict)) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    for (int64_t i = d->entries_used - 1; i >= 0; i--) {
        DictEntry *e = &d->entries[i];
        PyObject *key = py_dict_entry_key(d, e);
        if (key == NULL) continue;           /* dead slot */
        PyObject *val = py_dict_entry_value(d, e);
        PyObject *tup = py_tuple_new(2);
        if (tup == NULL) return NULL;
        py_tuple_set_item(tup, 0, key);
        py_tuple_set_item(tup, 1, val);
        (void)py_dict_del(dict, key);
        return tup;
    }
    /* Empty dict: CPython raises KeyError('popitem(): dictionary is empty');
     * we raise a bare KeyError (consistent with the pcc-Python port tier,
     * which has no cstr message helper here). Rare error path. */
    PyObject *exc = py_exc_new_with_value(PY_EXC_KEYERROR, NULL);
    py_raise(exc);
    if (exc) py_decref(exc);
    return NULL;
}

int64_t py_dict_contains(PyObject *dict, PyObject *key) {
    if (!py_object_is_dict(dict) || key == NULL) return 0;
    PyDictObject *d = (PyDictObject *)dict;
    int64_t hash = py_obj_hash(key);
    if (py_err_occurred()) return 0;
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    return ix >= 0 ? 1 : 0;
}

int64_t py_dict_del(PyObject *dict, PyObject *key) {
    if (!py_object_is_dict(dict) || key == NULL) return -1;
    PyDictObject *d = (PyDictObject *)dict;
    int64_t hash = py_obj_hash(key);
    if (py_err_occurred()) return -1;
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix < 0) return -1;

    DictEntry *e = &d->entries[ix];
    py_decref(py_dict_entry_key(d, e));
    py_decref(py_dict_entry_value(d, e));
    e->key   = NULL;
    e->value = NULL;
    /* Leave e->hash intact; it's just a cached int and aids debugging. */
    d->indices[slot] = PY_DICT_TOMBSTONE;
    d->size--;
    return 0;
}

void py_dict_clear(PyObject *dict) {
    if (!py_object_is_dict(dict)) return;
    PyDictObject *d = (PyDictObject *)dict;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        PyObject *key = py_dict_entry_key(d, e);
        if (key == NULL) continue;
        py_decref(key);
        py_decref(py_dict_entry_value(d, e));
        e->hash = 0;
        e->key = NULL;
        e->value = NULL;
    }
    for (int64_t i = 0; i < d->capacity; i++) {
        d->indices[i] = PY_DICT_EMPTY;
    }
    d->size = 0;
    d->entries_used = 0;
}

int64_t py_dict_len(PyObject *dict) {
    if (!py_object_is_dict(dict)) return 0;
    return ((PyDictObject *)dict)->size;
}

int64_t py_dict_entries_used(PyObject *dict) {
    if (!py_object_is_dict(dict)) return 0;
    return ((PyDictObject *)dict)->entries_used;
}

PyObject *py_dict_entry_key_at(PyObject *dict, int64_t i) {
    if (!py_object_is_dict(dict) || i < 0) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    if (i >= d->entries_used) return NULL;
    PyObject *key = py_dict_entry_key(d, &d->entries[i]);
    if (key != NULL) py_incref(key);
    return key;
}

PyObject *py_dict_entry_value_at(PyObject *dict, int64_t i) {
    if (!py_object_is_dict(dict) || i < 0) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    if (i >= d->entries_used) return NULL;
    DictEntry *e = &d->entries[i];
    PyObject *key = py_dict_entry_key(d, e);
    if (key == NULL) return NULL;
    PyObject *value = py_dict_entry_value(d, e);
    if (value != NULL) py_incref(value);
    return value;
}

PyObject *py_dict_keys(PyObject *dict) {
    if (!py_object_is_dict(dict)) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        PyObject *key = py_dict_entry_key(d, e);
        if (key == NULL) continue;
        py_list_append(out, key);
    }
    return out;
}

PyObject *py_dict_values(PyObject *dict) {
    if (!py_object_is_dict(dict)) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        PyObject *key = py_dict_entry_key(d, e);
        if (key == NULL) continue;
        py_list_append(out, py_dict_entry_value(d, e));
    }
    return out;
}

PyObject *py_dict_items(PyObject *dict) {
    if (!py_object_is_dict(dict)) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        PyObject *key = py_dict_entry_key(d, e);
        if (key == NULL) continue;
        PyObject *value = py_dict_entry_value(d, e);
        /* Build a 2-tuple (key, value) for each live entry. */
        PyObject *pair = py_tuple_new(2);
        if (pair == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(pair, 0, key);
        py_tuple_set_item(pair, 1, value);
        py_list_append(out, pair);
        py_decref(pair);  /* list took its own ref */
    }
    return out;
}

void py_dict_update(PyObject *dst, PyObject *src) {
    if (!py_object_is_dict(dst) || !py_object_is_dict(src)) return;
    PyDictObject *s = (PyDictObject *)src;
    int64_t used = s->entries_used;
    for (int64_t i = 0; i < used; i++) {
        DictEntry *e = &s->entries[i];
        PyObject *key = py_dict_entry_key(s, e);
        if (key == NULL) continue;
        py_dict_set(dst, key, py_dict_entry_value(s, e));
    }
}
