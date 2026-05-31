#include "py_internal.h"
#include <stdint.h>
#include <stdlib.h>

// Lightweight pointer->node map used by py_obj_gc.py to avoid O(n)
// node lookups under heavy tracking workloads.

typedef struct PyGcIndexEntry {
    PyObject *key;
    PyGcNode *node;
    struct PyGcIndexEntry *next;
} PyGcIndexEntry;

static PyGcIndexEntry **py_gc_index_buckets = NULL;
static int64_t py_gc_index_cap = 0;
static int64_t py_gc_index_count = 0;

static uint64_t py_gc_index_hash(PyObject *o) {
    uint64_t v = (uint64_t)(uintptr_t)o;
    v ^= v >> 33;
    v *= 0xff51afd7ed558ccdULL;
    v ^= v >> 33;
    v *= 0xc4ceb9fe1a85ec53ULL;
    v ^= v >> 33;
    return v;
}

static int64_t py_gc_index_next_pow2(int64_t n) {
    if (n < 8) return 8;
    int64_t p = 1;
    while (p < n) p <<= 1;
    return p;
}

static int py_gc_index_rehash(int64_t new_cap) {
    new_cap = py_gc_index_next_pow2(new_cap);
    PyGcIndexEntry **new_buckets = (PyGcIndexEntry **)calloc((size_t)new_cap, sizeof(PyGcIndexEntry *));
    if (new_buckets == NULL) return -1;

    if (py_gc_index_buckets != NULL) {
        for (int64_t i = 0; i < py_gc_index_cap; i++) {
            PyGcIndexEntry *cur = py_gc_index_buckets[i];
            while (cur != NULL) {
                PyGcIndexEntry *next = cur->next;
                uint64_t h = py_gc_index_hash(cur->key);
                int64_t idx = (int64_t)(h & (uint64_t)(new_cap - 1));
                cur->next = new_buckets[idx];
                new_buckets[idx] = cur;
                cur = next;
            }
        }
        free(py_gc_index_buckets);
    }

    py_gc_index_buckets = new_buckets;
    py_gc_index_cap = new_cap;
    return 0;
}

static int py_gc_index_init(void) {
    if (py_gc_index_buckets != NULL) return 0;
    return py_gc_index_rehash(256);
}

PyGcNode *py_gc_index_find(PyObject *obj) {
    if (py_gc_index_buckets == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(py_gc_index_cap - 1));
    PyGcIndexEntry *cur = py_gc_index_buckets[idx];
    while (cur != NULL) {
        if (cur->key == obj) return cur->node;
        cur = cur->next;
    }
    return NULL;
}

int64_t py_gc_index_insert(PyObject *obj, PyGcNode *node) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return -1;
    if (py_gc_index_buckets == NULL) {
        if (py_gc_index_init() != 0) return -1;
    }

    if (py_gc_index_find(obj) != NULL) return 0;

    if (py_gc_index_count + 1 > (py_gc_index_cap * 3) / 4) {
        if (py_gc_index_rehash(py_gc_index_cap * 2) != 0) return -1;
    }

    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(py_gc_index_cap - 1));
    PyGcIndexEntry *entry = (PyGcIndexEntry *)malloc(sizeof(PyGcIndexEntry));
    if (entry == NULL) return -1;
    entry->key = obj;
    entry->node = node;
    entry->next = py_gc_index_buckets[idx];
    py_gc_index_buckets[idx] = entry;
    py_gc_index_count++;
    return 1;
}

PyGcNode *py_gc_index_remove(PyObject *obj) {
    if (py_gc_index_buckets == NULL || obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(py_gc_index_cap - 1));

    PyGcIndexEntry **slot = &py_gc_index_buckets[idx];
    while (*slot != NULL) {
        PyGcIndexEntry *cur = *slot;
        if (cur->key == obj) {
            PyGcNode *node = cur->node;
            *slot = cur->next;
            free(cur);
            py_gc_index_count--;
            return node;
        }
        slot = &(*slot)->next;
    }
    return NULL;
}

typedef struct PccGcObjectIndexEntry {
    PyObject *key;
    void *node;
    struct PccGcObjectIndexEntry *next;
} PccGcObjectIndexEntry;

static PccGcObjectIndexEntry **pcc_gc_object_index_buckets = NULL;
static int64_t pcc_gc_object_index_cap = 0;
static int64_t pcc_gc_object_index_count = 0;

static int pcc_gc_object_index_rehash(int64_t new_cap) {
    new_cap = py_gc_index_next_pow2(new_cap);
    PccGcObjectIndexEntry **new_buckets =
        (PccGcObjectIndexEntry **)calloc(
            (size_t)new_cap, sizeof(PccGcObjectIndexEntry *));
    if (new_buckets == NULL) return -1;

    if (pcc_gc_object_index_buckets != NULL) {
        for (int64_t i = 0; i < pcc_gc_object_index_cap; i++) {
            PccGcObjectIndexEntry *cur = pcc_gc_object_index_buckets[i];
            while (cur != NULL) {
                PccGcObjectIndexEntry *next = cur->next;
                uint64_t h = py_gc_index_hash(cur->key);
                int64_t idx = (int64_t)(h & (uint64_t)(new_cap - 1));
                cur->next = new_buckets[idx];
                new_buckets[idx] = cur;
                cur = next;
            }
        }
        free(pcc_gc_object_index_buckets);
    }

    pcc_gc_object_index_buckets = new_buckets;
    pcc_gc_object_index_cap = new_cap;
    return 0;
}

static int pcc_gc_object_index_init(void) {
    if (pcc_gc_object_index_buckets != NULL) return 0;
    return pcc_gc_object_index_rehash(256);
}

void *pcc_gc_object_index_find(PyObject *obj) {
    if (
        pcc_gc_object_index_buckets == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return NULL;
    }
    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(pcc_gc_object_index_cap - 1));
    PccGcObjectIndexEntry *cur = pcc_gc_object_index_buckets[idx];
    while (cur != NULL) {
        if (cur->key == obj) return cur->node;
        cur = cur->next;
    }
    return NULL;
}

int64_t pcc_gc_object_index_insert(PyObject *obj, void *node) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || node == NULL) return -1;
    if (pcc_gc_object_index_buckets == NULL) {
        if (pcc_gc_object_index_init() != 0) return -1;
    }

    if (pcc_gc_object_index_find(obj) != NULL) return 0;

    if (pcc_gc_object_index_count + 1 > (pcc_gc_object_index_cap * 3) / 4) {
        if (
            pcc_gc_object_index_rehash(pcc_gc_object_index_cap * 2) != 0
        ) {
            return -1;
        }
    }

    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(pcc_gc_object_index_cap - 1));
    PccGcObjectIndexEntry *entry =
        (PccGcObjectIndexEntry *)malloc(sizeof(PccGcObjectIndexEntry));
    if (entry == NULL) return -1;
    entry->key = obj;
    entry->node = node;
    entry->next = pcc_gc_object_index_buckets[idx];
    pcc_gc_object_index_buckets[idx] = entry;
    pcc_gc_object_index_count++;
    return 1;
}

void *pcc_gc_object_index_remove(PyObject *obj) {
    if (
        pcc_gc_object_index_buckets == NULL
        || obj == NULL
        || PY_IS_TAGGED_INT(obj)
    ) {
        return NULL;
    }
    uint64_t h = py_gc_index_hash(obj);
    int64_t idx = (int64_t)(h & (uint64_t)(pcc_gc_object_index_cap - 1));

    PccGcObjectIndexEntry **slot = &pcc_gc_object_index_buckets[idx];
    while (*slot != NULL) {
        PccGcObjectIndexEntry *cur = *slot;
        if (cur->key == obj) {
            void *node = cur->node;
            *slot = cur->next;
            free(cur);
            pcc_gc_object_index_count--;
            return node;
        }
        slot = &(*slot)->next;
    }
    return NULL;
}

void pcc_gc_object_index_clear(void) {
    if (pcc_gc_object_index_buckets == NULL) return;
    for (int64_t i = 0; i < pcc_gc_object_index_cap; i++) {
        PccGcObjectIndexEntry *cur = pcc_gc_object_index_buckets[i];
        while (cur != NULL) {
            PccGcObjectIndexEntry *next = cur->next;
            free(cur);
            cur = next;
        }
    }
    free(pcc_gc_object_index_buckets);
    pcc_gc_object_index_buckets = NULL;
    pcc_gc_object_index_cap = 0;
    pcc_gc_object_index_count = 0;
}
