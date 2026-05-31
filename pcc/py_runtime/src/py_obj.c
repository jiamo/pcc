/* pcc/py_runtime/src/py_obj.c
 *
 * PyObject reference counting + dealloc dispatch.
 *
 * Phase 4c.11 split:
 *   - Immortal singletons (py_None/py_True/py_False) live in
 *     py_substrate.c so they remain exported when this module is
 *     replaced by the pcc-Python port.
 *   - Type-specific deallocators live in py_obj_dealloc.c so the
 *     dispatch here can be independently ported while the dealloc
 *     details (flexible-array-member free, child ref drop, etc.)
 *     stay C.
 */

#include "py_internal.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

extern void pcc_debug_note_alloc_size(void *ptr, int64_t size);
void pcc_gc_pin(PyObject *o);

static int pcc_obj_debug_runtime_enabled_cache = -1;

static int pcc_obj_debug_runtime_enabled(void) {
    int cached = __atomic_load_n(
        &pcc_obj_debug_runtime_enabled_cache, __ATOMIC_ACQUIRE
    );
    if (cached < 0) {
        int value = getenv("PCC_DEBUG_RUNTIME") != NULL ? 1 : 0;
        int expected = -1;
        (void)__atomic_compare_exchange_n(
            &pcc_obj_debug_runtime_enabled_cache,
            &expected,
            value,
            0,
            __ATOMIC_ACQ_REL,
            __ATOMIC_ACQUIRE
        );
        cached = __atomic_load_n(
            &pcc_obj_debug_runtime_enabled_cache, __ATOMIC_ACQUIRE
        );
    }
    return cached;
}

static PyObject *pcc_gc_callbacks = NULL;
static int32_t pcc_gc_callbacks_firing = 0;

static PyObject *pcc_gc_ensure_callbacks(void) {
    if (pcc_gc_callbacks == NULL) {
        pcc_gc_callbacks = py_list_new(0);
        if (pcc_gc_callbacks != NULL) {
            pcc_gc_pin(pcc_gc_callbacks);
        }
    }
    return pcc_gc_callbacks;
}

PyObject *py_gc_callbacks_list(void) {
    PyObject *callbacks = pcc_gc_ensure_callbacks();
    if (callbacks != NULL) py_incref(callbacks);
    return callbacks;
}

void py_gc_callbacks_append(PyObject *callback) {
    PyObject *callbacks = pcc_gc_ensure_callbacks();
    if (callbacks == NULL) return;
    py_list_append(callbacks, callback);
}

static int pcc_gc_callback_eq(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;
    if (PY_IS_TAGGED_INT(a) || PY_IS_TAGGED_INT(b)) return py_obj_eq(a, b);
    a = pcc_gc_note_relocation_read(a);
    b = pcc_gc_note_relocation_read(b);
    if (a == b) return 1;
    if (
        py_header(a)->type_tag == PY_TYPE_FUNC
        && py_header(b)->type_tag == PY_TYPE_FUNC
    ) {
        PyFuncObject *fa = (PyFuncObject *)a;
        PyFuncObject *fb = (PyFuncObject *)b;
        PyObject *a_captures = pcc_gc_load_ptr(a, &fa->captures);
        PyObject *b_captures = pcc_gc_load_ptr(b, &fb->captures);
        return fa->entry == fb->entry && py_obj_eq(a_captures, b_captures);
    }
    return py_obj_eq(a, b);
}

void py_gc_callbacks_remove(PyObject *callback) {
    PyObject *callbacks = pcc_gc_ensure_callbacks();
    if (callbacks == NULL) return;
    PyListObject *lst = (PyListObject *)callbacks;
    for (int64_t i = 0; i < lst->length; i++) {
        PyObject *existing = pcc_gc_load_ptr(callbacks, &lst->items[i]);
        if (pcc_gc_callback_eq(existing, callback)) {
            PyObject *old = existing;
            if (i < lst->length - 1) {
                memmove(
                    &lst->items[i],
                    &lst->items[i + 1],
                    (size_t)(lst->length - i - 1) * sizeof(PyObject *)
                );
            }
            lst->length--;
            py_decref(old);
            return;
        }
    }
    py_list_remove(callbacks, callback);
}

static void pcc_gc_fire_callbacks(const char *phase) {
    if (pcc_gc_callbacks == NULL || pcc_gc_callbacks_firing != 0) return;
    int64_t n = py_list_len(pcc_gc_callbacks);
    if (n <= 0) return;

    pcc_gc_callbacks_firing++;
    PyObject *phase_obj = py_str_new(phase, (int64_t)strlen(phase));
    PyObject *info = py_dict_new();
    if (phase_obj == NULL || info == NULL) {
        if (phase_obj != NULL) py_decref(phase_obj);
        if (info != NULL) py_decref(info);
        pcc_gc_callbacks_firing--;
        return;
    }

    for (int64_t i = 0; i < n; i++) {
        PyObject *callback = py_list_get(pcc_gc_callbacks, i);
        if (callback == NULL) continue;
        PyObject *args = py_tuple_new(2);
        if (args != NULL) {
            py_tuple_set_item(args, 0, phase_obj);
            py_tuple_set_item(args, 1, info);
            PyObject *result = py_obj_call(callback, args, py_None);
            if (result != NULL) py_decref(result);
            py_decref(args);
        }
        py_decref(callback);
        py_clear_exception();
    }

    py_decref(info);
    py_decref(phase_obj);
    pcc_gc_callbacks_firing--;
}

static int py_pointer_can_have_header(PyObject *o) {
    uintptr_t p = (uintptr_t)o;
    if (o == NULL) return 0;
    if (PY_IS_TAGGED_INT(o)) return 0;
    if (p < 0x1000u) return 0;
    if ((p & 0x7u) != 0u) return 0;
    if ((p >> 48) != 0u) return 0;
    return 1;
}

static void pcc_debug_maybe_abort_bad_decref(PyObject *o) {
    if (!pcc_obj_debug_runtime_enabled()) return;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    if (!py_pointer_can_have_header(o)) {
        fprintf(stderr, "py_decref suspicious pointer=%p\n", o);
        fflush(stderr);
        abort();
    }
    PyObjectHeader *h = py_header(o);
    int32_t tag = h->type_tag;
    if (tag == PY_TYPE_NONE || tag == PY_TYPE_BOOL || tag == PY_TYPE_INT
        || tag == PY_TYPE_FLOAT || tag == PY_TYPE_STR || tag == PY_TYPE_LIST
        || tag == PY_TYPE_DICT || tag == PY_TYPE_TUPLE || tag == PY_TYPE_SET
        || tag == PY_TYPE_FUNC || tag == PY_TYPE_CLASS || tag == PY_TYPE_INSTANCE
        || tag == PY_TYPE_EXC || tag == PY_TYPE_FILE || tag == PY_TYPE_ITER
        || tag == PY_TYPE_GEN || tag == PY_TYPE_COMPLEX || tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY || tag == PY_TYPE_MEMORYVIEW
        || tag == PY_TYPE_COROUTINE || tag == PY_TYPE_WEAKREF
        || tag == PY_TYPE_THREAD_LOCK || tag == PY_TYPE_THREAD_RLOCK
        || tag == PY_TYPE_THREAD_EVENT || tag == PY_TYPE_THREAD_CONDITION
        || tag == PY_TYPE_THREAD_SEMAPHORE
        || tag == PY_TYPE_THREAD
        || tag == PY_TYPE_TASK
        || tag == PY_TYPE_CONTINUATION
        || tag == PY_TYPE_VIRTUAL_THREAD
        || tag >= PY_TYPE_USER) {
        return;
    }
    fprintf(stderr,
        "py_decref invalid type_tag=%d for ptr=%p (possible corruption)\n",
        tag, o
    );
    fflush(stderr);
    abort();
}

static int py_type_tag_is_valid(int32_t tag) {
    return (
        tag == PY_TYPE_NONE || tag == PY_TYPE_BOOL || tag == PY_TYPE_INT
        || tag == PY_TYPE_FLOAT || tag == PY_TYPE_STR || tag == PY_TYPE_LIST
        || tag == PY_TYPE_DICT || tag == PY_TYPE_TUPLE || tag == PY_TYPE_SET
        || tag == PY_TYPE_FUNC || tag == PY_TYPE_CLASS || tag == PY_TYPE_INSTANCE
        || tag == PY_TYPE_EXC || tag == PY_TYPE_FILE || tag == PY_TYPE_ITER
        || tag == PY_TYPE_GEN || tag == PY_TYPE_COMPLEX || tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY || tag == PY_TYPE_MEMORYVIEW
        || tag == PY_TYPE_COROUTINE || tag == PY_TYPE_WEAKREF
        || tag == PY_TYPE_THREAD_LOCK || tag == PY_TYPE_THREAD_RLOCK
        || tag == PY_TYPE_THREAD_EVENT || tag == PY_TYPE_THREAD_CONDITION
        || tag == PY_TYPE_THREAD_SEMAPHORE || tag == PY_TYPE_THREAD
        || tag == PY_TYPE_TASK
        || tag == PY_TYPE_CONTINUATION
        || tag == PY_TYPE_VIRTUAL_THREAD
        || tag >= PY_TYPE_USER
    );
}

PyObject *py_bool_from_bit(int b) {
    return b ? py_True : py_False;
}

PyObject *pcc_gc_alloc(int64_t size, int32_t type_tag, int32_t flags) {
    if (size < (int64_t)sizeof(PyObjectHeader)) return NULL;
    pcc_thread_safepoint();
    pcc_gc_note_alloc(size);
    pcc_runtime_log_event_code(1, 1, size, type_tag, NULL);
    PyObjectHeader *h = (PyObjectHeader *)pcc_gc_try_minor_alloc(size);
    int32_t stored_flags = flags;
    if (h == NULL) {
        h = (PyObjectHeader *)pcc_gc_backend4_try_zpage_alloc(size, flags);
        if (h != NULL) stored_flags |= PY_FLAG_GC_ZPAGE_ALLOC;
    }
    if (h == NULL) {
        h = (PyObjectHeader *)calloc(1, (size_t)size);
    }
    if (h == NULL) return NULL;
    h->refcount = 1;
    h->type_tag = type_tag;
    h->flags = stored_flags;
    pcc_debug_note_alloc_size(h, size);
    pcc_gc_note_object_allocated_sized((PyObject *)h, size);
    pcc_runtime_log_event_code(1, 2, size, type_tag, h);
    return (PyObject *)h;
}

PyObject *pcc_gc_retain(PyObject *o) {
    py_incref(o);
    return o;
}

void pcc_gc_release(PyObject *o) {
    py_decref(o);
}

PyObject *pcc_gc_load_ptr(PyObject *owner, PyObject **slot) {
    (void)owner;
    if (slot == NULL) return NULL;
    PyObject *value = *slot;
    int64_t backend = pcc_gc_backend();
    if (
        backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        pcc_gc_note_load();
        PyObject *resolved = pcc_gc_note_relocation_read(value);
        if (resolved != value) {
            py_incref(resolved);
            *slot = resolved;
            py_decref(value);
            value = resolved;
        }
    }
    return value;
}

void pcc_gc_store_ptr(PyObject *owner, PyObject **slot, PyObject *value) {
    (void)owner;
    if (slot == NULL) return;
    int64_t backend = pcc_gc_backend();
    if (
        backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
        || backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
        || backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        pcc_gc_note_store();
    }
    pcc_gc_note_slot_write_barrier(owner, slot, value);
    pcc_runtime_log_event_code(2, 3, backend, 0, owner);
    PyObject *old = *slot;
    py_incref(value);
    *slot = value;
    py_decref(old);
}

void pcc_gc_store_root(PyObject **slot, PyObject *value) {
    pcc_gc_root_slot_lock();
    if (slot != NULL) {
        int64_t backend = pcc_gc_backend();
        if (
            backend == PCC_GC_KIND_INCREMENTAL_TRICOLOR
            || backend == PCC_GC_KIND_CONCURRENT_MARK_SWEEP
            || backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
            || backend == PCC_GC_KIND_COLORED_RELOCATING
        ) {
            pcc_gc_note_store();
        }
        pcc_gc_note_slot_write_barrier(NULL, slot, value);
        pcc_runtime_log_event_code(2, 3, backend, 0, NULL);
        PyObject *old = *slot;
        py_incref(value);
        *slot = value;
        if (
            old != NULL
            && !PY_IS_TAGGED_INT(old)
            && backend == PCC_GC_KIND_COLORED_RELOCATING
            && pcc_gc_object_is_known_no_lock(old) == 0
        ) {
            old = NULL;
        }
        py_decref(old);
    }
    pcc_gc_root_slot_unlock();
}

void pcc_gc_frame_enter(const void *frame_map, PyObject **slots) {
    pcc_gc_note_frame_enter(frame_map, slots);
}

void pcc_gc_frame_leave(PyObject **slots) {
    pcc_gc_note_frame_leave(slots);
}

void pcc_gc_safepoint(void) {
    pcc_gc_note_safepoint();
    pcc_thread_safepoint();
    if (!pcc_threads_enabled()) {
        (void)pcc_gc_step(1);
    }
}

int64_t pcc_gc_collect(int32_t reason) {
    (void)reason;
    int64_t backend = pcc_gc_backend();
    /* Reentrancy guard (mirror of the py_obj.py port): a gc.collect() invoked
     * from a finalizer running DURING an in-progress tracing collect must be a
     * no-op, else the reentrant mark/sweep corrupts the outer sweep's in-flight
     * state -> use-after-free segfault on #1/#2/#3/#4. #0 never sets the flag.
     * See gc-5backend-reentrant-collect-during-finalizer-no-libpython.md. */
    if (backend != PCC_GC_KIND_REFCOUNT_CYCLE
        && pcc_gc_explicit_collect_is_active()) {
        return 0;
    }
    pcc_runtime_log_event_code(2, 1, reason, backend, NULL);
    pcc_gc_fire_callbacks("start");
    int64_t collected = 0;
    if (backend == PCC_GC_KIND_REFCOUNT_CYCLE) {
        collected = py_gc_collect();
    } else {
        int64_t stw = pcc_stop_the_world();
        while (stw != 0) {
            pcc_thread_safepoint();
            stw = pcc_stop_the_world();
        }
        pcc_gc_begin_explicit_tracing_collect();
        for (;;) {
            int64_t stepped = pcc_gc_step(1024);
            if (stepped == 0) break;
        }
        if (pcc_gc_has_tracing_sweep() != 0) {
            collected += pcc_gc_collect_tracing();
        }
        pcc_gc_end_explicit_tracing_collect();
        (void)pcc_resume_world();
    }
    pcc_gc_fire_callbacks("stop");
    pcc_runtime_log_event_code(2, 2, collected, backend, NULL);
    return collected;
}

void pcc_gc_pin(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header_flags_or(py_header(o), PY_FLAG_GC_PINNED);
    pcc_gc_note_pin(1);
}

void pcc_gc_unpin(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header_flags_and(py_header(o), ~PY_FLAG_GC_PINNED);
    pcc_gc_note_pin(-1);
}

extern void pcc_debug_bad_incref(void *o, int32_t tag);

void py_incref(PyObject *o) {
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;  /* tagged ints carry no refcount */
    if (!py_pointer_can_have_header(o)) {
        if (pcc_obj_debug_runtime_enabled()) {
            pcc_debug_bad_incref(o, -2);
        }
        return;
    }
    PyObjectHeader *h = py_header(o);
    if (!py_type_tag_is_valid(h->type_tag) || h->type_tag > 500) {
        if (pcc_obj_debug_runtime_enabled()) {
            pcc_debug_bad_incref(o, h->type_tag);
        }
        return;
    }
    if (py_header_flags_load(h) & PY_FLAG_IMMORTAL) return;
    int64_t new_refcount = pcc_refcount_incref(&h->refcount);
    pcc_runtime_log_event_code(3, 1, new_refcount, h->type_tag, o);
}

typedef struct PccTrashNode {
    PyObject *obj;
    int32_t type_tag;
    struct PccTrashNode *next;
} PccTrashNode;

static _Thread_local int pcc_trash_dealloc_depth = 0;
static _Thread_local PccTrashNode *pcc_trash_head = NULL;
static _Thread_local PccTrashNode *pcc_trash_tail = NULL;

static int pcc_trash_should_defer(int32_t type_tag) {
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
        case PY_TYPE_VIRTUAL_THREAD:
        default:
            return type_tag >= PY_TYPE_USER;
    }
}

static void pcc_dealloc_dispatch(PyObject *o, int32_t type_tag) {
    switch (type_tag) {
        case PY_TYPE_INT:      py_dealloc_int(o);      break;
        case PY_TYPE_FLOAT:    py_dealloc_float(o);    break;
        case PY_TYPE_STR:      py_dealloc_str(o);      break;
        case PY_TYPE_LIST:     py_dealloc_list(o);     break;
        case PY_TYPE_TUPLE:    py_dealloc_tuple(o);    break;
        case PY_TYPE_DICT:     py_dealloc_dict(o);     break;
        case PY_TYPE_SET:      py_dealloc_set(o);      break;
        case PY_TYPE_FUNC:     py_dealloc_func(o);     break;
        case PY_TYPE_CLASS:    py_class_dealloc(o);    break;
        case PY_TYPE_INSTANCE: py_instance_dealloc(o); break;
        case PY_TYPE_EXC:      py_dealloc_exc(o);      break;
        case PY_TYPE_ITER:     py_dealloc_iter(o);     break;
        case PY_TYPE_GEN:      py_dealloc_gen(o);      break;
        case PY_TYPE_COROUTINE: py_dealloc_coroutine(o); break;
        case PY_TYPE_CONTINUATION: py_dealloc_continuation(o); break;
        case PY_TYPE_MEMORYVIEW: py_dealloc_memoryview(o); break;
        case PY_TYPE_WEAKREF:   py_dealloc_weakref(o); break;
        case PY_TYPE_THREAD_LOCK: py_dealloc_thread_lock(o); break;
        case PY_TYPE_THREAD_RLOCK: py_dealloc_thread_rlock(o); break;
        case PY_TYPE_THREAD_EVENT: py_dealloc_thread_event(o); break;
        case PY_TYPE_THREAD_CONDITION: py_dealloc_thread_condition(o); break;
        case PY_TYPE_THREAD_SEMAPHORE: py_dealloc_thread_semaphore(o); break;
        case PY_TYPE_THREAD: py_dealloc_thread_thread(o); break;
        case PY_TYPE_TASK: py_dealloc_task(o); break;
        case PY_TYPE_VIRTUAL_THREAD: py_dealloc_virtual_thread(o); break;
        default:
            if (type_tag >= PY_TYPE_USER) {
                py_instance_dealloc(o);
            } else {
                py_dealloc_generic(o);
            }
            break;
    }
}

static int pcc_trash_enqueue(PyObject *o, int32_t type_tag) {
    PccTrashNode *node = (PccTrashNode *)malloc(sizeof(PccTrashNode));
    if (node == NULL) return 0;
    node->obj = o;
    node->type_tag = type_tag;
    node->next = NULL;
    if (pcc_trash_tail != NULL) {
        pcc_trash_tail->next = node;
    } else {
        pcc_trash_head = node;
    }
    pcc_trash_tail = node;
    return 1;
}

static void pcc_trash_drain(void) {
    while (pcc_trash_head != NULL) {
        PccTrashNode *node = pcc_trash_head;
        pcc_trash_head = node->next;
        if (pcc_trash_head == NULL) pcc_trash_tail = NULL;
        PyObject *obj = node->obj;
        int32_t type_tag = node->type_tag;
        free(node);
        pcc_dealloc_dispatch(obj, type_tag);
    }
}

void py_decref(PyObject *o) {
    pcc_debug_maybe_abort_bad_decref(o);
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;
    if (!py_pointer_can_have_header(o)) {
        if (pcc_obj_debug_runtime_enabled()) {
            pcc_debug_bad_incref(o, -2);
        }
        return;
    }
    PyObjectHeader *h = py_header(o);
    if (!py_type_tag_is_valid(h->type_tag) || h->type_tag > 500) {
        if (pcc_obj_debug_runtime_enabled()) {
            pcc_debug_bad_incref(o, h->type_tag);
        }
        return;
    }
    if (py_header_flags_load(h) & PY_FLAG_IMMORTAL) return;
    if (
        pcc_gc_backend() == PCC_GC_KIND_COLORED_RELOCATING
        && pcc_gc_object_is_known(o) == 0
    ) {
        return;
    }
    assert(pcc_refcount_load(&h->refcount) > 0 && "py_decref: refcount underflow");
    int32_t type_tag = h->type_tag;
    int64_t new_refcount = pcc_refcount_decref(&h->refcount);
    assert(new_refcount >= 0 && "py_decref: refcount underflow");
    pcc_runtime_log_event_code(3, 2, new_refcount, type_tag, o);
    if (new_refcount > 0) return;
    pcc_refcount_forget(&h->refcount);
    pcc_runtime_log_event_code(3, 3, 0, type_tag, o);

    py_weakref_invalidate(o);
    pcc_gc_note_object_freeing(o);
    py_gc_untrack(o);
    if (
        pcc_trash_dealloc_depth > 0
        && pcc_trash_should_defer(type_tag)
        && pcc_trash_enqueue(o, type_tag)
    ) {
        return;
    }

    pcc_trash_dealloc_depth++;
    pcc_dealloc_dispatch(o, type_tag);
    if (pcc_trash_dealloc_depth == 1) {
        pcc_trash_drain();
    }
    pcc_trash_dealloc_depth--;
}
