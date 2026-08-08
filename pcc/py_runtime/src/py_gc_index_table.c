#include "py_internal.h"
#include <stdint.h>
#include <stdlib.h>

// Lightweight pointer->node map used by py_obj_gc.py to avoid O(n)
// node lookups under heavy tracking workloads.

typedef struct PccGcIndexSlot {
    void *key;
    void *node;
    uint8_t state;
} PccGcIndexSlot;

#define PCC_GC_INDEX_SLOT_EMPTY 0
#define PCC_GC_INDEX_SLOT_FULL 1
#define PCC_GC_INDEX_SLOT_DELETED 2
#define PCC_GC_INDEX_DEFAULT_INIT_CAP 256

static PccGcIndexSlot *py_gc_index_slots = NULL;
static int64_t py_gc_index_cap = 0;
static int64_t py_gc_index_count = 0;
static int64_t py_gc_index_used = 0;

static uint64_t py_gc_index_hash_ptr(const void *p) {
    /* Heap/object pointers are at least 8-byte aligned and usually arrive
     * from contiguous arenas or zpages. The previous shift-xor mix left
     * consecutive addresses in consecutive slots, which is the worst case
     * for linear probing (primary clustering: bootstrap-scale bursts of
     * bump-allocated objects formed giant contiguous runs and find_slot
     * dominated GC3/GC4 profiles). One Fibonacci multiply breaks the
     * consecutive-key pattern; keep in bit-exact sync with
     * pcc_gc_index_py_hash_ptr in py/freestanding_gc_index_table.py. */
    uint64_t v = (uint64_t)(uintptr_t)p >> 3;
    v *= 0x9E3779B97F4A7C15ull;
    return v ^ (v >> 32);
}

static int64_t py_gc_index_next_pow2(int64_t n) {
    if (n < 8) return 8;
    int64_t p = 1;
    while (p < n) p <<= 1;
    return p;
}

static int64_t pcc_gc_index_rehash_capacity(
    int64_t cap,
    int64_t count,
    int64_t min_cap
) {
    int64_t desired = (count + 1) * 4;
    if (desired < min_cap) desired = min_cap;
    int64_t compact_cap = py_gc_index_next_pow2(desired);
    if (count + 1 > cap / 2) {
        int64_t grown_cap = cap * 2;
        return grown_cap > compact_cap ? grown_cap : compact_cap;
    }
    return compact_cap < cap ? compact_cap : cap;
}

static int64_t pcc_gc_index_find_slot(
    PccGcIndexSlot *slots,
    int64_t cap,
    void *key,
    int *found
) {
    /* Backward-shift deletion keeps probe chains gap-free, so slot state
     * is only EMPTY or FULL: churn workloads no longer accumulate
     * tombstones that force same-capacity rehashes (calloc+memset
     * dominated the GC4 longrun profile) or lengthen probe runs. Keep in
     * lockstep with pcc_gc_index_py_find_slot in
     * py/freestanding_gc_index_table.py. */
    uint64_t h = py_gc_index_hash_ptr(key);
    int64_t mask = cap - 1;
    int64_t idx = (int64_t)(h & (uint64_t)mask);
    for (;;) {
        uint8_t state = slots[idx].state;
        if (state == PCC_GC_INDEX_SLOT_EMPTY) {
            *found = 0;
            return idx;
        }
        if (slots[idx].key == key) {
            *found = 1;
            return idx;
        }
        idx = (idx + 1) & mask;
    }
}

static int pcc_gc_index_rehash_slots(
    PccGcIndexSlot **slots_ptr,
    int64_t *cap_ptr,
    int64_t *used_ptr,
    int64_t new_cap
) {
    new_cap = py_gc_index_next_pow2(new_cap);
    PccGcIndexSlot *new_slots = (PccGcIndexSlot *)calloc(
        (size_t)new_cap,
        sizeof(PccGcIndexSlot)
    );
    if (new_slots == NULL) return -1;

    PccGcIndexSlot *old_slots = *slots_ptr;
    int64_t old_cap = *cap_ptr;
    int64_t new_used = 0;
    if (old_slots != NULL) {
        for (int64_t i = 0; i < old_cap; i++) {
            if (old_slots[i].state != PCC_GC_INDEX_SLOT_FULL) continue;
            int found = 0;
            int64_t idx = pcc_gc_index_find_slot(
                new_slots,
                new_cap,
                old_slots[i].key,
                &found
            );
            new_slots[idx] = old_slots[i];
            new_slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
            new_used++;
        }
        free(old_slots);
    }

    *slots_ptr = new_slots;
    *cap_ptr = new_cap;
    *used_ptr = new_used;
    return 0;
}

static void *pcc_gc_index_remove_slot(
    PccGcIndexSlot *slots,
    int64_t cap,
    int64_t *count,
    int64_t *used,
    void *key
) {
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(slots, cap, key, &found);
    if (!found) return NULL;
    void *node = slots[idx].node;
    /* Backward-shift deletion: close the probe-chain gap instead of
     * writing a tombstone. An entry at ``probe`` (home slot ``home``) may
     * fill the hole iff its home lies cyclically outside (hole, probe],
     * i.e. (probe - home) mod cap >= (probe - hole) mod cap. Loop cost is
     * the probe-run length past the hole (short at <=50% load with the
     * Fibonacci hash). No engine caller iterates slots across removes, so
     * moving entries here is safe for every index instance. Keep in
     * lockstep with pcc_gc_index_py_remove. */
    int64_t mask = cap - 1;
    int64_t hole = idx;
    int64_t probe = idx;
    for (;;) {
        probe = (probe + 1) & mask;
        if (slots[probe].state != PCC_GC_INDEX_SLOT_FULL) break;
        int64_t home = (int64_t)(
            py_gc_index_hash_ptr(slots[probe].key) & (uint64_t)mask
        );
        if (((probe - home) & mask) >= ((probe - hole) & mask)) {
            slots[hole] = slots[probe];
            hole = probe;
        }
    }
    slots[hole].key = NULL;
    slots[hole].node = NULL;
    slots[hole].state = PCC_GC_INDEX_SLOT_EMPTY;
    (*count)--;
    (*used)--;
    return node;
}

static int py_gc_index_rehash(int64_t new_cap) {
    return pcc_gc_index_rehash_slots(
        &py_gc_index_slots,
        &py_gc_index_cap,
        &py_gc_index_used,
        new_cap
    );
}

static int py_gc_index_init(void) {
    if (py_gc_index_slots != NULL) return 0;
    return py_gc_index_rehash(PCC_GC_INDEX_DEFAULT_INIT_CAP);
}

PyGcNode *py_gc_index_find(PyObject *obj) {
    if (py_gc_index_slots == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        py_gc_index_slots,
        py_gc_index_cap,
        obj,
        &found
    );
    return found ? (PyGcNode *)py_gc_index_slots[idx].node : NULL;
}

int64_t py_gc_index_insert(PyObject *obj, PyGcNode *node) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return -1;
    if (py_gc_index_slots == NULL) {
        if (py_gc_index_init() != 0) return -1;
    }

    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        py_gc_index_slots,
        py_gc_index_cap,
        obj,
        &found
    );
    if (found) return 0;

    if (py_gc_index_used + 1 > py_gc_index_cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            py_gc_index_cap,
            py_gc_index_count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (py_gc_index_rehash(new_cap) != 0) return -1;
        idx = pcc_gc_index_find_slot(
            py_gc_index_slots,
            py_gc_index_cap,
            obj,
            &found
        );
    }
    if (py_gc_index_slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        py_gc_index_used++;
    }
    py_gc_index_slots[idx].key = obj;
    py_gc_index_slots[idx].node = node;
    py_gc_index_slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    py_gc_index_count++;
    return 1;
}

PyGcNode *py_gc_index_remove(PyObject *obj) {
    if (py_gc_index_slots == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    return (PyGcNode *)pcc_gc_index_remove_slot(
        py_gc_index_slots,
        py_gc_index_cap,
        &py_gc_index_count,
        &py_gc_index_used,
        obj
    );
}

typedef struct {
    PccGcIndexSlot *slots;
    int64_t cap;
    int64_t count;
    int64_t used;
} PccGcPtrIndex;

static int pcc_gc_ptr_index_rehash(PccGcPtrIndex *index, int64_t new_cap) {
    return pcc_gc_index_rehash_slots(
        &index->slots,
        &index->cap,
        &index->used,
        new_cap
    );
}

static int pcc_gc_ptr_index_init(PccGcPtrIndex *index) {
    if (index->slots != NULL) return 0;
    return pcc_gc_ptr_index_rehash(index, PCC_GC_INDEX_DEFAULT_INIT_CAP);
}

static void *pcc_gc_ptr_index_find(PccGcPtrIndex *index, PyObject *obj) {
    if (index->slots == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) {
        return NULL;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        obj,
        &found
    );
    return found ? index->slots[idx].node : NULL;
}

static void *pcc_gc_ptr_index_find_raw(PccGcPtrIndex *index, void *key) {
    if (index->slots == NULL || key == NULL) return NULL;
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        key,
        &found
    );
    return found ? index->slots[idx].node : NULL;
}

/* Kept as a stable runtime hook for the thread-exit path. The old chained
 * ptr-index implementation had a _Thread_local recycled-entry pool; the
 * open-addressed table has no per-thread heap nodes to drain. */
void pcc_gc_ptr_index_tls_pool_drain(void) {
}

static int64_t pcc_gc_ptr_index_insert(
    PccGcPtrIndex *index,
    PyObject *obj,
    void *node
) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return -1;
    if (index->slots == NULL) {
        if (pcc_gc_ptr_index_init(index) != 0) return -1;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        obj,
        &found
    );
    if (found) return 0;
    if (index->used + 1 > index->cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            index->cap,
            index->count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (pcc_gc_ptr_index_rehash(index, new_cap) != 0) return -1;
        idx = pcc_gc_index_find_slot(
            index->slots,
            index->cap,
            obj,
            &found
        );
    }

    if (index->slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        index->used++;
    }
    index->slots[idx].key = obj;
    index->slots[idx].node = node;
    index->slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    index->count++;
    return 1;
}

static int64_t pcc_gc_ptr_index_upsert(
    PccGcPtrIndex *index,
    PyObject *obj,
    void *node
) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || node == NULL) return -1;
    if (index->slots == NULL) {
        if (pcc_gc_ptr_index_init(index) != 0) return -1;
    }

    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        obj,
        &found
    );
    if (found) {
        index->slots[idx].node = node;
        return 0;
    }

    if (index->used + 1 > index->cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            index->cap,
            index->count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (pcc_gc_ptr_index_rehash(index, new_cap) != 0) return -1;
        idx = pcc_gc_index_find_slot(
            index->slots,
            index->cap,
            obj,
            &found
        );
    }

    if (index->slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        index->used++;
    }
    index->slots[idx].key = obj;
    index->slots[idx].node = node;
    index->slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    index->count++;
    return 1;
}

static int64_t pcc_gc_ptr_index_insert_raw(
    PccGcPtrIndex *index,
    void *key,
    void *node
) {
    if (key == NULL) return -1;
    if (index->slots == NULL) {
        if (pcc_gc_ptr_index_init(index) != 0) return -1;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        key,
        &found
    );
    if (found) return 0;
    if (index->used + 1 > index->cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            index->cap,
            index->count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (pcc_gc_ptr_index_rehash(index, new_cap) != 0) return -1;
        idx = pcc_gc_index_find_slot(
            index->slots,
            index->cap,
            key,
            &found
        );
    }
    if (index->slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        index->used++;
    }
    index->slots[idx].key = key;
    index->slots[idx].node = node;
    index->slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    index->count++;
    return 1;
}

static int64_t pcc_gc_ptr_index_upsert_raw(
    PccGcPtrIndex *index,
    void *key,
    void *node
) {
    if (key == NULL || node == NULL) return -1;
    if (index->slots == NULL) {
        if (pcc_gc_ptr_index_init(index) != 0) return -1;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        key,
        &found
    );
    if (found) {
        index->slots[idx].node = node;
        return 0;
    }
    if (index->used + 1 > index->cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            index->cap,
            index->count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (pcc_gc_ptr_index_rehash(index, new_cap) != 0) return -1;
        idx = pcc_gc_index_find_slot(
            index->slots,
            index->cap,
            key,
            &found
        );
    }
    if (index->slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        index->used++;
    }
    index->slots[idx].key = key;
    index->slots[idx].node = node;
    index->slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    index->count++;
    return 1;
}

static void *pcc_gc_ptr_index_replace_raw(
    PccGcPtrIndex *index,
    void *key,
    void *node
) {
    if (key == NULL || node == NULL) return node;
    if (index->slots == NULL) {
        if (pcc_gc_ptr_index_init(index) != 0) return node;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        index->slots,
        index->cap,
        key,
        &found
    );
    if (found) {
        void *old = index->slots[idx].node;
        index->slots[idx].node = node;
        return old;
    }
    if (index->used + 1 > index->cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            index->cap,
            index->count,
            PCC_GC_INDEX_DEFAULT_INIT_CAP
        );
        if (pcc_gc_ptr_index_rehash(index, new_cap) != 0) return node;
        idx = pcc_gc_index_find_slot(
            index->slots,
            index->cap,
            key,
            &found
        );
    }
    if (index->slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        index->used++;
    }
    index->slots[idx].key = key;
    index->slots[idx].node = node;
    index->slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    index->count++;
    return NULL;
}

static void *pcc_gc_ptr_index_remove(PccGcPtrIndex *index, PyObject *obj) {
    if (index->slots == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) {
        return NULL;
    }
    return pcc_gc_index_remove_slot(
        index->slots,
        index->cap,
        &index->count,
        &index->used,
        obj
    );
}

static void *pcc_gc_ptr_index_remove_raw(PccGcPtrIndex *index, void *key) {
    if (index->slots == NULL || key == NULL) return NULL;
    return pcc_gc_index_remove_slot(
        index->slots,
        index->cap,
        &index->count,
        &index->used,
        key
    );
}

static void pcc_gc_ptr_index_clear(PccGcPtrIndex *index) {
    free(index->slots);
    index->slots = NULL;
    index->cap = 0;
    index->count = 0;
    index->used = 0;
}

static PccGcPtrIndex pcc_gc_forwarding_index = {
    NULL, 0, 0, 0
};
static PccGcPtrIndex pcc_gc_forwarding_target_index = {
    NULL, 0, 0, 0
};
static PccGcPtrIndex pcc_gc_identity_index = {
    NULL, 0, 0, 0
};
static PccGcPtrIndex pcc_gc_frame_index = {
    NULL, 0, 0, 0
};
static PccGcPtrIndex pcc_gc_zpage_owner_index = {
    NULL, 0, 0, 0
};
static PccGcPtrIndex pcc_gc_zpage_page_index = {
    NULL, 0, 0, 0
};

/* Exact provenance for managed objects that intentionally do not have a
 * PccGcObjectNode: backend-0 objects, backend-3/4 graph leaves, and the small
 * number of runtime-owned objects constructed outside pcc_gc_alloc.  This is
 * a key-only set rather than another pointer->node table so the provenance
 * contract does not add a 24-byte slot to every refcount-mode object.
 *
 * Access is serialized by the GC graph lock in py_gc_backend.c.  Lookups only
 * compare pointer VALUES; they never dereference a candidate pointer. */
static void **pcc_gc_managed_pointer_slots = NULL;
static int64_t pcc_gc_managed_pointer_cap = 0;
static int64_t pcc_gc_managed_pointer_count = 0;

static int64_t pcc_gc_managed_pointer_find_slot(
    void **slots,
    int64_t cap,
    void *key,
    int *found
) {
    int64_t mask = cap - 1;
    int64_t index = (int64_t)(
        py_gc_index_hash_ptr(key) & (uint64_t)mask
    );
    for (;;) {
        void *candidate = slots[index];
        if (candidate == NULL) {
            *found = 0;
            return index;
        }
        if (candidate == key) {
            *found = 1;
            return index;
        }
        index = (index + 1) & mask;
    }
}

static int pcc_gc_managed_pointer_rehash(int64_t requested_cap) {
    int64_t new_cap = py_gc_index_next_pow2(requested_cap);
    void **new_slots = (void **)calloc((size_t)new_cap, sizeof(void *));
    if (new_slots == NULL) return -1;
    for (int64_t i = 0; i < pcc_gc_managed_pointer_cap; i++) {
        void *key = pcc_gc_managed_pointer_slots[i];
        if (key == NULL) continue;
        int found = 0;
        int64_t index = pcc_gc_managed_pointer_find_slot(
            new_slots, new_cap, key, &found
        );
        new_slots[index] = key;
    }
    free(pcc_gc_managed_pointer_slots);
    pcc_gc_managed_pointer_slots = new_slots;
    pcc_gc_managed_pointer_cap = new_cap;
    return 0;
}

int64_t pcc_gc_managed_pointer_index_contains(PyObject *obj) {
    if (
        pcc_gc_managed_pointer_slots == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return 0;
    }
    int found = 0;
    (void)pcc_gc_managed_pointer_find_slot(
        pcc_gc_managed_pointer_slots,
        pcc_gc_managed_pointer_cap,
        obj,
        &found
    );
    return found ? 1 : 0;
}

int64_t pcc_gc_managed_pointer_index_insert(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return -1;
    if (pcc_gc_managed_pointer_slots == NULL) {
        if (pcc_gc_managed_pointer_rehash(256) != 0) return -1;
    }
    int found = 0;
    int64_t index = pcc_gc_managed_pointer_find_slot(
        pcc_gc_managed_pointer_slots,
        pcc_gc_managed_pointer_cap,
        obj,
        &found
    );
    if (found) return 0;
    if (pcc_gc_managed_pointer_count + 1 > pcc_gc_managed_pointer_cap / 2) {
        if (
            pcc_gc_managed_pointer_rehash(
                pcc_gc_managed_pointer_cap * 2
            ) != 0
        ) {
            return -1;
        }
        index = pcc_gc_managed_pointer_find_slot(
            pcc_gc_managed_pointer_slots,
            pcc_gc_managed_pointer_cap,
            obj,
            &found
        );
    }
    pcc_gc_managed_pointer_slots[index] = obj;
    pcc_gc_managed_pointer_count++;
    return 1;
}

int64_t pcc_gc_managed_pointer_index_remove(PyObject *obj) {
    if (
        pcc_gc_managed_pointer_slots == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return 0;
    }
    int found = 0;
    int64_t hole = pcc_gc_managed_pointer_find_slot(
        pcc_gc_managed_pointer_slots,
        pcc_gc_managed_pointer_cap,
        obj,
        &found
    );
    if (!found) return 0;
    int64_t mask = pcc_gc_managed_pointer_cap - 1;
    int64_t probe = hole;
    for (;;) {
        probe = (probe + 1) & mask;
        void *key = pcc_gc_managed_pointer_slots[probe];
        if (key == NULL) break;
        int64_t home = (int64_t)(
            py_gc_index_hash_ptr(key) & (uint64_t)mask
        );
        if (((probe - home) & mask) >= ((probe - hole) & mask)) {
            pcc_gc_managed_pointer_slots[hole] = key;
            hole = probe;
        }
    }
    pcc_gc_managed_pointer_slots[hole] = NULL;
    pcc_gc_managed_pointer_count--;
    return 1;
}

void *pcc_gc_forwarding_index_find(PyObject *obj) {
    return pcc_gc_ptr_index_find(&pcc_gc_forwarding_index, obj);
}

int64_t pcc_gc_forwarding_index_insert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_insert(&pcc_gc_forwarding_index, obj, node);
}

void *pcc_gc_forwarding_index_remove(PyObject *obj) {
    return pcc_gc_ptr_index_remove(&pcc_gc_forwarding_index, obj);
}

void pcc_gc_forwarding_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_forwarding_index);
}

void *pcc_gc_forwarding_target_index_find(PyObject *obj) {
    return pcc_gc_ptr_index_find(&pcc_gc_forwarding_target_index, obj);
}

int64_t pcc_gc_forwarding_target_index_insert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_insert(&pcc_gc_forwarding_target_index, obj, node);
}

int64_t pcc_gc_forwarding_target_index_upsert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_upsert(&pcc_gc_forwarding_target_index, obj, node);
}

void *pcc_gc_forwarding_target_index_remove(PyObject *obj) {
    return pcc_gc_ptr_index_remove(&pcc_gc_forwarding_target_index, obj);
}

void pcc_gc_forwarding_target_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_forwarding_target_index);
}

void *pcc_gc_identity_index_find(PyObject *obj) {
    return pcc_gc_ptr_index_find(&pcc_gc_identity_index, obj);
}

int64_t pcc_gc_identity_index_insert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_insert(&pcc_gc_identity_index, obj, node);
}

void *pcc_gc_identity_index_remove(PyObject *obj) {
    return pcc_gc_ptr_index_remove(&pcc_gc_identity_index, obj);
}

void pcc_gc_identity_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_identity_index);
}

void *pcc_gc_frame_index_find(void *slots) {
    return pcc_gc_ptr_index_find_raw(&pcc_gc_frame_index, slots);
}

int64_t pcc_gc_frame_index_insert(void *slots, void *node) {
    return pcc_gc_ptr_index_insert_raw(&pcc_gc_frame_index, slots, node);
}

void *pcc_gc_frame_index_replace(void *slots, void *node) {
    return pcc_gc_ptr_index_replace_raw(&pcc_gc_frame_index, slots, node);
}

void *pcc_gc_frame_index_remove(void *slots) {
    return pcc_gc_ptr_index_remove_raw(&pcc_gc_frame_index, slots);
}

void pcc_gc_frame_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_frame_index);
}

void *pcc_gc_zpage_owner_index_find(PyObject *obj) {
    return pcc_gc_ptr_index_find(&pcc_gc_zpage_owner_index, obj);
}

int64_t pcc_gc_zpage_owner_index_insert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_insert(&pcc_gc_zpage_owner_index, obj, node);
}

int64_t pcc_gc_zpage_owner_index_upsert(PyObject *obj, void *node) {
    return pcc_gc_ptr_index_upsert(&pcc_gc_zpage_owner_index, obj, node);
}

void *pcc_gc_zpage_owner_index_remove(PyObject *obj) {
    return pcc_gc_ptr_index_remove(&pcc_gc_zpage_owner_index, obj);
}

void pcc_gc_zpage_owner_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_zpage_owner_index);
}

void *pcc_gc_zpage_page_index_find(void *page) {
    return pcc_gc_ptr_index_find_raw(&pcc_gc_zpage_page_index, page);
}

int64_t pcc_gc_zpage_page_index_insert(void *page, void *node) {
    return pcc_gc_ptr_index_insert_raw(&pcc_gc_zpage_page_index, page, node);
}

int64_t pcc_gc_zpage_page_index_upsert(void *page, void *node) {
    return pcc_gc_ptr_index_upsert_raw(&pcc_gc_zpage_page_index, page, node);
}

void *pcc_gc_zpage_page_index_remove(void *page) {
    return pcc_gc_ptr_index_remove_raw(&pcc_gc_zpage_page_index, page);
}

void pcc_gc_zpage_page_index_clear(void) {
    pcc_gc_ptr_index_clear(&pcc_gc_zpage_page_index);
}

static PccGcIndexSlot *pcc_gc_object_index_slots = NULL;
static int64_t pcc_gc_object_index_cap = 0;
static int64_t pcc_gc_object_index_count = 0;
static int64_t pcc_gc_object_index_used = 0;

static int pcc_gc_object_index_rehash(int64_t new_cap) {
    return pcc_gc_index_rehash_slots(
        &pcc_gc_object_index_slots,
        &pcc_gc_object_index_cap,
        &pcc_gc_object_index_used,
        new_cap
    );
}

static int pcc_gc_object_index_init(void) {
    if (pcc_gc_object_index_slots != NULL) return 0;
    return pcc_gc_object_index_rehash(16384);
}

void *pcc_gc_object_index_find(PyObject *obj) {
    if (
        pcc_gc_object_index_slots == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return NULL;
    }
    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        pcc_gc_object_index_slots,
        pcc_gc_object_index_cap,
        obj,
        &found
    );
    return found ? pcc_gc_object_index_slots[idx].node : NULL;
}

int64_t pcc_gc_object_index_insert(PyObject *obj, void *node) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || node == NULL) return -1;
    if (pcc_gc_object_index_slots == NULL) {
        if (pcc_gc_object_index_init() != 0) return -1;
    }

    int found = 0;
    int64_t idx = pcc_gc_index_find_slot(
        pcc_gc_object_index_slots,
        pcc_gc_object_index_cap,
        obj,
        &found
    );
    if (found) return 0;

    if (pcc_gc_object_index_used + 1 > pcc_gc_object_index_cap / 2) {
        int64_t new_cap = pcc_gc_index_rehash_capacity(
            pcc_gc_object_index_cap,
            pcc_gc_object_index_count,
            16384
        );
        if (
            pcc_gc_object_index_rehash(new_cap) != 0
        ) {
            return -1;
        }
        idx = pcc_gc_index_find_slot(
            pcc_gc_object_index_slots,
            pcc_gc_object_index_cap,
            obj,
            &found
        );
    }

    if (pcc_gc_object_index_slots[idx].state == PCC_GC_INDEX_SLOT_EMPTY) {
        pcc_gc_object_index_used++;
    }
    pcc_gc_object_index_slots[idx].key = obj;
    pcc_gc_object_index_slots[idx].node = node;
    pcc_gc_object_index_slots[idx].state = PCC_GC_INDEX_SLOT_FULL;
    pcc_gc_object_index_count++;
    return 1;
}

void *pcc_gc_object_index_remove(PyObject *obj) {
    if (
        pcc_gc_object_index_slots == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return NULL;
    }
    return pcc_gc_index_remove_slot(
        pcc_gc_object_index_slots,
        pcc_gc_object_index_cap,
        &pcc_gc_object_index_count,
        &pcc_gc_object_index_used,
        obj
    );
}

void pcc_gc_object_index_clear(void) {
    free(pcc_gc_object_index_slots);
    pcc_gc_object_index_slots = NULL;
    pcc_gc_object_index_cap = 0;
    pcc_gc_object_index_count = 0;
    pcc_gc_object_index_used = 0;
}
