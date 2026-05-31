/* Helpers for call splat lowering: f(*args), f(**kwargs), f(*a, **k).
 *
 * These helpers intentionally live below codegen.  The frontend can lower a
 * dynamic call into:
 *
 *   args = py_call_merge_posargs(base_tuple, star_value)
 *   kwargs = py_call_merge_kwargs(base_dict, star_kw)
 *   py_obj_call(callable, args, kwargs)
 *
 * The first implementation handles tuple/list for *args and dict for **kwargs,
 * matching the common no-libpython subset and giving codegen a concrete ABI.
 */

#include "py_internal.h"

static PyObject *pcc_empty_tuple(void) {
    return py_tuple_new(0);
}

static int64_t pcc_sequence_len_for_splat(PyObject *o) {
    if (o == NULL) return -1;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_TUPLE) return py_tuple_len(o);
    if (tag == PY_TYPE_LIST) return py_list_len(o);
    return -1;
}

static PyObject *pcc_sequence_get_for_splat(PyObject *o, int64_t i) {
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_TUPLE) return py_tuple_get(o, i);
    if (tag == PY_TYPE_LIST) return py_list_get(o, i);
    return NULL;
}

PyObject *py_call_merge_posargs(PyObject *base_tuple, PyObject *star_args) {
    int64_t base_len = 0;
    if (base_tuple == NULL || base_tuple == py_None) {
        base_tuple = pcc_empty_tuple();
        if (base_tuple == NULL) return NULL;
    } else if (py_type_of(base_tuple) != PY_TYPE_TUPLE) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "call args base must be tuple"));
        return NULL;
    } else {
        py_incref(base_tuple);
    }
    base_len = py_tuple_len(base_tuple);

    if (star_args == NULL || star_args == py_None) {
        return base_tuple;
    }

    int64_t star_len = pcc_sequence_len_for_splat(star_args);
    if (star_len < 0) {
        py_decref(base_tuple);
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "*args must be tuple or list"));
        return NULL;
    }

    PyObject *out = py_tuple_new(base_len + star_len);
    if (out == NULL) {
        py_decref(base_tuple);
        return NULL;
    }

    for (int64_t i = 0; i < base_len; i++) {
        PyObject *item = py_tuple_get(base_tuple, i);
        py_tuple_set_item(out, i, item);
        py_decref(item);
    }
    for (int64_t i = 0; i < star_len; i++) {
        PyObject *item = pcc_sequence_get_for_splat(star_args, i);
        py_tuple_set_item(out, base_len + i, item);
        py_decref(item);
    }
    py_decref(base_tuple);
    return out;
}

/* zip(*rows): transpose. ``rows`` is the *splat sequence (tuple/list) whose
 * elements are the iterables to zip. Returns a list of tuples (pcc materialises
 * zip eagerly), truncated to the shortest row — matching zip() semantics. The
 * frontend lowers ``zip(*m)`` to this; the static zip(a,b,...) path can't size a
 * runtime number of iterables. */
PyObject *py_zip_star(PyObject *rows) {
    if (rows == NULL || rows == py_None) return py_list_new(0);
    int64_t nrows = pcc_sequence_len_for_splat(rows);
    if (nrows < 0) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR,
                            "zip(*x): x must be a tuple or list"));
        return NULL;
    }
    if (nrows == 0) return py_list_new(0);

    int64_t min_len = -1;
    for (int64_t r = 0; r < nrows; r++) {
        PyObject *row = pcc_sequence_get_for_splat(rows, r);
        if (row == NULL) return NULL;
        int64_t rl = py_obj_len(row);
        py_decref(row);
        if (rl < 0) return NULL;
        if (min_len < 0 || rl < min_len) min_len = rl;
    }
    if (min_len < 0) min_len = 0;

    PyObject *out = py_list_new(0);
    if (out == NULL) return NULL;
    for (int64_t col = 0; col < min_len; col++) {
        PyObject *cidx = py_int_from_i64(col);
        PyObject *tup = py_tuple_new(nrows);
        if (tup == NULL) {
            py_decref(cidx);
            py_decref(out);
            return NULL;
        }
        for (int64_t r = 0; r < nrows; r++) {
            PyObject *row = pcc_sequence_get_for_splat(rows, r);
            PyObject *elem = py_obj_getitem(row, cidx);
            py_decref(row);
            py_tuple_set_item(tup, r, elem);   /* takes its own ref */
            if (elem != NULL) py_decref(elem);
        }
        py_decref(cidx);
        py_list_append(out, tup);              /* takes its own ref */
        py_decref(tup);
    }
    return out;
}

static PyObject *pcc_dict_clone(PyObject *src) {
    PyObject *out = py_dict_new();
    if (out == NULL) return NULL;
    if (src != NULL && src != py_None) {
        if (py_type_of(src) != PY_TYPE_DICT) {
            py_decref(out);
            py_raise(py_exc_new(PY_EXC_TYPEERROR, "kwargs base must be dict"));
            return NULL;
        }
        py_dict_update(out, src);
    }
    return out;
}

PyObject *py_call_merge_kwargs(PyObject *base_kwargs, PyObject *star_kwargs) {
    PyObject *out = pcc_dict_clone(base_kwargs);
    if (out == NULL) return NULL;
    if (star_kwargs == NULL || star_kwargs == py_None) {
        return out;
    }
    if (py_type_of(star_kwargs) != PY_TYPE_DICT) {
        py_decref(out);
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "**kwargs must be dict"));
        return NULL;
    }
    py_dict_update(out, star_kwargs);
    return out;
}

PyObject *py_obj_call_splat(PyObject *callable,
                            PyObject *base_args,
                            PyObject *star_args,
                            PyObject *base_kwargs,
                            PyObject *star_kwargs) {
    PyObject *args = py_call_merge_posargs(base_args, star_args);
    if (args == NULL) return NULL;
    PyObject *kwargs = py_call_merge_kwargs(base_kwargs, star_kwargs);
    if (kwargs == NULL) {
        py_decref(args);
        return NULL;
    }
    PyObject *out = py_obj_call(callable, args, kwargs);
    py_decref(args);
    py_decref(kwargs);
    return out;
}
