/* pcc/py_runtime/src/py_obj.c
 *
 * PyObject header, reference counting, and global singletons.
 *
 * Reference-counting rules (Phase 1):
 *   - py_incref / py_decref are no-ops for tagged ints (low bit = 1).
 *   - They are no-ops for immortal objects (flag bit 0 set).
 *   - When refcount hits zero, we dispatch by type_tag to a type-specific
 *     deallocator and free the memory.
 *   - Cycle collection (py_gc_*) is a stub until Phase 2/3.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>

/* ---- Forward decls for type-specific dealloc -------------------------- */
/* Defined in their respective .c files; kept here to avoid another
 * header and to centralize dispatch. */
static void py_dealloc_int(PyObject *o);
static void py_dealloc_float(PyObject *o);
static void py_dealloc_str(PyObject *o);
static void py_dealloc_list(PyObject *o);
static void py_dealloc_tuple(PyObject *o);
static void py_dealloc_dict(PyObject *o);
static void py_dealloc_set(PyObject *o);
static void py_dealloc_generic(PyObject *o);

/* ---- Immortal singletons ---------------------------------------------- */
/* None / True / False are static PyObjectHeader instances with the
 * immortal flag set. They are never freed. */

static PyObjectHeader py_none_storage = {
    .refcount = 1,
    .type_tag = PY_TYPE_NONE,
    .flags    = PY_FLAG_IMMORTAL,
};
static PyObjectHeader py_true_storage = {
    .refcount = 1,
    .type_tag = PY_TYPE_BOOL,
    .flags    = PY_FLAG_IMMORTAL,
};
static PyObjectHeader py_false_storage = {
    .refcount = 1,
    .type_tag = PY_TYPE_BOOL,
    .flags    = PY_FLAG_IMMORTAL,
};

PyObject *const py_None  = (PyObject *)&py_none_storage;
PyObject *const py_True  = (PyObject *)&py_true_storage;
PyObject *const py_False = (PyObject *)&py_false_storage;

PyObject *py_bool_from_bit(int b) {
    return b ? py_True : py_False;
}

/* ---- INCREF / DECREF -------------------------------------------------- */

void py_incref(PyObject *o) {
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;  /* tagged ints carry no refcount */
    PyObjectHeader *h = py_header(o);
    if (h->flags & PY_FLAG_IMMORTAL) return;
    h->refcount++;
}

void py_decref(PyObject *o) {
    if (o == NULL) return;
    if (PY_IS_TAGGED_INT(o)) return;
    PyObjectHeader *h = py_header(o);
    if (h->flags & PY_FLAG_IMMORTAL) return;
    assert(h->refcount > 0 && "py_decref: refcount underflow");
    if (--h->refcount > 0) return;

    /* Dispatch dealloc by type. */
    switch (h->type_tag) {
        case PY_TYPE_INT:      py_dealloc_int(o);   break;
        case PY_TYPE_FLOAT:    py_dealloc_float(o); break;
        case PY_TYPE_STR:      py_dealloc_str(o);   break;
        case PY_TYPE_LIST:     py_dealloc_list(o);  break;
        case PY_TYPE_TUPLE:    py_dealloc_tuple(o); break;
        case PY_TYPE_DICT:     py_dealloc_dict(o);  break;
        case PY_TYPE_SET:      py_dealloc_set(o);   break;
        case PY_TYPE_CLASS:    py_class_dealloc(o); break;
        case PY_TYPE_INSTANCE: py_instance_dealloc(o); break;
        case PY_TYPE_EXC:      py_dealloc_exc(o);   break;
        default:
            /* Per-class PY_TYPE_USER+N tag: still an instance. */
            if (h->type_tag >= PY_TYPE_USER) {
                py_instance_dealloc(o);
            } else {
                py_dealloc_generic(o);
            }
            break;
    }
}

/* ---- Type-specific deallocators --------------------------------------- */
/* These are intentionally in py_obj.c so the dispatch table is one source
 * of truth. Individual modules expose _contents_free helpers when needed. */

static void py_dealloc_int(PyObject *o) {
    /* Heap int: no owned children. */
    free(o);
}

static void py_dealloc_float(PyObject *o) {
    free(o);
}

static void py_dealloc_str(PyObject *o) {
    /* PyStrObject has a flexible-array-member tail, so the UTF-8 bytes
     * share a single allocation with the header. One free() is enough. */
    free(o);
}

static void py_dealloc_list(PyObject *o) {
    PyListObject *l = (PyListObject *)o;
    for (int64_t i = 0; i < l->length; i++) {
        py_decref(l->items[i]);
    }
    free(l->items);
    free(l);
}

static void py_dealloc_tuple(PyObject *o) {
    /* PyTupleObject uses a flexible-array items[], so the header + items
     * are a single allocation. One free() after decref'ing the children. */
    PyTupleObject *t = (PyTupleObject *)o;
    for (int64_t i = 0; i < t->len; i++) {
        py_decref(t->items[i]);
    }
    free(t);
}

static void py_dealloc_dict(PyObject *o) {
    PyDictObject *d = (PyDictObject *)o;
    if (d->entries != NULL) {
        for (int64_t i = 0; i < d->entries_used; i++) {
            DictEntry *e = &d->entries[i];
            if (e->key != NULL) {
                py_decref(e->key);
                py_decref(e->value);
            }
        }
        free(d->entries);
    }
    free(d->indices);
    free(d);
}

static void py_dealloc_set(PyObject *o) {
    PySetObject *s = (PySetObject *)o;
    if (s->entries != NULL) {
        for (int64_t i = 0; i < s->capacity; i++) {
            PyObject *k = s->entries[i].key;
            if (k != NULL && k != py_set_dummy) {
                py_decref(k);
            }
        }
        free(s->entries);
    }
    free(s);
}

static void py_dealloc_generic(PyObject *o) {
    /* Fallback for types we haven't wired up yet.
     * TODO(phase2+): call type-specific finalizer / tp_dealloc slot. */
    free(o);
}

/* ---- GC stubs (Phase 2/3) --------------------------------------------- */
/* The ABI requires these symbols to exist. They're no-ops until the
 * tricolor cycle collector lands. */
void py_gc_init(void)              { /* TODO(phase2+): init tri-color lists */ }
void py_gc_collect(void)           { /* TODO(phase2+): run a collection   */ }
void py_gc_track(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header(o)->flags |= PY_FLAG_GC_TRACKED;
}
void py_gc_untrack(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header(o)->flags &= ~PY_FLAG_GC_TRACKED;
}
