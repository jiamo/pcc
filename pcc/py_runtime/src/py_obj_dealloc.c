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

extern void pcc_debug_bad_dict_slot(
    void *dict,
    int64_t index,
    int64_t offset,
    void *obj,
    int64_t tag
);

static void pcc_debug_check_dict_dealloc_slot(
    PyObject *dict,
    int64_t index,
    int64_t offset,
    PyObject *obj
) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return;
    PyObjectHeader *h = py_header(obj);
    if ((py_header_flags_load(h) & PY_FLAG_IMMORTAL) != 0) return;
    int32_t flags = py_header_flags_load(h);
    if (
        pcc_gc_backend() == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && (flags & PY_FLAG_GC_MINOR_ARENA) != 0
        && (flags & PY_FLAG_GC_OLD) != 0
        && pcc_refcount_load(&h->refcount) <= 0
    ) {
        return;
    }
    if (pcc_refcount_load(&h->refcount) <= 0) {
        pcc_debug_bad_dict_slot(dict, index, offset, obj, h->type_tag);
    }
}

static int pcc_dealloc_should_defer(int32_t type_tag) {
    if (pcc_capi_is_cext_type_tag((int64_t)type_tag) != 0) return 0;
    switch (type_tag) {
        case PY_TYPE_LIST:
        case PY_TYPE_TUPLE:
        case PY_TYPE_DICT:
        case PY_TYPE_SET:
        case PY_TYPE_INSTANCE:
        case PY_TYPE_EXC:
        case PY_TYPE_ITER:
        case PY_TYPE_GEN:
        case PY_TYPE_COROUTINE:
        case PY_TYPE_CONTINUATION:
        case PY_TYPE_TASK:
        case PY_TYPE_VIRTUAL_THREAD:
        case PY_TYPE_VTHREAD_CHANNEL:
        case PY_TYPE_PROPERTY:
        case PY_TYPE_CLASSMETHOD:
        case PY_TYPE_STATICMETHOD:
            return 1;
        default:
            return type_tag >= PY_TYPE_USER_CLASS_START;
    }
}

static void pcc_dealloc_dispatch(PyObject *o, int32_t type_tag) {
    switch (type_tag) {
        case PY_TYPE_INT:       py_dealloc_int(o);       break;
        case PY_TYPE_FLOAT:     py_dealloc_float(o);     break;
        case PY_TYPE_STR:       py_dealloc_str(o);       break;
        case PY_TYPE_LIST:      py_dealloc_list(o);      break;
        case PY_TYPE_TUPLE:     py_dealloc_tuple(o);     break;
        case PY_TYPE_DICT:      py_dealloc_dict(o);      break;
        case PY_TYPE_SET:       py_dealloc_set(o);       break;
        case PY_TYPE_FUNC:      py_dealloc_func(o);      break;
        case PY_TYPE_CLASS:     py_class_dealloc(o);     break;
        case PY_TYPE_INSTANCE:  py_instance_dealloc(o);  break;
        case PY_TYPE_EXC:       py_dealloc_exc(o);       break;
        case PY_TYPE_ITER:      py_dealloc_iter(o);      break;
        case PY_TYPE_GEN:       py_dealloc_gen(o);       break;
        case PY_TYPE_COROUTINE: py_dealloc_coroutine(o); break;
        case PY_TYPE_CONTINUATION: py_dealloc_continuation(o); break;
        case PY_TYPE_MEMORYVIEW: py_dealloc_memoryview(o); break;
        case PY_TYPE_WEAKREF:   py_dealloc_weakref(o);   break;
        case PY_TYPE_FILE:      py_dealloc_file(o);      break;
        case PY_TYPE_THREAD_LOCK: py_dealloc_thread_lock(o); break;
        case PY_TYPE_THREAD_RLOCK: py_dealloc_thread_rlock(o); break;
        case PY_TYPE_THREAD_EVENT: py_dealloc_thread_event(o); break;
        case PY_TYPE_THREAD_CONDITION: py_dealloc_thread_condition(o); break;
        case PY_TYPE_THREAD_SEMAPHORE: py_dealloc_thread_semaphore(o); break;
        case PY_TYPE_THREAD: py_dealloc_thread_thread(o); break;
        case PY_TYPE_TASK: py_dealloc_task(o); break;
        case PY_TYPE_VIRTUAL_THREAD: py_dealloc_virtual_thread(o); break;
        case PY_TYPE_VTHREAD_CHANNEL: py_dealloc_vthread_channel(o); break;
        case PY_TYPE_PROPERTY:
        case PY_TYPE_CLASSMETHOD:
        case PY_TYPE_STATICMETHOD: py_descriptor_dealloc(o); break;
        default:
            if (pcc_capi_dealloc_cext_object(o, (int64_t)type_tag) != 0) break;
            if (type_tag >= PY_TYPE_USER_CLASS_START) py_instance_dealloc(o);
            else py_dealloc_generic(o);
            break;
    }
}

typedef struct PccDeallocTrashNode {
    PyObject *obj;
    int32_t type_tag;
    struct PccDeallocTrashNode *next;
} PccDeallocTrashNode;

/* Deallocation cascades are mutator-local.  Sharing this depth/queue between
 * pthreads lets one thread drain another thread's nodes and can double-free a
 * container payload while explicit GC is parking the other mutators. */
static _Thread_local int pcc_dealloc_depth = 0;
static _Thread_local PccDeallocTrashNode *pcc_dealloc_trash_head = NULL;
static _Thread_local PccDeallocTrashNode *pcc_dealloc_trash_tail = NULL;

static int pcc_dealloc_trash_enqueue(PyObject *o, int32_t type_tag) {
    PccDeallocTrashNode *node = (PccDeallocTrashNode *)malloc(
        sizeof(PccDeallocTrashNode)
    );
    if (node == NULL) return 0;
    node->obj = o;
    node->type_tag = type_tag;
    node->next = NULL;
    if (pcc_dealloc_trash_tail != NULL) {
        pcc_dealloc_trash_tail->next = node;
    } else {
        pcc_dealloc_trash_head = node;
    }
    pcc_dealloc_trash_tail = node;
    return 1;
}

static void pcc_dealloc_trash_drain(void) {
    while (pcc_dealloc_trash_head != NULL) {
        PccDeallocTrashNode *node = pcc_dealloc_trash_head;
        pcc_dealloc_trash_head = node->next;
        if (pcc_dealloc_trash_head == NULL) {
            pcc_dealloc_trash_tail = NULL;
        }
        PyObject *obj = node->obj;
        int32_t type_tag = node->type_tag;
        free(node);
        pcc_dealloc_dispatch(obj, type_tag);
    }
}

void pcc_dealloc_with_trash(PyObject *o, int64_t type_tag) {
    int32_t tag = (int32_t)type_tag;
    /* INTENTIONAL DIVERGENCE from the pcc-Python production port: the
     * port defers zpage-resident objects too (excluding them recursed
     * the whole cascade and overflowed the stack on backend 4) and
     * closes the recycle UAF by deferring backend-4 page recycles while
     * pcc_dealloc_cascade_active(), completed by
     * pcc_gc_backend4_sweep_deferred_recycles after the drain. This C
     * oracle has no zpage allocator, so PY_FLAG_GC_ZPAGE_ALLOC is never
     * set in a pure-C link and the exclusion below is unreachable; it is
     * kept fail-safe for hybrid links that mix the C dealloc with a
     * zpage-capable allocator without the deferred-recycle machinery. */
    if (
        pcc_dealloc_depth > 0
        && pcc_dealloc_should_defer(tag)
        && (py_header(o)->flags & PY_FLAG_GC_ZPAGE_ALLOC) == 0
        && pcc_dealloc_trash_enqueue(o, tag)
    ) {
        return;
    }
    pcc_dealloc_depth++;
    pcc_dealloc_dispatch(o, tag);
    if (pcc_dealloc_depth == 1) {
        pcc_dealloc_trash_drain();
    }
    pcc_dealloc_depth--;
}

void py_dealloc_int(PyObject *o) {
    pcc_gc_free_object_memory(o);
}

void py_dealloc_float(PyObject *o) {
    pcc_gc_free_object_memory(o);
}

void py_dealloc_str(PyObject *o) {
    /* PyStrObject uses a flexible-array-member tail — one free() is
     * sufficient. */
    pcc_gc_free_object_memory(o);
}

void py_dealloc_list(PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    int64_t length = l->length;
    PyObject **items = l->items;
    if (pcc_gc_backend() == PCC_GC_KIND_COLORED_RELOCATING) {
        l->length = 0;
        l->capacity = 0;
        l->items = NULL;
    }
    for (int64_t i = 0; items != NULL && i < length; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &items[i]);
        if (item != NULL) py_decref(item);
    }
    if (pcc_gc_backend() != PCC_GC_KIND_COLORED_RELOCATING) {
        l->items = NULL;
        l->length = 0;
        l->capacity = 0;
    }
    free(items);
    pcc_gc_free_object_memory(o);
}

void py_dealloc_tuple(PyObject *o) {
    PyTupleObject *t = (PyTupleObject *)o;
    for (int64_t i = 0; i < t->len; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &t->items[i]);
        if (item != NULL) py_decref(item);
    }
    pcc_gc_free_object_memory(o);
}

void py_dealloc_dict(PyObject *o) {
    PyDictObject *d = (PyDictObject *)o;
    if (d->entries != NULL) {
        for (int64_t i = 0; i < d->entries_used; i++) {
            DictEntry *e = &d->entries[i];
            if (e->key != NULL) {
                PyObject *key = pcc_gc_load_ptr(o, &e->key);
                PyObject *value = pcc_gc_load_ptr(o, &e->value);
                pcc_debug_check_dict_dealloc_slot(o, i, 8, key);
                pcc_debug_check_dict_dealloc_slot(o, i, 16, value);
                if (key != NULL) py_decref(key);
                if (value != NULL) py_decref(value);
            }
        }
        free(d->entries);
    }
    free(d->indices);
    pcc_gc_free_object_memory(o);
}

void py_dealloc_set(PyObject *o) {
    PySetObject *s = (PySetObject *)o;
    if (s->entries != NULL) {
        for (int64_t i = 0; i < s->capacity; i++) {
            PyObject *k = s->entries[i].key;
            if (k != NULL && k != py_set_dummy) {
                k = pcc_gc_load_ptr(o, &s->entries[i].key);
                if (k != NULL && k != py_set_dummy) py_decref(k);
            }
        }
        free(s->entries);
    }
    pcc_gc_free_object_memory(o);
}

void py_dealloc_file(PyObject *o) {
    py_file_close(o);
    pcc_gc_free_object_memory(o);
}

void py_dealloc_generic(PyObject *o) {
    /* Fallback for types whose dealloc isn't specialized yet. */
    pcc_gc_free_object_memory(o);
}
