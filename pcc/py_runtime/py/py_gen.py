"""pcc-Python port of py_gen.c."""

from pcc.extern import extern, c_abi_export, c_int32, c_ptr, c_int64, c_void
from pcc.unsafe import (
    call_ptr2,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i64,
    store_ptr,
)

py_incref   = extern("py_incref",   (c_ptr,),         c_void)
py_decref   = extern("py_decref",   (c_ptr,),         c_void)
py_exc_new  = extern("py_exc_new",  (c_int64, c_ptr), c_ptr)
py_exc_new_with_value = extern("py_exc_new_with_value", (c_int64, c_ptr), c_ptr)
py_raise    = extern("py_raise",    (c_ptr,),         c_void)
py_raise = extern("py_raise", (c_ptr,),        c_void)
py_coroutine_run = extern("py_coroutine_run", (c_ptr,), c_ptr)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception   = extern("py_clear_exception",   (), c_void)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches       = extern("py_exc_matches",       (c_ptr, c_ptr), c_int64)
py_gc_track          = extern("py_gc_track",          (c_ptr,),         c_void)
pcc_gc_alloc         = extern("pcc_gc_alloc",         (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr     = extern("pcc_gc_store_ptr",     (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)


@c_abi_export("py_gen_new")
def py_gen_new(resume, frame):
    if ptr_is_null(resume) or ptr_is_null(frame):
        return null()
    g = pcc_gc_alloc(56, 15, 0)
    if ptr_is_null(g):
        return null()
    store_ptr(g, 16, resume)
    store_ptr(g, 24, null())
    store_i64(g, 32, 0)       # state
    store_i64(g, 40, 0)       # done
    store_ptr(g, 48, null())   # pending send value
    pcc_gc_store_ptr(g, ptr_add(g, 24), frame)
    pcc_gc_store_ptr(g, ptr_add(g, 48), global_load_ptr("py_None"))
    py_gc_track(g)
    return g


@c_abi_export("py_dealloc_gen")
def py_dealloc_gen(o) -> None:
    frame = pcc_gc_load_ptr(o, ptr_add(o, 24))
    if not ptr_is_null(frame):
        py_decref(frame)
    send_value = pcc_gc_load_ptr(o, ptr_add(o, 48))
    if not ptr_is_null(send_value):
        py_decref(send_value)
    pcc_gc_free_object_memory(o)


def _checked_gen(gen):
    if ptr_is_null(gen):
        exc = py_exc_new(3, null())
        py_raise(exc)
        return null()
    if is_tagged_int(gen):
        exc = py_exc_new(3, null())
        py_raise(exc)
        return null()
    if load_i32(gen, 8) != 15:
        exc = py_exc_new(3, null())
        py_raise(exc)
        return null()
    return gen


def _set_send_value(gen, value) -> None:
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    pcc_gc_store_ptr(gen, ptr_add(gen, 48), value)


@c_abi_export("py_gen_state")
def py_gen_state(gen) -> int:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return -1
    return load_i64(gen, 32)


@c_abi_export("py_gen_set_state")
def py_gen_set_state(gen, state: int) -> None:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return
    store_i64(gen, 32, state)


@c_abi_export("py_gen_set_done")
def py_gen_set_done(gen) -> None:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return
    store_i64(gen, 40, 1)


@c_abi_export("py_gen_is_done")
def py_gen_is_done(gen) -> int:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return 1
    if load_i64(gen, 40) != 0:
        return 1
    return 0


@c_abi_export("py_gen_finish")
def py_gen_finish(gen, value):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    store_i64(gen, 40, 1)
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    stop = py_exc_new_with_value(8, value)       # PY_EXC_STOPITERATION
    py_raise(stop)
    return null()


@c_abi_export("py_gen_next")
def py_gen_next(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        exc = py_exc_new(8, null())
        py_raise(exc)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if ptr_is_null(resume):
        exc = py_exc_new(8, null())
        py_raise(exc)
        return null()
    _set_send_value(gen, global_load_ptr("py_None"))
    return call_ptr2(resume, gen, frame)


@c_abi_export("py_gen_send")
def py_gen_send(gen, value):
    if not ptr_is_null(gen):
        if is_tagged_int(gen) == 0:
            if load_i32(gen, 8) == 20:       # PY_TYPE_COROUTINE
                none = global_load_ptr("py_None")
                if not ptr_is_null(value):
                    if ptr_eq(value, none) == 0:
                        exc = py_exc_new(3, null())
                        py_raise(exc)
                        return null()
                return py_coroutine_run(gen)
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        exc = py_exc_new(8, null())
        py_raise(exc)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if ptr_is_null(resume):
        exc = py_exc_new(8, null())
        py_raise(exc)
        return null()
    none = global_load_ptr("py_None")
    if load_i64(gen, 32) == 0 and not ptr_is_null(value):
        if ptr_eq(value, none) == 0:
            exc = py_exc_new(3, null())
            py_raise(exc)
            return null()
    _set_send_value(gen, value)
    return call_ptr2(resume, gen, frame)


@c_abi_export("py_gen_throw")
def py_gen_throw(gen, exc):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        stop = py_exc_new(8, null())
        py_raise(stop)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if ptr_is_null(resume):
        stop = py_exc_new(8, null())
        py_raise(stop)
        return null()
    _set_send_value(gen, global_load_ptr("py_None"))
    py_raise(exc)
    return call_ptr2(resume, gen, frame)


@c_abi_export("py_gen_close")
def py_gen_close(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) == 0:
        resume = load_ptr(gen, 16)
        frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
        if not ptr_is_null(resume):
            exc = py_exc_new(0, null())       # GeneratorExit ~= BaseException
            _set_send_value(gen, global_load_ptr("py_None"))
            py_raise(exc)
            result = call_ptr2(resume, gen, frame)
            if not ptr_is_null(result):
                py_decref(result)
                store_i64(gen, 40, 1)
                runtime_error = py_exc_new(7, null())
                py_raise(runtime_error)
                return null()
            cur = py_current_exception()
            stop_cls = py_exc_builtin_class(8)
            if py_exc_matches(cur, stop_cls) != 0:
                py_clear_exception()
                store_i64(gen, 40, 1)
            elif ptr_eq(cur, exc) != 0:
                # Our injected GeneratorExit propagated back unhandled:
                # that IS the normal close path in CPython — swallow it.
                # Any OTHER exception from the body keeps propagating.
                py_clear_exception()
                store_i64(gen, 40, 1)
            else:
                return null()
    none = global_load_ptr("py_None")
    py_incref(none)
    return none


@c_abi_export("py_gen_take_send")
def py_gen_take_send(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    none = global_load_ptr("py_None")
    value = pcc_gc_load_ptr(gen, ptr_add(gen, 48))
    if ptr_is_null(value):
        value = none
    py_incref(value)
    pcc_gc_store_ptr(gen, ptr_add(gen, 48), none)
    return value
