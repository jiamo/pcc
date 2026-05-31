/* pcc/py_runtime/src/py_obj_gc.c
 *
 * CPython-style refcount backend plus a stop-the-world cycle collector.
 * Atomic objects are still reclaimed by immediate refcounting. Containers
 * that can form cycles are linked into a side table by py_gc_track().
 * gc.collect() runs the classic update_refs/subtract_refs/reachable scan
 * over that tracked set and then clears unreachable cycles.
 *
 * GC metadata intentionally lives in a side table for this backend. That
 * keeps the public object header stable while the pluggable GC interface is
 * still being brought up for Lua/Go/OCaml/ZGC-style backends.
 */
#include "py_internal.h"
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

static int32_t py_gc_enabled = 1;
static int32_t py_gc_threshold0 = 700;
static int32_t py_gc_threshold1 = 10;
static int32_t py_gc_threshold2 = 10;
static int64_t py_gc_freeze_count = 0;

typedef struct PyGcNodeSlot {
    PyObject *obj;
    PyGcNode *node;
    struct PyGcNodeSlot *next;
} PyGcNodeSlot;

typedef struct {
    PyObjectHeader h;
    const char *name;
    PyNativeFuncEntry entry;
    PyObject *captures;
    PyObject *args;
    PyObject *result;
    int32_t closed;
    int32_t done;
} PyGcCoroutineObject;

static PyGcNode *py_gc_head = NULL;
static int64_t py_gc_tracked_count = 0;
static int32_t py_gc_collecting = 0;
static PyGcNodeSlot **py_gc_node_index = NULL;
static int64_t py_gc_node_index_cap = 0;
static int64_t py_gc_node_index_count = 0;
static unsigned char py_gc_table_lock_word = 0;

static void py_gc_table_lock(void) {
    while (__atomic_test_and_set(&py_gc_table_lock_word, __ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint();
    }
}

static void py_gc_table_unlock(void) {
    __atomic_clear(&py_gc_table_lock_word, __ATOMIC_RELEASE);
}

static uint64_t py_gc_node_hash(PyObject *o) {
    uintptr_t p = (uintptr_t)o;
    p ^= p >> 33;
    p *= 0xff51afd7ed558ccdULL;
    p ^= p >> 33;
    p *= 0xc4ceb9fe1a85ec53ULL;
    p ^= p >> 33;
    return (uint64_t)p;
}

static PyGcNode *py_gc_find_node(PyObject *o) {
    if (py_gc_node_index == NULL || py_gc_node_index_cap <= 0 || o == NULL || PY_IS_TAGGED_INT(o)) {
        return NULL;
    }
    size_t cap = (size_t)py_gc_node_index_cap;
    size_t idx = py_gc_node_hash(o) & (cap - 1);
    for (PyGcNodeSlot *slot = py_gc_node_index[idx]; slot != NULL; slot = slot->next) {
        if (slot->obj == o) return slot->node;
    }
    return NULL;
}

static int py_gc_node_index_rehash(int64_t new_cap) {
    if (new_cap < 1024) new_cap = 1024;
    if ((new_cap & (new_cap - 1)) != 0) {
        int64_t pow2 = 1;
        while (pow2 < new_cap) pow2 <<= 1;
        new_cap = pow2;
    }
    PyGcNodeSlot **new_index = (PyGcNodeSlot **)calloc((size_t)new_cap, sizeof(PyGcNodeSlot *));
    if (!new_index) return -1;

    if (py_gc_node_index != NULL) {
        for (int64_t i = 0; i < py_gc_node_index_cap; i++) {
            PyGcNodeSlot *slot = py_gc_node_index[i];
            while (slot != NULL) {
                PyGcNodeSlot *next = slot->next;
                size_t idx = py_gc_node_hash(slot->obj) & (size_t)(new_cap - 1);
                slot->next = new_index[idx];
                new_index[idx] = slot;
                slot = next;
            }
        }
        free(py_gc_node_index);
    }

    py_gc_node_index = new_index;
    py_gc_node_index_cap = new_cap;
    return 0;
}

static int py_gc_node_index_init(void) {
    if (py_gc_node_index != NULL) return 0;
    return py_gc_node_index_rehash(1024);
}

static int py_gc_node_index_insert(PyObject *o, PyGcNode *node) {
    if (o == NULL || node == NULL) return -1;
    if (py_gc_node_index == NULL) {
        if (py_gc_node_index_init() != 0) return -1;
    }
    if (py_gc_find_node(o) != NULL) return 0;
    if (py_gc_node_index_count + 1 > py_gc_node_index_cap * 4 / 3) {
        if (py_gc_node_index_rehash(py_gc_node_index_cap << 1) != 0) return -1;
    }
    size_t idx = py_gc_node_hash(o) & (size_t)(py_gc_node_index_cap - 1);
    PyGcNodeSlot *slot = (PyGcNodeSlot *)malloc(sizeof(PyGcNodeSlot));
    if (slot == NULL) return -1;
    slot->obj = o;
    slot->node = node;
    slot->next = py_gc_node_index[idx];
    py_gc_node_index[idx] = slot;
    py_gc_node_index_count++;
    return 1;
}

static PyGcNode *py_gc_node_index_remove(PyObject *o) {
    if (py_gc_node_index == NULL || py_gc_node_index_cap <= 0 || o == NULL || PY_IS_TAGGED_INT(o)) {
        return NULL;
    }
    size_t idx = py_gc_node_hash(o) & (size_t)(py_gc_node_index_cap - 1);
    PyGcNodeSlot **slot = &py_gc_node_index[idx];
    while (*slot != NULL) {
        PyGcNodeSlot *cur = *slot;
        if (cur->obj == o) {
            PyGcNode *node = cur->node;
            *slot = cur->next;
            free(cur);
            py_gc_node_index_count--;
            return node;
        }
        slot = &(*slot)->next;
    }
    return NULL;
}

static void py_gc_unlink_node(PyGcNode *n) {
    if (n == NULL) return;
    if (n->prev != NULL) n->prev->next = n->next;
    else py_gc_head = n->next;
    if (n->next != NULL) n->next->prev = n->prev;
    n->prev = NULL;
    n->next = NULL;
    py_gc_tracked_count--;
}

static int py_gc_is_unreachable(PyObject *o) {
    PyGcNode *n = py_gc_find_node(o);
    return n != NULL && n->reachable == 0;
}

static void py_gc_visit_referents(
    PyObject *o,
    void (*visit)(PyObject *child, void *ctx),
    void *ctx
) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)o;
        for (int64_t i = 0; i < l->length; i++) visit(l->items[i], ctx);
    } else if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)o;
        for (int64_t i = 0; i < t->len; i++) visit(t->items[i], ctx);
    } else if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)o;
        if (d->entries != NULL) {
            for (int64_t i = 0; i < d->entries_used; i++) {
                DictEntry *e = &d->entries[i];
                if (e->key != NULL) {
                    visit(e->key, ctx);
                    visit(e->value, ctx);
                }
            }
        }
    } else if (tag == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)o;
        if (s->entries != NULL) {
            for (int64_t i = 0; i < s->capacity; i++) {
                PyObject *k = s->entries[i].key;
                if (k != NULL && k != py_set_dummy) visit(k, ctx);
            }
        }
    } else if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        visit(f->captures, ctx);
        visit(f->self_obj, ctx);
    } else if (tag == PY_TYPE_ITER) {
        PyIterObject *it = (PyIterObject *)o;
        visit(it->seq, ctx);
    } else if (tag == PY_TYPE_GEN) {
        PyGenObject *g = (PyGenObject *)o;
        visit(g->frame, ctx);
        visit(g->send_value, ctx);
    } else if (tag == PY_TYPE_COROUTINE) {
        PyGcCoroutineObject *c = (PyGcCoroutineObject *)o;
        visit(c->captures, ctx);
        visit(c->args, ctx);
        visit(c->result, ctx);
    } else if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *c = (PyContinuationObject *)o;
        PyContinuationStackChunk *chunk = c->stack_chunk;
        if (chunk != NULL && chunk->slots != NULL) {
            for (int64_t i = 0; i < chunk->slot_count; i++) {
                visit(chunk->slots[i], ctx);
            }
        }
    } else if (tag == PY_TYPE_TASK) {
        PyTaskObject *t = (PyTaskObject *)o;
        visit(t->coro, ctx);
        visit(t->result, ctx);
        visit(t->waiter, ctx);
    } else if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *t = (PyVirtualThreadObject *)o;
        visit(t->continuation, ctx);
        visit(t->result, ctx);
    } else if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        visit(e->message, ctx);
        visit(e->cause, ctx);
        visit(e->context, ctx);
    } else if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = inst->cls;
        if (cls != NULL) {
            int32_t n_fields = cls->n_fields;
            if (n_fields < 0) n_fields = 0;
            for (int32_t i = 0; i < n_fields; i++) visit(inst->fields[i], ctx);
            if ((cls->h.flags & 2) == 0) visit(inst->fields[n_fields], ctx);
        }
    }
}

static void py_gc_subtract_child(PyObject *child, void *ctx) {
    (void)ctx;
    PyGcNode *n = py_gc_find_node(child);
    if (n != NULL) n->gc_refs--;
}

static void py_gc_append_referent(PyObject *child, void *ctx) {
    PyObject *out = (PyObject *)ctx;
    if (out == NULL || child == NULL) return;
    py_list_append(out, child);
}

PyObject *py_gc_get_objects(void) {
    int64_t tracked_count = py_gc_tracked_count;
    PyGcNode *head = py_gc_head;
    PyObject *out = py_list_new(tracked_count);
    if (out == NULL) return NULL;
    for (PyGcNode *n = head; n != NULL; n = n->next) {
        if (n->obj != NULL) py_list_append(out, n->obj);
    }
    return out;
}

PyObject *py_gc_get_referents(PyObject *o) {
    PyObject *out = py_list_new(0);
    if (out == NULL) return NULL;
    py_gc_visit_referents(o, py_gc_append_referent, out);
    return out;
}

typedef struct {
    PyObject *target;
    int found;
} PyGcReferrerSearch;

static void py_gc_find_referrer_child(PyObject *child, void *ctx) {
    PyGcReferrerSearch *search = (PyGcReferrerSearch *)ctx;
    if (search != NULL && child == search->target) search->found = 1;
}

PyObject *py_gc_get_referrers(PyObject *target) {
    PyGcNode *head = py_gc_head;
    PyObject *out = py_list_new(0);
    if (out == NULL) return NULL;
    PyGcReferrerSearch search;
    search.target = target;
    for (PyGcNode *n = head; n != NULL; n = n->next) {
        if (n->obj == NULL) continue;
        search.found = 0;
        py_gc_visit_referents(n->obj, py_gc_find_referrer_child, &search);
        if (search.found) py_list_append(out, n->obj);
    }
    return out;
}

static void py_gc_mark_reachable(PyObject *o);

static void py_gc_mark_child(PyObject *child, void *ctx) {
    (void)ctx;
    py_gc_mark_reachable(child);
}

static void py_gc_mark_runtime_root(PyObject *root, void *ctx) {
    (void)ctx;
    py_gc_mark_reachable(root);
}

static void py_gc_mark_reachable(PyObject *o) {
    PyGcNode *n = py_gc_find_node(o);
    if (n == NULL || n->reachable != 0) return;
    n->reachable = 1;
    py_gc_visit_referents(o, py_gc_mark_child, NULL);
}

static void py_gc_recompute_reachability(void) {
    for (PyGcNode *n = py_gc_head; n != NULL; n = n->next) {
        n->gc_refs = py_header(n->obj)->refcount;
        n->reachable = 0;
    }
    for (PyGcNode *n = py_gc_head; n != NULL; n = n->next) {
        py_gc_visit_referents(n->obj, py_gc_subtract_child, NULL);
    }
    for (PyGcNode *n = py_gc_head; n != NULL; n = n->next) {
        if (n->gc_refs > 0) py_gc_mark_reachable(n->obj);
    }
    for (PyGcNode *n = py_gc_head; n != NULL; n = n->next) {
        if (
            (py_header_flags_load(py_header(n->obj)) & PY_FLAG_GC_PINNED) != 0
        ) {
            py_gc_mark_reachable(n->obj);
        }
    }
    (void)pcc_gc_trace_continuation_roots();
    pcc_gc_visit_runtime_roots(py_gc_mark_runtime_root, NULL);
}

static int py_gc_maybe_finalize_unreachable(PyGcNode **unreachable,
                                            int64_t n_unreachable) {
    int finalized = 0;
    for (int64_t i = 0; i < n_unreachable; i++) {
        PyObject *obj = unreachable[i]->obj;
        if (obj == NULL || PY_IS_TAGGED_INT(obj)) continue;
        PyObjectHeader *h = py_header(obj);
        int32_t tag = h->type_tag;
        if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) continue;
        int32_t flags_before = h->flags;
        py_user_del_dispatch(obj);
        if ((flags_before & PY_FLAG_FINALIZED) == 0 &&
            (py_header(obj)->flags & PY_FLAG_FINALIZED) != 0) {
            finalized = 1;
        }
    }
    return finalized;
}

static void py_gc_clear_slot(PyObject **slot) {
    if (slot == NULL) return;
    PyObject *child = *slot;
    *slot = NULL;
    if (child == NULL) return;
    if (py_gc_is_unreachable(child)) return;
    py_decref(child);
}

static void py_gc_clear_referents(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_header(o)->type_tag;
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)o;
        for (int64_t i = 0; i < l->length; i++) py_gc_clear_slot(&l->items[i]);
        l->length = 0;
    } else if (tag == PY_TYPE_TUPLE) {
        PyTupleObject *t = (PyTupleObject *)o;
        for (int64_t i = 0; i < t->len; i++) py_gc_clear_slot(&t->items[i]);
        t->len = 0;
    } else if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)o;
        if (d->entries != NULL) {
            for (int64_t i = 0; i < d->entries_used; i++) {
                DictEntry *e = &d->entries[i];
                if (e->key != NULL) {
                    py_gc_clear_slot(&e->key);
                    py_gc_clear_slot(&e->value);
                    e->hash = 0;
                }
            }
        }
        if (d->indices != NULL) {
            for (int64_t i = 0; i < d->capacity; i++) d->indices[i] = PY_DICT_EMPTY;
        }
        d->size = 0;
        d->entries_used = 0;
    } else if (tag == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)o;
        if (s->entries != NULL) {
            for (int64_t i = 0; i < s->capacity; i++) {
                PyObject *k = s->entries[i].key;
                if (k != NULL && k != py_set_dummy) {
                    s->entries[i].key = NULL;
                    if (!py_gc_is_unreachable(k)) py_decref(k);
                } else {
                    s->entries[i].key = NULL;
                }
                s->entries[i].hash = 0;
            }
        }
        s->size = 0;
        s->fill = 0;
    } else if (tag == PY_TYPE_FUNC) {
        PyFuncObject *f = (PyFuncObject *)o;
        py_gc_clear_slot(&f->captures);
        py_gc_clear_slot(&f->self_obj);
    } else if (tag == PY_TYPE_ITER) {
        PyIterObject *it = (PyIterObject *)o;
        py_gc_clear_slot(&it->seq);
    } else if (tag == PY_TYPE_GEN) {
        PyGenObject *g = (PyGenObject *)o;
        py_gc_clear_slot(&g->frame);
        py_gc_clear_slot(&g->send_value);
    } else if (tag == PY_TYPE_COROUTINE) {
        PyGcCoroutineObject *c = (PyGcCoroutineObject *)o;
        py_gc_clear_slot(&c->captures);
        py_gc_clear_slot(&c->args);
        py_gc_clear_slot(&c->result);
    } else if (tag == PY_TYPE_CONTINUATION) {
        PyContinuationObject *c = (PyContinuationObject *)o;
        PyContinuationStackChunk *chunk = c->stack_chunk;
        if (chunk != NULL && chunk->slots != NULL) {
            for (int64_t i = 0; i < chunk->slot_count; i++) {
                py_gc_clear_slot(&chunk->slots[i]);
            }
        }
    } else if (tag == PY_TYPE_TASK) {
        PyTaskObject *t = (PyTaskObject *)o;
        py_gc_clear_slot(&t->coro);
        py_gc_clear_slot(&t->result);
        py_gc_clear_slot(&t->waiter);
    } else if (tag == PY_TYPE_VIRTUAL_THREAD) {
        PyVirtualThreadObject *t = (PyVirtualThreadObject *)o;
        py_gc_clear_slot(&t->continuation);
        py_gc_clear_slot(&t->result);
    } else if (tag == PY_TYPE_EXC) {
        PyExceptionObject *e = (PyExceptionObject *)o;
        py_gc_clear_slot(&e->message);
        py_gc_clear_slot(&e->cause);
        py_gc_clear_slot(&e->context);
    } else if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
        PyInstanceObject *inst = (PyInstanceObject *)o;
        PyClassObject *cls = inst->cls;
        if (cls != NULL) {
            int32_t n_fields = cls->n_fields;
            if (n_fields < 0) n_fields = 0;
            for (int32_t i = 0; i < n_fields; i++) {
                py_gc_clear_slot(&inst->fields[i]);
            }
            if ((cls->h.flags & 2) == 0) py_gc_clear_slot(&inst->fields[n_fields]);
        }
    }
}

static void py_gc_dealloc_unreachable(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    PyObjectHeader *h = py_header(o);
    switch (h->type_tag) {
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
        case PY_TYPE_TASK:      py_dealloc_task(o);      break;
        case PY_TYPE_VIRTUAL_THREAD: py_dealloc_virtual_thread(o); break;
        case PY_TYPE_MEMORYVIEW: py_dealloc_memoryview(o); break;
        case PY_TYPE_WEAKREF:   py_dealloc_weakref(o);   break;
        default:
            if (h->type_tag >= PY_TYPE_USER) py_instance_dealloc(o);
            else py_dealloc_generic(o);
            break;
    }
}

void py_gc_init(void) {
    py_gc_enabled = 1;
}

int64_t py_gc_collect(void) {
    int64_t collected = 0;
    PyGcNode **unreachable = NULL;

    if (py_gc_collecting != 0) return 0;

    /* Backend #0 is the CPython-shaped refcount+cycle collector.  In
     * threaded builds it must scan a stable tracked-object set, so the
     * update_refs/subtract_refs/mark/dealloc cycle runs under the shared
     * substrate STW gate.  In the default PCC_WITH_THREADS=0 build this is
     * a no-op and preserves the existing single-threaded behavior. */
    int64_t stw = pcc_stop_the_world();
    while (stw != 0) {
        pcc_thread_safepoint();
        stw = pcc_stop_the_world();
    }
    py_gc_table_lock();
    py_gc_collecting = 1;

    int64_t tracked = py_gc_tracked_count;
    if (tracked <= 0) {
        goto done;
    }
    unreachable = (PyGcNode **)calloc(
        (size_t)tracked, sizeof(PyGcNode *));
    if (unreachable == NULL) {
        goto done;
    }

    py_gc_recompute_reachability();

    int64_t n_unreachable = 0;
    for (PyGcNode *n = py_gc_head; n != NULL; n = n->next) {
        /* An object whose raw refcount is already 0 is owned by an in-flight
         * py_decref (a thread parked at a safepoint between rc->0 and
         * py_gc_untrack still has it tracked). The refcount path will free it;
         * the cycle collector must not, or it double-frees the block under
         * threaded explicit gc.collect(). Genuine cycle garbage always has
         * refcount > 0 (its references are internal to the cycle), so this
         * never suppresses real cycle collection. Single-threaded builds never
         * have a tracked rc==0 object at collection time, so behavior is
         * unchanged there. See
         * docs/investigations/pcc1-threaded-explicit-gc-backend0-double-free-highscale.md */
        if (n->reachable == 0 && pcc_refcount_load(&py_header(n->obj)->refcount) > 0) {
            unreachable[n_unreachable++] = n;
        }
    }

    if (py_gc_maybe_finalize_unreachable(unreachable, n_unreachable) != 0) {
        py_gc_recompute_reachability();
    }
    for (int64_t i = 0; i < n_unreachable; i++) {
        PyGcNode *n = unreachable[i];
        if (n->reachable != 0) continue;
        py_weakref_invalidate(n->obj);
        py_gc_clear_referents(n->obj);
    }
    for (int64_t i = 0; i < n_unreachable; i++) {
        PyGcNode *n = unreachable[i];
        if (n->reachable != 0) continue;
        PyObject *obj = n->obj;
        PyGcNode *index_n = py_gc_node_index_remove(obj);
        (void)index_n;
        py_gc_unlink_node(n);
        py_header_flags_and(py_header(obj), ~PY_FLAG_GC_TRACKED);
        py_header(obj)->refcount = 0;
        free(n);
        pcc_gc_note_object_freeing(obj);
        py_gc_dealloc_unreachable(obj);
        collected++;
    }

done:
    if (unreachable != NULL) free(unreachable);
    py_gc_collecting = 0;
    py_gc_table_unlock();
    (void)pcc_resume_world();
    return collected;
}

void py_gc_track(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    if (
        pcc_gc_backend() == PCC_GC_KIND_COLORED_RELOCATING
        && pcc_threads_enabled()
    ) {
        return;
    }
    py_gc_table_lock();
    PyObjectHeader *h = py_header(o);
    if ((py_header_flags_load(h) & PY_FLAG_GC_TRACKED) != 0) {
        py_header_flags_or(py_header(o), PY_FLAG_GC_TRACKED);
        py_gc_table_unlock();
        return;
    }
    PyGcNode *n = (PyGcNode *)calloc(1, sizeof(PyGcNode));
    if (n == NULL) {
        py_gc_table_unlock();
        return;
    }
    int status = py_gc_node_index_insert(o, n);
    if (status == 0) {
        py_header_flags_or(h, PY_FLAG_GC_TRACKED);
        free(n);
        py_gc_table_unlock();
        return;
    }
    if (status != 1) {
        free(n);
        py_gc_table_unlock();
        return;
    }
    n->obj = o;
    n->next = py_gc_head;
    if (py_gc_head != NULL) py_gc_head->prev = n;
    py_gc_head = n;
    py_gc_tracked_count++;
    py_header_flags_or(h, PY_FLAG_GC_TRACKED);
    py_gc_table_unlock();
}

void py_gc_untrack(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    if (
        pcc_gc_backend() == PCC_GC_KIND_COLORED_RELOCATING
        && pcc_threads_enabled()
    ) {
        return;
    }
    py_gc_table_lock();
    PyObjectHeader *h = py_header(o);
    if ((py_header_flags_load(h) & PY_FLAG_GC_TRACKED) == 0) {
        py_gc_table_unlock();
        return;
    }
    PyGcNode *n = py_gc_node_index_remove(o);
    if (n != NULL) {
        py_gc_unlink_node(n);
        free(n);
    }
    py_header_flags_and(h, ~PY_FLAG_GC_TRACKED);
    py_gc_table_unlock();
}

void py_gc_enable(void) {
    py_gc_enabled = 1;
}

void py_gc_disable(void) {
    py_gc_enabled = 0;
}

int64_t py_gc_is_enabled(void) {
    return py_gc_enabled != 0 ? 1 : 0;
}

int64_t py_gc_is_tracked(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    return (py_header_flags_load(py_header(o)) & PY_FLAG_GC_TRACKED) != 0 ? 1 : 0;
}

int64_t py_gc_get_count(int32_t generation) {
    if (generation == 0) return py_gc_tracked_count;
    return 0;
}

int64_t py_gc_get_threshold(int32_t generation) {
    if (generation == 0) return py_gc_threshold0;
    if (generation == 1) return py_gc_threshold1;
    if (generation == 2) return py_gc_threshold2;
    return 0;
}

void py_gc_set_threshold(int32_t gen0, int32_t gen1, int32_t gen2) {
    if (gen0 >= 0) py_gc_threshold0 = gen0;
    if (gen1 >= 0) py_gc_threshold1 = gen1;
    if (gen2 >= 0) py_gc_threshold2 = gen2;
}

void py_gc_freeze(void) {
    py_gc_freeze_count = py_gc_tracked_count > 0 ? py_gc_tracked_count : 1;
}

void py_gc_unfreeze(void) {
    py_gc_freeze_count = 0;
}

int64_t py_gc_get_freeze_count(void) {
    return py_gc_freeze_count;
}
