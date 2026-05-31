/* pcc/py_runtime/src/py_set.c
 *
 * Open-addressing hash set of PyObject* (unordered, unique).
 *
 * Layout (see py_internal.h):
 *
 *   SetEntry { int64_t hash; PyObject *key; }
 *
 *   PySetObject {
 *       header; size; capacity; fill; entries[];
 *   }
 *
 *   entries[j].key == NULL              -> never-used slot ("empty")
 *   entries[j].key == py_set_dummy      -> tombstone (deleted)
 *   otherwise                           -> live entry
 *
 *   size = live count
 *   fill = live + tombstones (drives rehash at 2/3 load factor)
 *
 * Probing matches py_dict.c (CPython-style perturbation):
 *   perturb = hash; j = hash & mask;
 *   next:   perturb >>= 5;  j = (j*5 + perturb + 1) & mask.
 *
 * Hash/equality: py_obj_hash + py_obj_eq from py_obj_ops.c.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

#define PY_SET_INITIAL_CAPACITY  8  /* must be power of 2 */

/* Tombstone sentinel moved to py_substrate.c so py_set.c can be
 * replaced by a pcc-Python port (py_set.py) without losing the
 * sentinel symbol. The const pointer value is accessed via
 * py_subs_set_dummy() from the Python port, and imported via
 * the extern declaration in py_internal.h for C callers. */

/* ---- Forward decls ----------------------------------------------------- */
static int py_set_rehash(PySetObject *s, int64_t new_capacity);
int64_t py_set_contains(PyObject *set, PyObject *item);

static PyObject *py_set_entry_key(PySetObject *s, SetEntry *e) {
    PyObject *raw = e->key;
    if (raw == NULL || raw == py_set_dummy) return raw;
    return pcc_gc_load_ptr((PyObject *)s, &e->key);
}

/* ---- Allocation -------------------------------------------------------- */

static int py_set_alloc_entries(PySetObject *s, int64_t capacity) {
    SetEntry *entries = (SetEntry *)malloc((size_t)capacity * sizeof(SetEntry));
    if (entries == NULL) return -1;
    for (int64_t i = 0; i < capacity; i++) {
        entries[i].hash = 0;
        entries[i].key  = NULL;
    }
    s->entries  = entries;
    s->capacity = capacity;
    s->size     = 0;
    s->fill     = 0;
    (void)pcc_gc_backend4_zpage_register_owner_payload_span(
        (PyObject *)s,
        s->entries,
        capacity * (int64_t)sizeof(SetEntry)
    );
    return 0;
}

PyObject *py_set_new(void) {
    PySetObject *s = (PySetObject *)pcc_gc_alloc(
        (int64_t)sizeof(PySetObject), PY_TYPE_SET, 0
    );
    if (s == NULL) return NULL;
    s->entries    = NULL;
    s->capacity   = 0;
    s->size       = 0;
    s->fill       = 0;
    if (py_set_alloc_entries(s, PY_SET_INITIAL_CAPACITY) != 0) {
        py_decref((PyObject *)s);
        return NULL;
    }
    py_gc_track((PyObject *)s);
    return (PyObject *)s;
}

/* ---- Probing ----------------------------------------------------------- */

/* Locate the slot for `key`. On return:
 *   *out_slot        = slot index the key lives at (if found) or a usable
 *                      insert target (first tombstone seen, else the
 *                      terminal empty slot).
 *   *out_found       = 1 if the key is live, 0 otherwise.
 */
static void py_set_lookup(PySetObject *s, int64_t hash, PyObject *key,
                          int64_t *out_slot, int *out_found) {
    int64_t mask = s->capacity - 1;
    uint64_t perturb = (uint64_t)hash;
    int64_t j = (int64_t)((uint64_t)hash & (uint64_t)mask);
    int64_t first_tombstone = -1;

    for (;;) {
        SetEntry *e = &s->entries[j];
        PyObject *entry_key = py_set_entry_key(s, e);
        if (entry_key == NULL) {
            *out_slot = (first_tombstone >= 0) ? first_tombstone : j;
            *out_found = 0;
            return;
        }
        if (entry_key == py_set_dummy) {
            if (first_tombstone < 0) first_tombstone = j;
        } else if (e->hash == hash && (entry_key == key || py_obj_eq(entry_key, key))) {
            *out_slot = j;
            *out_found = 1;
            return;
        }
        perturb >>= 5;
        j = (int64_t)(((uint64_t)j * 5u + perturb + 1u) & (uint64_t)mask);
    }
}

/* Rebuild the entries[] array at `new_capacity`. Moves refs (no
 * incref/decref) from old to new. */
static int py_set_rehash(PySetObject *s, int64_t new_capacity) {
    SetEntry *old_entries = s->entries;
    int64_t   old_capacity = s->capacity;

    if (py_set_alloc_entries(s, new_capacity) != 0) {
        /* Restore old state on failure. */
        s->entries  = old_entries;
        s->capacity = old_capacity;
        return -1;
    }

    for (int64_t i = 0; i < old_capacity; i++) {
        PyObject *k = py_set_entry_key(s, &old_entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        int64_t slot;
        int found;
        py_set_lookup(s, old_entries[i].hash, k, &slot, &found);
        /* found must be 0 during rehash — same keys never collide when
         * we're reinserting distinct lives from an old table. */
        (void)found;
        s->entries[slot].hash = old_entries[i].hash;
        s->entries[slot].key  = k;
        s->size++;
        s->fill++;
    }
    free(old_entries);
    return 0;
}

static int py_set_maybe_grow(PySetObject *s) {
    int64_t threshold = (s->capacity * 2) / 3;
    if (s->fill <= threshold) return 0;
    int64_t new_cap = s->capacity;
    if (s->size > threshold / 2) {
        new_cap = s->capacity * 2;
    }
    return py_set_rehash(s, new_cap);
}

/* ---- Public API -------------------------------------------------------- */

void py_set_add(PyObject *set, PyObject *item) {
    if (set == NULL || item == NULL) return;
    PySetObject *s = (PySetObject *)set;
    int64_t hash = py_obj_hash(item);
    int64_t slot;
    int found;
    py_set_lookup(s, hash, item, &slot, &found);
    if (found) return;  /* already present */

    SetEntry *e = &s->entries[slot];
    int was_tombstone = (e->key == py_set_dummy);
    e->hash = hash;
    e->key = NULL;
    pcc_gc_store_ptr(set, &e->key, item);
    s->size++;
    if (!was_tombstone) s->fill++;

    (void)py_set_maybe_grow(s);
}

void py_set_update(PyObject *dst, PyObject *src) {
    if (dst == NULL || src == NULL) return;
    if (PY_IS_TAGGED_INT(src)) return;
    if (py_header(src)->type_tag != PY_TYPE_SET) return;
    PySetObject *s = (PySetObject *)src;
    for (int64_t i = 0; i < s->capacity; i++) {
        PyObject *k = py_set_entry_key(s, &s->entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        py_set_add(dst, k);
    }
}

PyObject *py_set_intersection(PyObject *a, PyObject *b) {
    PyObject *out = py_set_new();
    if (out == NULL) return NULL;
    if (a == NULL || b == NULL) return out;
    if (PY_IS_TAGGED_INT(a) || PY_IS_TAGGED_INT(b)) return out;
    if (py_header(a)->type_tag != PY_TYPE_SET) return out;
    if (py_header(b)->type_tag != PY_TYPE_SET) return out;
    PySetObject *sa = (PySetObject *)a;
    for (int64_t i = 0; i < sa->capacity; i++) {
        PyObject *k = py_set_entry_key(sa, &sa->entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        if (py_set_contains(b, k)) py_set_add(out, k);
    }
    return out;
}

PyObject *py_set_difference(PyObject *a, PyObject *b) {
    PyObject *out = py_set_new();
    if (out == NULL) return NULL;
    if (a == NULL) return out;
    if (PY_IS_TAGGED_INT(a)) return out;
    if (py_header(a)->type_tag != PY_TYPE_SET) return out;
    PySetObject *sa = (PySetObject *)a;
    int b_is_set = (
        b != NULL
        && !PY_IS_TAGGED_INT(b)
        && py_header(b)->type_tag == PY_TYPE_SET
    );
    for (int64_t i = 0; i < sa->capacity; i++) {
        PyObject *k = py_set_entry_key(sa, &sa->entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        if (!b_is_set || !py_set_contains(b, k)) py_set_add(out, k);
    }
    return out;
}

/* a ^ b: elements in exactly one of a, b ((a - b) | (b - a)). */
PyObject *py_set_symmetric_difference(PyObject *a, PyObject *b) {
    PyObject *out = py_set_new();
    if (out == NULL) return NULL;
    int a_is_set = (
        a != NULL && !PY_IS_TAGGED_INT(a)
        && py_header(a)->type_tag == PY_TYPE_SET
    );
    int b_is_set = (
        b != NULL && !PY_IS_TAGGED_INT(b)
        && py_header(b)->type_tag == PY_TYPE_SET
    );
    if (a_is_set) {
        PySetObject *sa = (PySetObject *)a;
        for (int64_t i = 0; i < sa->capacity; i++) {
            PyObject *k = py_set_entry_key(sa, &sa->entries[i]);
            if (k == NULL || k == py_set_dummy) continue;
            if (!b_is_set || !py_set_contains(b, k)) py_set_add(out, k);
        }
    }
    if (b_is_set) {
        PySetObject *sb = (PySetObject *)b;
        for (int64_t i = 0; i < sb->capacity; i++) {
            PyObject *k = py_set_entry_key(sb, &sb->entries[i]);
            if (k == NULL || k == py_set_dummy) continue;
            if (!a_is_set || !py_set_contains(a, k)) py_set_add(out, k);
        }
    }
    return out;
}

int64_t py_set_issubset(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return 0;
    if (PY_IS_TAGGED_INT(a) || PY_IS_TAGGED_INT(b)) return 0;
    if (py_header(a)->type_tag != PY_TYPE_SET) return 0;
    if (py_header(b)->type_tag != PY_TYPE_SET) return 0;
    PySetObject *sa = (PySetObject *)a;
    PySetObject *sb = (PySetObject *)b;
    if (sa->size > sb->size) return 0;
    for (int64_t i = 0; i < sa->capacity; i++) {
        PyObject *k = py_set_entry_key(sa, &sa->entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        if (!py_set_contains(b, k)) return 0;
    }
    return 1;
}

int64_t py_set_issuperset(PyObject *a, PyObject *b) {
    return py_set_issubset(b, a);
}

PyObject *py_set_items(PyObject *set) {
    if (set == NULL) return NULL;
    if (PY_IS_TAGGED_INT(set)) return NULL;
    if (py_header(set)->type_tag != PY_TYPE_SET) return NULL;
    PySetObject *s = (PySetObject *)set;
    PyObject *out = py_list_new(s->size > 0 ? s->size : 4);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < s->capacity; i++) {
        PyObject *k = py_set_entry_key(s, &s->entries[i]);
        if (k == NULL || k == py_set_dummy) continue;
        py_list_append(out, k);
    }
    return out;
}

int64_t py_set_contains(PyObject *set, PyObject *item) {
    if (set == NULL || item == NULL) return 0;
    PySetObject *s = (PySetObject *)set;
    int64_t hash = py_obj_hash(item);
    int64_t slot;
    int found;
    py_set_lookup(s, hash, item, &slot, &found);
    return found;
}

int64_t py_set_remove(PyObject *set, PyObject *item) {
    if (set == NULL || item == NULL) return -1;
    PySetObject *s = (PySetObject *)set;
    int64_t hash = py_obj_hash(item);
    int64_t slot;
    int found;
    py_set_lookup(s, hash, item, &slot, &found);
    if (!found) return -1;
    SetEntry *e = &s->entries[slot];
    PyObject *k = py_set_entry_key(s, e);
    py_decref(k);
    e->key = py_set_dummy;   /* tombstone; fill unchanged */
    s->size--;
    return 0;
}

int64_t py_set_len(PyObject *set) {
    if (set == NULL) return 0;
    return ((PySetObject *)set)->size;
}
