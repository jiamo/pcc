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

#define PY_DICT_INITIAL_CAPACITY  8   /* must be power of 2 */

/* ---- Forward decls ---------------------------------------------------- */
static int  py_dict_rehash(PyDictObject *d, int64_t new_capacity);

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
    return 0;
}

PyObject *py_dict_new(void) {
    PyDictObject *d = (PyDictObject *)malloc(sizeof(PyDictObject));
    if (d == NULL) return NULL;
    d->h.refcount = 1;
    d->h.type_tag = PY_TYPE_DICT;
    d->h.flags    = 0;
    d->indices      = NULL;
    d->entries      = NULL;
    d->capacity     = 0;
    d->size         = 0;
    d->entries_used = 0;
    if (py_dict_alloc_tables(d, PY_DICT_INITIAL_CAPACITY) != 0) {
        free(d);
        return NULL;
    }
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
    int64_t mask = d->capacity - 1;
    uint64_t perturb = (uint64_t)hash;
    int64_t j = (int64_t)((uint64_t)hash & (uint64_t)mask);
    int64_t first_tombstone = -1;

    for (;;) {
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
            if (e->key != NULL && e->hash == hash &&
                (e->key == key || py_obj_eq(e->key, key))) {
                *out_slot = j;
                *out_entry_idx = ix;
                return;
            }
        }
        /* Advance probe. */
        perturb >>= 5;
        j = (int64_t)(((uint64_t)j * 5u + perturb + 1u) & (uint64_t)mask);
    }
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
    py_incref(key);
    py_incref(value);
    e->hash  = hash;
    e->key   = key;
    e->value = value;
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
    free(old_indices);

    for (int64_t i = 0; i < old_entries_used; i++) {
        DictEntry *e = &old_entries[i];
        if (e->key == NULL) continue;  /* skip dead entries */
        /* Insert without incref'ing again — we're moving refs over. We
         * can't reuse insert_fresh which would double-count. Inline the
         * move: find slot via lookup, copy entry, bump counters. */
        int64_t slot, ix;
        py_dict_lookup(d, e->hash, e->key, &slot, &ix);
        (void)ix;  /* guaranteed -1 during rehash */
        int64_t ei = d->entries_used++;
        DictEntry *ne = &d->entries[ei];
        ne->hash  = e->hash;
        ne->key   = e->key;
        ne->value = e->value;
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
    if (dict == NULL || key == NULL) return;
    PyDictObject *d = (PyDictObject *)dict;

    int64_t hash = py_obj_hash(key);
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix >= 0) {
        /* Update existing. Replace value; key stays (Python semantics:
         * dict[k] = v keeps the original key object). */
        DictEntry *e = &d->entries[ix];
        py_incref(value);
        py_decref(e->value);
        e->value = value;
        return;
    }
    /* Fresh insert. */
    py_dict_insert_fresh(d, hash, key, value);
    (void)py_dict_maybe_grow(d);
    /* If maybe_grow fails, we've already inserted; the table just gets
     * denser. A future op will try again. */
}

PyObject *py_dict_get(PyObject *dict, PyObject *key) {
    if (dict == NULL || key == NULL) return NULL;
    PyDictObject *d = (PyDictObject *)dict;

    int64_t hash = py_obj_hash(key);
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix < 0) return NULL;
    PyObject *v = d->entries[ix].value;
    py_incref(v);
    return v;
}

PyObject *py_dict_get_default(PyObject *dict, PyObject *key, PyObject *def) {
    PyObject *v = py_dict_get(dict, key);
    if (v != NULL) return v;
    py_incref(def);
    return def;
}

int py_dict_contains(PyObject *dict, PyObject *key) {
    if (dict == NULL || key == NULL) return 0;
    PyDictObject *d = (PyDictObject *)dict;
    int64_t hash = py_obj_hash(key);
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    return ix >= 0 ? 1 : 0;
}

int py_dict_del(PyObject *dict, PyObject *key) {
    if (dict == NULL || key == NULL) return -1;
    PyDictObject *d = (PyDictObject *)dict;
    int64_t hash = py_obj_hash(key);
    int64_t slot, ix;
    py_dict_lookup(d, hash, key, &slot, &ix);
    if (ix < 0) return -1;

    DictEntry *e = &d->entries[ix];
    py_decref(e->key);
    py_decref(e->value);
    e->key   = NULL;
    e->value = NULL;
    /* Leave e->hash intact; it's just a cached int and aids debugging. */
    d->indices[slot] = PY_DICT_TOMBSTONE;
    d->size--;
    return 0;
}

int64_t py_dict_len(PyObject *dict) {
    if (dict == NULL) return 0;
    return ((PyDictObject *)dict)->size;
}

PyObject *py_dict_keys(PyObject *dict) {
    if (dict == NULL) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        if (e->key == NULL) continue;
        py_list_append(out, e->key);
    }
    return out;
}

PyObject *py_dict_values(PyObject *dict) {
    if (dict == NULL) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        if (e->key == NULL) continue;
        py_list_append(out, e->value);
    }
    return out;
}

PyObject *py_dict_items(PyObject *dict) {
    if (dict == NULL) return NULL;
    PyDictObject *d = (PyDictObject *)dict;
    PyObject *out = py_list_new(d->size > 0 ? d->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        if (e->key == NULL) continue;
        /* Build a 2-tuple (key, value) for each live entry. */
        PyObject *pair = py_tuple_new(2);
        if (pair == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(pair, 0, e->key);
        py_tuple_set_item(pair, 1, e->value);
        py_list_append(out, pair);
        py_decref(pair);  /* list took its own ref */
    }
    return out;
}
