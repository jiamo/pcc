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

static int pcc_dealloc_should_defer(int32_t type_tag) {
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
        default:
            return type_tag >= PY_TYPE_USER;
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
        default:
            if (type_tag >= PY_TYPE_USER) py_instance_dealloc(o);
            else py_dealloc_generic(o);
            break;
    }
}

typedef struct PccDeallocTrashNode {
    PyObject *obj;
    int32_t type_tag;
    struct PccDeallocTrashNode *next;
} PccDeallocTrashNode;

static int pcc_dealloc_depth = 0;
static PccDeallocTrashNode *pcc_dealloc_trash_head = NULL;
static PccDeallocTrashNode *pcc_dealloc_trash_tail = NULL;

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
    if (
        pcc_dealloc_depth > 0
        && pcc_dealloc_should_defer(tag)
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
