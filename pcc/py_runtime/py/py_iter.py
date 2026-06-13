"""pcc-Python port of py_iter.c.

Minimal native iterator wrapper for list / tuple / str / dict keys plus
dispatch to native generator objects.
"""

from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

py_incref        = extern("py_incref",        (c_ptr,),                 c_void)
py_decref        = extern("py_decref",        (c_ptr,),                 c_void)
py_list_len      = extern("py_list_len",      (c_ptr,),                 c_int64)
py_list_get      = extern("py_list_get",      (c_ptr, c_int64),         c_ptr)
py_tuple_len     = extern("py_tuple_len",     (c_ptr,),                 c_int64)
py_tuple_get     = extern("py_tuple_get",     (c_ptr, c_int64),         c_ptr)
py_str_len       = extern("py_str_len",       (c_ptr,),                 c_int64)
py_str_index     = extern("py_str_index",     (c_ptr, c_ptr),           c_ptr)
py_bytes_len     = extern("py_bytes_len",     (c_ptr,),                 c_int64)
py_bytes_getitem = extern("py_bytes_getitem", (c_ptr, c_ptr),           c_ptr)
py_int_from_i64  = extern("py_int_from_i64",  (c_int64,),               c_ptr)
py_dict_keys     = extern("py_dict_keys",     (c_ptr,),                 c_ptr)
py_set_items     = extern("py_set_items",     (c_ptr,),                 c_ptr)
py_gen_next      = extern("py_gen_next",      (c_ptr,),                 c_ptr)
py_tuple_new     = extern("py_tuple_new",     (c_int64,),               c_ptr)
py_tuple_set_item= extern("py_tuple_set_item",(c_ptr, c_int64, c_ptr),  c_void)
py_tuple_get     = extern("py_tuple_get",     (c_ptr, c_int64),         c_ptr)
py_obj_call      = extern("py_obj_call",      (c_ptr, c_ptr, c_ptr),    c_ptr)
py_obj_eq        = extern("py_obj_eq",        (c_ptr, c_ptr),           c_int64)
py_exc_new       = extern("py_exc_new",       (c_int64, c_ptr),         c_ptr)
py_err_occurred  = extern("py_err_occurred",  (),                       c_int64)
py_raise_owned   = extern("py_raise_owned",   (c_ptr,),                 c_void)
py_gc_track      = extern("py_gc_track",      (c_ptr,),                 c_void)
pcc_gc_load_ptr  = extern("pcc_gc_load_ptr",  (c_ptr, c_ptr),           c_ptr)
py_user_iter_dispatch = extern("py_user_iter_dispatch", (c_ptr,),       c_ptr)
py_user_next_dispatch = extern("py_user_next_dispatch", (c_ptr,),       c_ptr)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_capi_cext_object_iter = extern(
    "pcc_capi_cext_object_iter", (c_ptr,), c_ptr
)
pcc_capi_cext_object_next = extern(
    "pcc_capi_cext_object_next", (c_ptr,), c_ptr
)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2
    return load_i32(obj, 8)


def _iter_new(seq):
    if ptr_is_null(seq):
        return null()
    it = malloc(32)
    if ptr_is_null(it):
        return null()
    store_i64(it, 0, 1)
    store_i32(it, 8, 14)     # PY_TYPE_ITER
    store_i32(it, 12, 0)
    py_incref(seq)
    store_ptr(it, 16, seq)
    store_i64(it, 24, 0)
    py_gc_track(it)
    return it


@c_abi_export("py_iter_callable_new")
def py_iter_callable_new(callable, sentinel):
    # iter(callable, sentinel): reuse PY_TYPE_ITER. The single ``seq`` slot
    # holds a 2-tuple (callable, sentinel); ``index`` is a negative state
    # discriminator that never collides with a sequence iterator's index.
    if ptr_is_null(callable) or ptr_is_null(sentinel):
        return null()
    pair = py_tuple_new(2)
    if ptr_is_null(pair):
        return null()
    py_tuple_set_item(pair, 0, callable)
    py_tuple_set_item(pair, 1, sentinel)
    it = malloc(32)
    if ptr_is_null(it):
        py_decref(pair)
        return null()
    store_i64(it, 0, 1)
    store_i32(it, 8, 14)     # PY_TYPE_ITER
    store_i32(it, 12, 0)
    store_ptr(it, 16, pair)  # owned reference; dealloc decrefs it
    store_i64(it, 24, -1)    # PY_ITER_CALLABLE_ACTIVE
    py_gc_track(it)
    return it


@c_abi_export("py_dealloc_iter")
def py_dealloc_iter(o) -> None:
    seq = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(seq):
        py_decref(seq)
    free(o)


@c_abi_export("py_obj_iter")
def py_obj_iter(o):
    if ptr_is_null(o):
        return null()
    tag: int = _type_of(o)
    if tag == 14 or tag == 15:       # PY_TYPE_ITER / PY_TYPE_GEN
        py_incref(o)
        return o
    if (
        tag == 5
        or tag == 7
        or tag == 4
        or tag == 17
        or tag == 18
        or tag == 19
    ):                                      # list / tuple / str / bytes-like
        return _iter_new(o)
    if tag == 6:                          # dict -> keys iterator
        keys = py_dict_keys(o)
        if ptr_is_null(keys):
            return null()
        it = _iter_new(keys)
        py_decref(keys)
        return it
    if tag == 8:                          # set -> snapshot item list
        items = py_set_items(o)
        if ptr_is_null(items):
            return null()
        it = _iter_new(items)
        py_decref(items)
        return it
    if pcc_capi_is_cext_type_tag(tag) != 0:
        it = pcc_capi_cext_object_iter(o)
        if ptr_is_null(it) == 0 or py_err_occurred() != 0:
            return it
    dunder = py_user_iter_dispatch(o)
    if not ptr_is_null(dunder) or py_err_occurred() != 0:
        return dunder
    exc = py_exc_new(3, null())       # TypeError
    py_raise_owned(exc)
    return null()


@c_abi_export("py_obj_next")
def py_obj_next(it_obj):
    if not ptr_is_null(it_obj):
        if is_tagged_int(it_obj) == 0:
            tag: int = load_i32(it_obj, 8)
            if tag == 15:      # PY_TYPE_GEN
                return py_gen_next(it_obj)
            if pcc_capi_is_cext_type_tag(tag) != 0:
                item = pcc_capi_cext_object_next(it_obj)
                if ptr_is_null(item) == 0 or py_err_occurred() != 0:
                    return item
    if ptr_is_null(it_obj) or _type_of(it_obj) != 14:
        dunder = py_user_next_dispatch(it_obj)
        if not ptr_is_null(dunder) or py_err_occurred() != 0:
            return dunder
        exc = py_exc_new(3, null())            # TypeError
        py_raise_owned(exc)
        return null()

    index: int = load_i64(it_obj, 24)
    if index < 0:
        # Callable-iterator: iter(callable, sentinel).
        if index == -2:                       # PY_ITER_CALLABLE_DONE
            exc = py_exc_new(8, null())        # StopIteration
            py_raise_owned(exc)
            return null()
        pair = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        callable = py_tuple_get(pair, 0)
        sentinel = py_tuple_get(pair, 1)
        args = py_tuple_new(0)
        none_obj = global_load_ptr("py_None")
        result = py_obj_call(callable, args, none_obj)
        py_decref(args)
        py_decref(callable)
        if ptr_is_null(result):
            py_decref(sentinel)
            return null()
        is_stop: int = py_obj_eq(result, sentinel)
        py_decref(sentinel)
        if is_stop != 0:
            py_decref(result)
            store_i64(it_obj, 24, -2)          # PY_ITER_CALLABLE_DONE
            exc = py_exc_new(8, null())        # StopIteration
            py_raise_owned(exc)
            return null()
        return result

    seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
    tag: int = _type_of(seq)
    n: int = 0
    item = null()
    if tag == 5:
        n = py_list_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())        # StopIteration
            py_raise_owned(exc)
            return null()
        item = py_list_get(seq, index)
    elif tag == 7:
        n = py_tuple_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        item = py_tuple_get(seq, index)
    elif tag == 4:
        n = py_str_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        idx = py_int_from_i64(index)
        item = py_str_index(seq, idx)
        py_decref(idx)
    elif tag == 17 or tag == 18 or tag == 19:
        n = py_bytes_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        idx = py_int_from_i64(index)
        item = py_bytes_getitem(seq, idx)
        py_decref(idx)
    else:
        exc = py_exc_new(3, null())
        py_raise_owned(exc)
        return null()
    store_i64(it_obj, 24, index + 1)
    return item
