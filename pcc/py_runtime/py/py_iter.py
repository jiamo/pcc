"""pcc-Python port of py_iter.c.

Minimal native iterator wrapper for list / tuple / str / dict keys plus
dispatch to native generator objects.
"""
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_DICT,
    PY_TYPE_FILE,
    PY_TYPE_GEN,
    PY_TYPE_INT,
    PY_TYPE_ITER,
    PY_TYPE_LIST,
    PY_TYPE_MEMORYVIEW,
    PY_TYPE_SET,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
)

from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
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
py_list_new      = extern("py_list_new",      (c_int64,),               c_ptr)
py_list_append   = extern("py_list_append",   (c_ptr, c_ptr),           c_void)
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
py_file_readline = extern("py_file_readline", (c_ptr, c_int64),         c_ptr)
py_tuple_new     = extern("py_tuple_new",     (c_int64,),               c_ptr)
py_tuple_set_item= extern("py_tuple_set_item",(c_ptr, c_int64, c_ptr),  c_void)
py_tuple_get     = extern("py_tuple_get",     (c_ptr, c_int64),         c_ptr)
py_obj_call      = extern("py_obj_call",      (c_ptr, c_ptr, c_ptr),    c_ptr)
py_obj_eq        = extern("py_obj_eq",        (c_ptr, c_ptr),           c_int64)
py_exc_new       = extern("py_exc_new",       (c_int64, c_ptr),         c_ptr)
py_err_occurred  = extern("py_err_occurred",  (),                       c_int64)
py_clear_exception = extern("py_clear_exception", (),                    c_void)
py_current_exception = extern("py_current_exception", (),                c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,),         c_ptr)
py_exc_matches   = extern("py_exc_matches",   (c_ptr, c_ptr),           c_int64)
py_raise_owned   = extern("py_raise_owned",   (c_ptr,),                 c_void)
py_gc_track      = extern("py_gc_track",      (c_ptr,),                 c_void)
pcc_gc_alloc     = extern("pcc_gc_alloc",     (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern(
    "pcc_gc_free_object_memory", (c_ptr,), c_void
)
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
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)


def _require_result(result, helper_name, message):
    if ptr_is_null(result):
        py_runtime_error_if_unset(helper_name, message)
    return result


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT
    return load_i32(obj, 8)


def _iter_new(seq):
    if ptr_is_null(seq):
        return null()
    it = pcc_gc_alloc(32, PY_TYPE_ITER, 0)
    if ptr_is_null(it):
        return null()
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
        return _require_result(
            null(),
            cstr("py_iter_callable_new"),
            cstr("iter(callable, sentinel) received NULL operand"),
        )
    pair = py_tuple_new(2)
    if ptr_is_null(pair):
        return _require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("iter(callable, sentinel) could not allocate its state tuple"),
        )
    py_tuple_set_item(pair, 0, callable)
    py_tuple_set_item(pair, 1, sentinel)
    it = pcc_gc_alloc(32, PY_TYPE_ITER, 0)
    if ptr_is_null(it):
        _require_result(
            null(),
            cstr("py_iter_callable_new"),
            cstr("iter(callable, sentinel) could not allocate iterator state"),
        )
        py_decref(pair)
        return null()
    store_ptr(it, 16, pair)  # owned reference; dealloc decrefs it
    store_i64(it, 24, -1)    # PY_ITER_CALLABLE_ACTIVE
    py_gc_track(it)
    return it


@c_abi_export("py_dealloc_iter")
def py_dealloc_iter(o) -> None:
    seq = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(seq):
        py_decref(seq)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_obj_iter")
def py_obj_iter(o):
    if ptr_is_null(o):
        return _require_result(
            null(),
            cstr("py_obj_iter"),
            cstr("py_obj_iter received NULL object"),
        )
    tag: int = _type_of(o)
    if (
        tag == PY_TYPE_ITER
        or tag == PY_TYPE_GEN
        or tag == PY_TYPE_FILE
    ):
        py_incref(o)
        return o
    if (
        tag == PY_TYPE_LIST
        or tag == PY_TYPE_TUPLE
        or tag == PY_TYPE_STR
        or tag == PY_TYPE_BYTES
        or tag == PY_TYPE_BYTEARRAY
        or tag == PY_TYPE_MEMORYVIEW
    ):                                      # list / tuple / str / bytes-like
        return _require_result(
            _iter_new(o),
            cstr("py_iter_new"),
            cstr("sequence iterator allocation failed without setting an exception"),
        )
    if tag == PY_TYPE_DICT:                          # dict -> keys iterator
        keys = py_dict_keys(o)
        if ptr_is_null(keys):
            return _require_result(
                null(),
                cstr("py_dict_keys"),
                cstr("dictionary iterator snapshot failed without setting an exception"),
            )
        it = _iter_new(keys)
        if ptr_is_null(it):
            _require_result(
                null(),
                cstr("py_iter_new"),
                cstr("dictionary iterator allocation failed without setting an exception"),
            )
        py_decref(keys)
        return it
    if tag == PY_TYPE_SET:                          # set -> snapshot item list
        items = py_set_items(o)
        if ptr_is_null(items):
            return _require_result(
                null(),
                cstr("py_set_items"),
                cstr("set iterator snapshot failed without setting an exception"),
            )
        it = _iter_new(items)
        if ptr_is_null(it):
            _require_result(
                null(),
                cstr("py_iter_new"),
                cstr("set iterator allocation failed without setting an exception"),
            )
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
            if tag == PY_TYPE_GEN:      # PY_TYPE_GEN
                return _require_result(
                    py_gen_next(it_obj),
                    cstr("py_gen_next"),
                    cstr(
                        "generator next returned NULL without StopIteration or an exception"
                    ),
                )
            if tag == PY_TYPE_FILE:
                line = py_file_readline(it_obj, -1)
                if ptr_is_null(line):
                    return _require_result(
                        null(),
                        cstr("py_file_readline"),
                        cstr(
                            "file iterator readline returned NULL without an exception"
                        ),
                    )
                line_tag: int = _type_of(line)
                line_length: int = -1
                if line_tag == PY_TYPE_STR:
                    line_length = py_str_len(line)
                elif line_tag == PY_TYPE_BYTES:
                    line_length = py_bytes_len(line)
                if line_length == 0:
                    py_decref(line)
                    exc = py_exc_new(8, null())  # StopIteration
                    py_raise_owned(exc)
                    return null()
                if line_length < 0:
                    py_decref(line)
                    return _require_result(
                        null(),
                        cstr("py_file_readline"),
                        cstr("file iterator readline returned a non-line object"),
                    )
                return line
            if pcc_capi_is_cext_type_tag(tag) != 0:
                item = pcc_capi_cext_object_next(it_obj)
                if ptr_is_null(item) == 0 or py_err_occurred() != 0:
                    return item
    if ptr_is_null(it_obj) or _type_of(it_obj) != PY_TYPE_ITER:
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
        if ptr_is_null(callable):
            return _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("callable iterator lost its callable"),
            )
        sentinel = py_tuple_get(pair, 1)
        if ptr_is_null(sentinel):
            _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("callable iterator lost its sentinel"),
            )
            py_decref(callable)
            return null()
        args = py_tuple_new(0)
        if ptr_is_null(args):
            _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("callable iterator could not allocate its argument tuple"),
            )
            py_decref(callable)
            py_decref(sentinel)
            return null()
        none_obj = global_load_ptr("py_None")
        result = py_obj_call(callable, args, none_obj)
        if ptr_is_null(result):
            _require_result(
                null(),
                cstr("py_obj_call"),
                cstr("callable iterator returned NULL without setting an exception"),
            )
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
    if tag == PY_TYPE_LIST:
        n = py_list_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())        # StopIteration
            py_raise_owned(exc)
            return null()
        item = py_list_get(seq, index)
    elif tag == PY_TYPE_TUPLE:
        n = py_tuple_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        item = py_tuple_get(seq, index)
    elif tag == PY_TYPE_STR:
        n = py_str_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        idx = py_int_from_i64(index)
        if ptr_is_null(idx):
            return _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("string iterator could not allocate its index"),
            )
        item = py_str_index(seq, idx)
        py_decref(idx)
    elif tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY or tag == PY_TYPE_MEMORYVIEW:
        n = py_bytes_len(seq)
        if index >= n:
            exc = py_exc_new(8, null())
            py_raise_owned(exc)
            return null()
        idx = py_int_from_i64(index)
        if ptr_is_null(idx):
            return _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("bytes iterator could not allocate its index"),
            )
        item = py_bytes_getitem(seq, idx)
        py_decref(idx)
    else:
        exc = py_exc_new(3, null())
        py_raise_owned(exc)
        return null()
    if ptr_is_null(item):
        return _require_result(
            null(),
            cstr("py_obj_next"),
            cstr("iterator element lookup returned NULL without setting an exception"),
        )
    store_i64(it_obj, 24, index + 1)
    return item


@c_abi_export("py_enumerate_list")
def py_enumerate_list(iterable, start: int):
    it = py_obj_iter(iterable)
    if ptr_is_null(it) != 0:
        return null()
    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        _require_result(
            null(),
            cstr("py_list_new"),
            cstr("enumerate could not allocate its result list"),
        )
        py_decref(it)
        return null()

    index: int = start
    done: int = 0
    while done == 0:
        item = py_obj_next(it)
        if ptr_is_null(item) != 0:
            if py_err_occurred() != 0:
                current = py_current_exception()
                stop = py_exc_builtin_class(8)  # PY_EXC_STOPITERATION
                if py_exc_matches(current, stop) != 0:
                    py_clear_exception()
                else:
                    py_decref(it)
                    py_decref(out)
                    return null()
            done = 1
        else:
            pair = py_tuple_new(2)
            if ptr_is_null(pair) != 0:
                _require_result(
                    null(),
                    cstr("py_tuple_new"),
                    cstr("enumerate could not allocate an output pair"),
                )
                py_decref(item)
                py_decref(it)
                py_decref(out)
                return null()
            index_obj = py_int_from_i64(index)
            if ptr_is_null(index_obj) != 0:
                _require_result(
                    null(),
                    cstr("py_int_from_i64"),
                    cstr("enumerate could not allocate an index object"),
                )
                py_decref(item)
                py_decref(pair)
                py_decref(it)
                py_decref(out)
                return null()
            py_tuple_set_item(pair, 0, index_obj)
            py_decref(index_obj)
            py_tuple_set_item(pair, 1, item)
            py_decref(item)
            py_list_append(out, pair)
            if py_err_occurred() != 0:
                py_decref(pair)
                py_decref(it)
                py_decref(out)
                return null()
            py_decref(pair)
            index = index + 1

    py_decref(it)
    return out
