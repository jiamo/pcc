/* Minimal weakref.ref support for pcc's native Python runtime.
 *
 * Weak references keep a borrowed target pointer and are linked in a
 * process-global list. Object dealloc/in-cycle collection invalidates
 * entries for the target before memory is released.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

static PyWeakRefObject *g_weakrefs = NULL;

static void weakref_unlink(PyWeakRefObject *wr) {
    if (wr->prev == wr) {
        wr->prev = NULL;
        wr->next = NULL;
        return;
    }
    if (wr->prev == NULL && g_weakrefs != wr) {
        wr->next = NULL;
        return;
    }
    if (wr->prev != NULL && wr->prev->next != wr) {
        wr->prev = NULL;
        wr->next = NULL;
        return;
    }
    if (wr->prev) wr->prev->next = wr->next;
    else g_weakrefs = wr->next;
    if (wr->next) wr->next->prev = wr->prev;
    wr->prev = NULL;
    wr->next = NULL;
}

PyObject *py_weakref_new(PyObject *target, PyObject *callback) {
    if (target == NULL || PY_IS_TAGGED_INT(target)) return NULL;
    /* Value projections are identity-free; a weak reference observes
     * identity lifetime and a ValueBox's lifetime is unpredictable
     * (every boxing makes a new box). Mirror CPython's analogue
     * (weakref.ref(3) -> TypeError). The compile-time diagnostic
     * catches the static form; this covers Dyn-path boxes. */
    if (py_type_of(target) == PY_TYPE_VALUEBOX) {
        py_raise(py_exc_new(
            PY_EXC_TYPEERROR,
            "cannot create weak reference to a valueclass payload"));
        return NULL;
    }
    if (callback == py_None) callback = NULL;
    target = pcc_gc_note_relocation_read(target);

    PyWeakRefObject *wr = (PyWeakRefObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyWeakRefObject), PY_TYPE_WEAKREF, 0);
    if (wr == NULL) return NULL;
    memset((char *)wr + sizeof(PyObjectHeader), 0,
           sizeof(PyWeakRefObject) - sizeof(PyObjectHeader));
    wr->target = target;
    wr->callback = NULL;
    if (callback != NULL) {
        pcc_gc_store_ptr((PyObject *)wr, &wr->callback, callback);
    }

    wr->next = g_weakrefs;
    if (g_weakrefs != NULL) g_weakrefs->prev = wr;
    g_weakrefs = wr;
    pcc_runtime_log_event("weakref", "new", py_header(target)->type_tag, callback != NULL, target);
    return (PyObject *)wr;
}

PyObject *py_weakref_call(PyObject *ref) {
    if (ref == NULL || PY_IS_TAGGED_INT(ref)) return NULL;
    if (py_header(ref)->type_tag != PY_TYPE_WEAKREF) return NULL;
    PyWeakRefObject *wr = (PyWeakRefObject *)ref;
    if (wr->target == NULL) {
        py_incref(py_None);
        return py_None;
    }
    PyObject *resolved = pcc_gc_note_relocation_read(wr->target);
    if (resolved != wr->target) {
        wr->target = resolved;
    }
    py_incref(wr->target);
    return wr->target;
}

PyObject *py_weak_value_dict_new(void) {
    return py_dict_new();
}

int64_t py_weak_value_dict_set(PyObject *dict, PyObject *key, PyObject *value) {
    if (dict == NULL || key == NULL || value == NULL) return -1;
    PyObject *wr = py_weakref_new(value, py_None);
    if (wr == NULL) return -1;
    py_dict_set(dict, key, wr);
    py_decref(wr);
    return 0;
}

int64_t py_weak_value_dict_contains(PyObject *dict, PyObject *key) {
    if (dict == NULL || key == NULL) return 0;
    PyObject *wr = py_dict_get(dict, key);
    if (wr == NULL) return 0;
    PyObject *target = py_weakref_call(wr);
    py_decref(wr);
    if (target == NULL) return 0;
    if (target == py_None) {
        py_decref(target);
        (void)py_dict_del(dict, key);
        return 0;
    }
    py_decref(target);
    return 1;
}

int64_t py_weak_value_dict_len(PyObject *dict) {
    if (dict == NULL) return 0;
    PyObject *keys = py_dict_keys(dict);
    if (keys == NULL) return 0;
    int64_t n = py_list_len(keys);
    int64_t count = 0;
    for (int64_t i = 0; i < n; i++) {
        PyObject *key = py_list_get(keys, i);
        if (key == NULL) continue;
        if (py_weak_value_dict_contains(dict, key)) count++;
        py_decref(key);
    }
    py_decref(keys);
    return count;
}

PyObject *py_weak_key_dict_new(void) {
    return py_list_new(0);
}

static PyObject *weak_key_entry_new(PyObject *key, PyObject *value) {
    PyObject *wr = py_weakref_new(key, py_None);
    if (wr == NULL) return NULL;
    PyObject *entry = py_tuple_new(2);
    if (entry == NULL) {
        py_decref(wr);
        return NULL;
    }
    py_tuple_set_item(entry, 0, wr);
    py_tuple_set_item(entry, 1, value);
    py_decref(wr);
    return entry;
}

int64_t py_weak_key_dict_set(PyObject *dict, PyObject *key, PyObject *value) {
    if (dict == NULL || key == NULL || value == NULL) return -1;
    int64_t n = py_list_len(dict);
    for (int64_t i = 0; i < n; i++) {
        PyObject *entry = py_list_get(dict, i);
        if (entry == NULL) continue;
        PyObject *wr = py_tuple_get(entry, 0);
        PyObject *live = py_weakref_call(wr);
        int same = live != NULL && live != py_None && live == key;
        if (live != NULL) py_decref(live);
        if (wr != NULL) py_decref(wr);
        if (same) {
            PyObject *replacement = weak_key_entry_new(key, value);
            if (replacement == NULL) {
                py_decref(entry);
                return -1;
            }
            py_list_set(dict, i, replacement);
            py_decref(replacement);
            py_decref(entry);
            return 0;
        }
        py_decref(entry);
    }
    PyObject *entry = weak_key_entry_new(key, value);
    if (entry == NULL) return -1;
    py_list_append(dict, entry);
    py_decref(entry);
    return 0;
}

int64_t py_weak_key_dict_len(PyObject *dict) {
    if (dict == NULL) return 0;
    int64_t count = 0;
    int64_t i = 0;
    while (i < py_list_len(dict)) {
        PyObject *entry = py_list_get(dict, i);
        if (entry == NULL) {
            i++;
            continue;
        }
        PyObject *wr = py_tuple_get(entry, 0);
        PyObject *live = py_weakref_call(wr);
        int is_live = live != NULL && live != py_None;
        if (live != NULL) py_decref(live);
        if (wr != NULL) py_decref(wr);
        if (is_live) {
            count++;
            i++;
        } else {
            PyObject *popped = py_list_pop(dict, i);
            if (popped != NULL) py_decref(popped);
        }
        py_decref(entry);
    }
    return count;
}

void py_weakref_invalidate(PyObject *target) {
    if (target == NULL || PY_IS_TAGGED_INT(target)) return;
    PyWeakRefObject *wr = g_weakrefs;
    while (wr != NULL) {
        PyWeakRefObject *next = wr->next;
        PyObject *wr_target = wr->target;
        PyObject *resolved = pcc_gc_note_relocation_read(wr_target);
        if (wr_target == target || resolved == target) {
            wr->target = NULL;
            pcc_runtime_log_event("weakref", "invalidate", 0, py_header(target)->type_tag, target);
            PyObject *callback = pcc_gc_load_ptr((PyObject *)wr, &wr->callback);
            if (callback != NULL) {
                PyObject *args = py_tuple_new(1);
                if (args != NULL) {
                    pcc_runtime_log_event("weakref", "callback", 0, 0, (PyObject *)wr);
                    py_tuple_set_item(args, 0, (PyObject *)wr);
                    /* Weakref callbacks are an unraisable boundary: the
                     * callback result is ignored, but its owned reference
                     * must still be released.  py_obj_call classifies a
                     * silent NULL as RuntimeError before we deliberately
                     * clear the unraisable exception below. */
                    PyObject *result = py_obj_call(callback, args, py_None);
                    if (result != NULL) py_decref(result);
                    py_decref(args);
                }
                py_clear_exception();
            }
        } else if (resolved != wr_target) {
            wr->target = resolved;
        }
        wr = next;
    }
}

int64_t py_weakref_retarget(PyObject *from_obj, PyObject *to_obj) {
    if (from_obj == NULL || to_obj == NULL) return -1;
    if (PY_IS_TAGGED_INT(from_obj) || PY_IS_TAGGED_INT(to_obj)) return -1;
    if (
        py_header(from_obj)->type_tag != PY_TYPE_WEAKREF
        || py_header(to_obj)->type_tag != PY_TYPE_WEAKREF
    ) return -1;

    PyWeakRefObject *from = (PyWeakRefObject *)from_obj;
    PyWeakRefObject *to = (PyWeakRefObject *)to_obj;
    int found = 0;
    for (PyWeakRefObject *wr = g_weakrefs; wr != NULL; wr = wr->next) {
        if (wr == from) {
            found = 1;
            break;
        }
    }
    if (!found) return -1;

    to->prev = from->prev;
    to->next = from->next;
    if (from->prev != NULL) {
        from->prev->next = to;
    } else {
        g_weakrefs = to;
    }
    if (from->next != NULL) {
        from->next->prev = to;
    }

    from->prev = from;
    from->next = NULL;
    return 0;
}

void py_dealloc_weakref(PyObject *ref) {
    if (ref == NULL) return;
    PyWeakRefObject *wr = (PyWeakRefObject *)ref;
    weakref_unlink(wr);
    pcc_runtime_log_event("weakref", "dealloc", 0, 0, ref);
    PyObject *callback = pcc_gc_load_ptr(ref, &wr->callback);
    if (callback != NULL) py_decref(callback);
    pcc_gc_free_object_memory(ref);
}
