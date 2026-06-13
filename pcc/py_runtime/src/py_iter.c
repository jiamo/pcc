/* pcc/py_runtime/src/py_iter.c
 *
 * Minimal native iterator wrapper for pcc sequence objects. This is the
 * first generator-protocol bridge: the Python frontend can materialise a
 * yield-list, wrap it in PY_TYPE_ITER, and then support next(g) / for g
 * without libpython.
 */

#include "py_internal.h"
#include <stdlib.h>


static PyObject *py_iter_new(PyObject *seq) {
    if (seq == NULL) return NULL;
    PyIterObject *it = (PyIterObject *)malloc(sizeof(PyIterObject));
    if (it == NULL) return NULL;
    it->h.refcount = 1;
    it->h.type_tag = PY_TYPE_ITER;
    it->h.flags = 0;
    py_incref(seq);
    it->seq = seq;
    it->index = 0;
    py_gc_track((PyObject *)it);
    return (PyObject *)it;
}


void py_dealloc_iter(PyObject *o) {
    PyIterObject *it = (PyIterObject *)o;
    PyObject *seq = pcc_gc_load_ptr(o, &it->seq);
    if (seq != NULL) py_decref(seq);
    pcc_gc_free_object_memory(o);
}


/* Callable-iterator variant of the 2-argument iter(callable, sentinel).
 *
 * We reuse PyIterObject rather than adding a new type tag (which would fan
 * out into every GC visit/relocate/dealloc switch). The single pointer slot
 * ``seq`` holds a 2-element tuple ``(callable, sentinel)`` so the existing
 * single-slot GC tracing stays correct. The ``index`` field is repurposed as
 * a state discriminator: values < 0 mean "callable iterator" and never occur
 * for a sequence iterator (whose index starts at 0 and only increments).
 *
 *   index == PY_ITER_CALLABLE_ACTIVE (-1): live, call the callable on next()
 *   index == PY_ITER_CALLABLE_DONE   (-2): exhausted (sentinel already hit)
 */
#define PY_ITER_CALLABLE_ACTIVE (-1)
#define PY_ITER_CALLABLE_DONE   (-2)

PyObject *py_iter_callable_new(PyObject *callable, PyObject *sentinel) {
    if (callable == NULL || sentinel == NULL) return NULL;
    PyObject *pair = py_tuple_new(2);
    if (pair == NULL) return NULL;
    py_tuple_set_item(pair, 0, callable);
    py_tuple_set_item(pair, 1, sentinel);
    PyIterObject *it = (PyIterObject *)malloc(sizeof(PyIterObject));
    if (it == NULL) {
        py_decref(pair);
        return NULL;
    }
    it->h.refcount = 1;
    it->h.type_tag = PY_TYPE_ITER;
    it->h.flags = 0;
    /* pair is a fresh reference owned by the iterator; py_dealloc_iter
     * decrefs it->seq, which releases the tuple (and its two members). */
    it->seq = pair;
    it->index = PY_ITER_CALLABLE_ACTIVE;
    py_gc_track((PyObject *)it);
    return (PyObject *)it;
}


PyObject *py_obj_iter(PyObject *o) {
    if (o == NULL) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_ITER || tag == PY_TYPE_GEN) {
        py_incref(o);
        return o;
    }
    if (tag == PY_TYPE_LIST || tag == PY_TYPE_TUPLE || tag == PY_TYPE_STR ||
        tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY ||
        tag == PY_TYPE_MEMORYVIEW) {
        return py_iter_new(o);
    }
    if (tag == PY_TYPE_DICT) {
        PyObject *keys = py_dict_keys(o);
        if (keys == NULL) return NULL;
        PyObject *it = py_iter_new(keys);
        py_decref(keys);
        return it;
    }
    if (tag == PY_TYPE_SET) {
        PyObject *items = py_set_items(o);
        if (items == NULL) return NULL;
        PyObject *it = py_iter_new(items);
        py_decref(items);
        return it;
    }
    if (pcc_capi_is_cext_type_tag((int64_t)tag) != 0) {
        PyObject *it = pcc_capi_cext_object_iter(o);
        if (it != NULL || py_err_occurred()) return it;
    }
    PyObject *dunder = py_user_iter_dispatch(o);
    if (dunder != NULL || py_err_occurred()) return dunder;
    py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "object is not iterable"));
    return NULL;
}


PyObject *py_obj_next(PyObject *it_obj) {
    if (it_obj != NULL && !PY_IS_TAGGED_INT(it_obj)) {
        PyObjectHeader *h = py_header(it_obj);
        if (h->type_tag == PY_TYPE_GEN) {
            return py_gen_next(it_obj);
        }
        if (pcc_capi_is_cext_type_tag((int64_t)h->type_tag) != 0) {
            PyObject *item = pcc_capi_cext_object_next(it_obj);
            if (item != NULL || py_err_occurred()) return item;
        }
    }
    if (it_obj == NULL || py_type_of(it_obj) != PY_TYPE_ITER) {
        PyObject *dunder = py_user_next_dispatch(it_obj);
        if (dunder != NULL || py_err_occurred()) {
            return dunder;
        }
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "object is not an iterator"));
        return NULL;
    }
    PyIterObject *it = (PyIterObject *)it_obj;
    if (it->index < 0) {
        /* Callable-iterator: iter(callable, sentinel). */
        if (it->index == PY_ITER_CALLABLE_DONE) {
            py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
            return NULL;
        }
        PyObject *pair = pcc_gc_load_ptr(it_obj, &it->seq);
        PyObject *callable = py_tuple_get(pair, 0);
        PyObject *sentinel = py_tuple_get(pair, 1);
        PyObject *args = py_tuple_new(0);
        PyObject *result = py_obj_call(callable, args, py_None);
        py_decref(args);
        py_decref(callable);
        if (result == NULL) {
            /* callable raised (or returned NULL) — propagate. */
            py_decref(sentinel);
            return NULL;
        }
        int64_t is_stop = py_obj_eq(result, sentinel);
        py_decref(sentinel);
        if (is_stop) {
            py_decref(result);
            it->index = PY_ITER_CALLABLE_DONE;
            py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
            return NULL;
        }
        return result;
    }
    PyObject *seq = pcc_gc_load_ptr(it_obj, &it->seq);
    int32_t tag = py_type_of(seq);
    int64_t n = 0;
    PyObject *item = NULL;
    if (tag == PY_TYPE_LIST) {
        n = py_list_len(seq);
        if (it->index >= n) goto exhausted;
        item = py_list_get(seq, it->index);
    } else if (tag == PY_TYPE_TUPLE) {
        n = py_tuple_len(seq);
        if (it->index >= n) goto exhausted;
        item = py_tuple_get(seq, it->index);
    } else if (tag == PY_TYPE_STR) {
        n = py_str_len(seq);
        if (it->index >= n) goto exhausted;
        PyObject *idx = py_int_from_i64(it->index);
        item = py_str_index(seq, idx);
        py_decref(idx);
    } else if (tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY ||
               tag == PY_TYPE_MEMORYVIEW) {
        n = py_bytes_len(seq);
        if (it->index >= n) goto exhausted;
        PyObject *idx = py_int_from_i64(it->index);
        item = py_bytes_getitem(seq, idx);
        py_decref(idx);
    } else {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "iterator source is invalid"));
        return NULL;
    }
    it->index++;
    return item;

exhausted:
    py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
    return NULL;
}
