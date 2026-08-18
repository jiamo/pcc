/* pcc/py_runtime/src/py_iter.c
 *
 * Minimal native iterator wrapper for pcc sequence objects. This is the
 * first generator-protocol bridge: the Python frontend can materialise a
 * yield-list, wrap it in PY_TYPE_ITER, and then support next(g) / for g
 * without libpython.
 */

#include "py_internal.h"
#include <stdlib.h>

static PyObject *iter_require_result(
    PyObject *result,
    const char *helper_name,
    const char *message
) {
    if (result == NULL) {
        py_runtime_error_if_unset(helper_name, message);
    }
    return result;
}

static int iter_prepare_moving_root(PyObject **slot, void **out_handle) {
    if (out_handle == NULL) return -1;
    *out_handle = NULL;
    if (slot == NULL || *slot == NULL || PY_IS_TAGGED_INT(*slot)) return 0;
    int64_t backend = pcc_gc_backend();
    if (
        backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && backend != PCC_GC_KIND_COLORED_RELOCATING
    ) return 0;
    void *handle = pcc_gc_scheduler_root_register_handle(slot);
    if (handle == NULL) return -1;
    *slot = pcc_gc_load_ptr(NULL, slot);
    *out_handle = handle;
    return 0;
}

static PyObject *iter_reload_moving_root(PyObject **slot, void *handle) {
    if (slot == NULL) return NULL;
    if (handle != NULL) *slot = pcc_gc_load_ptr(NULL, slot);
    return *slot;
}

static void iter_finish_moving_root(void *handle) {
    if (handle != NULL) pcc_gc_scheduler_root_unregister_handle(handle);
}


static PyObject *py_iter_new(PyObject *seq) {
    if (seq == NULL) return NULL;
    PyIterObject *it = (PyIterObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyIterObject), PY_TYPE_ITER, 0
    );
    if (it == NULL) return NULL;
    py_incref(seq);
    it->seq = seq;
    it->index = 0;
    py_gc_track((PyObject *)it);
    pcc_gc_publish_initialized((PyObject *)it);
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
    if (callable == NULL || sentinel == NULL) {
        return iter_require_result(
            NULL,
            "py_iter_callable_new",
            "iter(callable, sentinel) received NULL operand"
        );
    }
    PyObject *pair = py_tuple_new(2);
    if (pair == NULL) {
        return iter_require_result(
            NULL,
            "py_tuple_new",
            "iter(callable, sentinel) could not allocate its state tuple"
        );
    }
    py_tuple_set_item(pair, 0, callable);
    py_tuple_set_item(pair, 1, sentinel);
    PyIterObject *it = (PyIterObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyIterObject), PY_TYPE_ITER, 0
    );
    if (it == NULL) {
        iter_require_result(
            NULL,
            "py_iter_callable_new",
            "iter(callable, sentinel) could not allocate iterator state"
        );
        py_decref(pair);
        return NULL;
    }
    /* pair is a fresh reference owned by the iterator; py_dealloc_iter
     * decrefs it->seq, which releases the tuple (and its two members). */
    it->seq = pair;
    it->index = PY_ITER_CALLABLE_ACTIVE;
    py_gc_track((PyObject *)it);
    pcc_gc_publish_initialized((PyObject *)it);
    return (PyObject *)it;
}


PyObject *py_obj_iter(PyObject *o) {
    if (o == NULL) {
        return iter_require_result(
            NULL,
            "py_obj_iter",
            "py_obj_iter received NULL object"
        );
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_ITER || tag == PY_TYPE_GEN || tag == PY_TYPE_FILE) {
        py_incref(o);
        return o;
    }
    if (tag == PY_TYPE_LIST || tag == PY_TYPE_TUPLE || tag == PY_TYPE_STR ||
        tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY ||
        tag == PY_TYPE_MEMORYVIEW) {
        return iter_require_result(
            py_iter_new(o),
            "py_iter_new",
            "sequence iterator allocation failed without setting an exception"
        );
    }
    if (tag == PY_TYPE_DICT) {
        PyObject *keys = py_dict_keys(o);
        if (keys == NULL) {
            return iter_require_result(
                NULL,
                "py_dict_keys",
                "dictionary iterator snapshot failed without setting an exception"
            );
        }
        PyObject *it = py_iter_new(keys);
        if (it == NULL) {
            iter_require_result(
                NULL,
                "py_iter_new",
                "dictionary iterator allocation failed without setting an exception"
            );
        }
        py_decref(keys);
        return it;
    }
    if (tag == PY_TYPE_SET) {
        PyObject *items = py_set_items(o);
        if (items == NULL) {
            return iter_require_result(
                NULL,
                "py_set_items",
                "set iterator snapshot failed without setting an exception"
            );
        }
        PyObject *it = py_iter_new(items);
        if (it == NULL) {
            iter_require_result(
                NULL,
                "py_iter_new",
                "set iterator allocation failed without setting an exception"
            );
        }
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
            return iter_require_result(
                py_gen_next(it_obj),
                "py_gen_next",
                "generator next returned NULL without StopIteration or an exception"
            );
        }
        if (h->type_tag == PY_TYPE_FILE) {
            PyObject *line = py_file_readline(it_obj, -1);
            if (line == NULL) {
                return iter_require_result(
                    NULL,
                    "py_file_readline",
                    "file iterator readline returned NULL without an exception"
                );
            }
            int32_t line_tag = py_type_of(line);
            int64_t line_length = -1;
            if (line_tag == PY_TYPE_STR) line_length = py_str_len(line);
            else if (line_tag == PY_TYPE_BYTES) line_length = py_bytes_len(line);
            if (line_length == 0) {
                py_decref(line);
                py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
                return NULL;
            }
            if (line_length < 0) {
                py_decref(line);
                return iter_require_result(
                    NULL,
                    "py_file_readline",
                    "file iterator readline returned a non-line object"
                );
            }
            return line;
        }
        if (pcc_capi_is_cext_type_tag((int64_t)h->type_tag) != 0) {
            PyObject *item = pcc_capi_cext_object_next(it_obj);
            if (item != NULL || py_err_occurred()) return item;
        }
    }
    if (it_obj == NULL || py_type_of(it_obj) != PY_TYPE_ITER) {
        PyObject *dunder = py_user_next_dispatch(it_obj);
        if (dunder != NULL || py_err_occurred()) return dunder;
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "object is not an iterator"));
        return NULL;
    }

    PyObject *it_storage = it_obj;
    void *it_handle = NULL;
    if (iter_prepare_moving_root(&it_storage, &it_handle) != 0) return NULL;
    PyIterObject *it = (PyIterObject *)it_storage;
    int64_t iterator_index = it->index;

    if (iterator_index < 0) {
        if (iterator_index == PY_ITER_CALLABLE_DONE) {
            iter_finish_moving_root(it_handle);
            py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
            return NULL;
        }

        PyObject *pair = pcc_gc_load_ptr(it_storage, &it->seq);
        PyObject *callable_storage = py_tuple_get(pair, 0);
        if (callable_storage == NULL) {
            iter_finish_moving_root(it_handle);
            return iter_require_result(
                NULL, "py_tuple_get", "callable iterator lost its callable"
            );
        }
        void *callable_handle = NULL;
        if (iter_prepare_moving_root(
                &callable_storage, &callable_handle
            ) != 0) {
            py_decref(callable_storage);
            iter_finish_moving_root(it_handle);
            return NULL;
        }

        it = (PyIterObject *)iter_reload_moving_root(&it_storage, it_handle);
        pair = pcc_gc_load_ptr(it_storage, &it->seq);
        PyObject *sentinel_storage = py_tuple_get(pair, 1);
        if (sentinel_storage == NULL) {
            iter_require_result(
                NULL, "py_tuple_get", "callable iterator lost its sentinel"
            );
            PyObject *callable = iter_reload_moving_root(
                &callable_storage, callable_handle
            );
            iter_finish_moving_root(callable_handle);
            py_decref(callable);
            iter_finish_moving_root(it_handle);
            return NULL;
        }
        void *sentinel_handle = NULL;
        if (iter_prepare_moving_root(
                &sentinel_storage, &sentinel_handle
            ) != 0) {
            py_decref(sentinel_storage);
            PyObject *callable = iter_reload_moving_root(
                &callable_storage, callable_handle
            );
            iter_finish_moving_root(callable_handle);
            py_decref(callable);
            iter_finish_moving_root(it_handle);
            return NULL;
        }

        PyObject *args_storage = py_tuple_new(0);
        if (args_storage == NULL) {
            iter_require_result(
                NULL,
                "py_tuple_new",
                "callable iterator could not allocate its argument tuple"
            );
            PyObject *sentinel = iter_reload_moving_root(
                &sentinel_storage, sentinel_handle
            );
            iter_finish_moving_root(sentinel_handle);
            py_decref(sentinel);
            PyObject *callable = iter_reload_moving_root(
                &callable_storage, callable_handle
            );
            iter_finish_moving_root(callable_handle);
            py_decref(callable);
            iter_finish_moving_root(it_handle);
            return NULL;
        }
        void *args_handle = NULL;
        if (iter_prepare_moving_root(&args_storage, &args_handle) != 0) {
            py_decref(args_storage);
            PyObject *sentinel = iter_reload_moving_root(
                &sentinel_storage, sentinel_handle
            );
            iter_finish_moving_root(sentinel_handle);
            py_decref(sentinel);
            PyObject *callable = iter_reload_moving_root(
                &callable_storage, callable_handle
            );
            iter_finish_moving_root(callable_handle);
            py_decref(callable);
            iter_finish_moving_root(it_handle);
            return NULL;
        }

        PyObject *result_storage = py_obj_call(
            iter_reload_moving_root(&callable_storage, callable_handle),
            iter_reload_moving_root(&args_storage, args_handle),
            py_None
        );
        void *result_handle = NULL;
        if (
            result_storage != NULL
            && iter_prepare_moving_root(&result_storage, &result_handle) != 0
        ) {
            py_decref(result_storage);
            result_storage = NULL;
            iter_require_result(
                NULL,
                "pcc_gc_scheduler_root_register_handle",
                "callable iterator could not root its result"
            );
        } else if (result_storage == NULL) {
            iter_require_result(
                NULL,
                "py_obj_call",
                "callable iterator returned NULL without setting an exception"
            );
        }

        PyObject *args = iter_reload_moving_root(&args_storage, args_handle);
        iter_finish_moving_root(args_handle);
        py_decref(args);
        PyObject *callable = iter_reload_moving_root(
            &callable_storage, callable_handle
        );
        iter_finish_moving_root(callable_handle);
        py_decref(callable);
        if (result_storage == NULL) {
            PyObject *sentinel = iter_reload_moving_root(
                &sentinel_storage, sentinel_handle
            );
            iter_finish_moving_root(sentinel_handle);
            py_decref(sentinel);
            iter_finish_moving_root(it_handle);
            return NULL;
        }

        int64_t is_stop = py_obj_eq(
            iter_reload_moving_root(&result_storage, result_handle),
            iter_reload_moving_root(&sentinel_storage, sentinel_handle)
        );
        int had_error = py_err_occurred() != NULL;
        PyObject *result = iter_reload_moving_root(
            &result_storage, result_handle
        );
        PyObject *sentinel = iter_reload_moving_root(
            &sentinel_storage, sentinel_handle
        );
        iter_finish_moving_root(sentinel_handle);
        py_decref(sentinel);
        if (had_error) {
            iter_finish_moving_root(result_handle);
            py_decref(result);
            iter_finish_moving_root(it_handle);
            return NULL;
        }
        if (is_stop) {
            iter_finish_moving_root(result_handle);
            py_decref(result);
            it = (PyIterObject *)iter_reload_moving_root(
                &it_storage, it_handle
            );
            it->index = PY_ITER_CALLABLE_DONE;
            iter_finish_moving_root(it_handle);
            py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
            return NULL;
        }
        iter_finish_moving_root(result_handle);
        iter_finish_moving_root(it_handle);
        return result;
    }

    PyObject *seq = pcc_gc_load_ptr(it_storage, &it->seq);
    int32_t tag = py_type_of(seq);
    int64_t n = 0;
    PyObject *item = NULL;
    if (tag == PY_TYPE_LIST) {
        n = py_list_len(seq);
        if (iterator_index >= n) goto exhausted;
        item = py_list_get(seq, iterator_index);
    } else if (tag == PY_TYPE_TUPLE) {
        n = py_tuple_len(seq);
        if (iterator_index >= n) goto exhausted;
        item = py_tuple_get(seq, iterator_index);
    } else if (tag == PY_TYPE_STR) {
        n = py_str_len(seq);
        if (iterator_index >= n) goto exhausted;
        PyObject *idx = py_int_from_i64(iterator_index);
        if (idx == NULL) {
            iter_finish_moving_root(it_handle);
            return iter_require_result(
                NULL,
                "py_int_from_i64",
                "string iterator could not allocate its index"
            );
        }
        item = py_str_index(seq, idx);
        py_decref(idx);
    } else if (tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY ||
               tag == PY_TYPE_MEMORYVIEW) {
        n = py_bytes_len(seq);
        if (iterator_index >= n) goto exhausted;
        PyObject *idx = py_int_from_i64(iterator_index);
        if (idx == NULL) {
            iter_finish_moving_root(it_handle);
            return iter_require_result(
                NULL,
                "py_int_from_i64",
                "bytes iterator could not allocate its index"
            );
        }
        item = py_bytes_getitem(seq, idx);
        py_decref(idx);
    } else {
        iter_finish_moving_root(it_handle);
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "iterator source is invalid"));
        return NULL;
    }
    if (item == NULL) {
        iter_finish_moving_root(it_handle);
        return iter_require_result(
            NULL,
            "py_obj_next",
            "iterator element lookup returned NULL without setting an exception"
        );
    }
    PyObject *item_storage = item;
    void *item_handle = NULL;
    if (iter_prepare_moving_root(&item_storage, &item_handle) != 0) {
        py_decref(item_storage);
        iter_finish_moving_root(it_handle);
        return NULL;
    }
    it = (PyIterObject *)iter_reload_moving_root(&it_storage, it_handle);
    it->index = iterator_index + 1;
    item = iter_reload_moving_root(&item_storage, item_handle);
    iter_finish_moving_root(item_handle);
    iter_finish_moving_root(it_handle);
    return item;

exhausted:
    iter_finish_moving_root(it_handle);
    py_raise_owned(py_exc_new(PY_EXC_STOPITERATION, ""));
    return NULL;
}
