"""pcc-Python mirror of py_threading.c.

This module keeps the no-C runtime archive linkable.  The heavy lifting stays
in pcc_threads.c through extern wrappers; Python-level stdlib shims call these
ABI symbols rather than embedding pthread details in layer1.
"""
from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import load_i64, load_ptr, null, ptr_add, ptr_is_null, store_i32, store_i64, store_ptr

pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_mutex_new = extern("pcc_mutex_new", (), c_ptr)
pcc_mutex_free = extern("pcc_mutex_free", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
pcc_cond_new = extern("pcc_cond_new", (), c_ptr)
pcc_cond_free = extern("pcc_cond_free", (c_ptr,), c_void)
pcc_cond_wait = extern("pcc_cond_wait", (c_ptr, c_ptr), c_int64)
pcc_cond_signal = extern("pcc_cond_signal", (c_ptr,), c_int64)
pcc_cond_broadcast = extern("pcc_cond_broadcast", (c_ptr,), c_int64)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_incref_extern = extern("py_incref", (c_ptr,), c_void)
py_decref_extern = extern("py_decref", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)


def _alloc_obj(type_tag: int, size: int):
    return pcc_gc_alloc(size, type_tag, 0)


@c_abi_export("py_threading_get_ident")
def py_threading_get_ident() -> int:
    return pcc_current_thread_id()


@c_abi_export("py_threading_current_thread")
def py_threading_current_thread():
    return py_int_from_i64(pcc_current_thread_id())


@c_abi_export("py_threading_lock_new")
def py_threading_lock_new():
    o = _alloc_obj(22, 24)
    if ptr_is_null(o):
        return o
    m = pcc_mutex_new()
    if ptr_is_null(m):
        pcc_gc_free_object_memory(o)
        return null()
    store_ptr(o, 16, m)
    return o


@c_abi_export("py_threading_lock_acquire")
def py_threading_lock_acquire(lock) -> int:
    if ptr_is_null(lock):
        return -1
    return pcc_mutex_lock(load_ptr(lock, 16))


@c_abi_export("py_threading_lock_acquire_vthread")
def py_threading_lock_acquire_vthread(lock) -> int:
    return py_threading_lock_acquire(lock)


@c_abi_export("py_threading_lock_release")
def py_threading_lock_release(lock) -> int:
    if ptr_is_null(lock):
        return -1
    return pcc_mutex_unlock(load_ptr(lock, 16))


@c_abi_export("py_dealloc_thread_lock")
def py_dealloc_thread_lock(lock) -> None:
    if ptr_is_null(lock):
        return
    pcc_mutex_free(load_ptr(lock, 16))
    pcc_gc_free_object_memory(lock)


@c_abi_export("py_threading_rlock_new")
def py_threading_rlock_new():
    return py_threading_lock_new()


@c_abi_export("py_threading_rlock_acquire")
def py_threading_rlock_acquire(lock) -> int:
    return py_threading_lock_acquire(lock)


@c_abi_export("py_threading_rlock_release")
def py_threading_rlock_release(lock) -> int:
    return py_threading_lock_release(lock)


@c_abi_export("py_dealloc_thread_rlock")
def py_dealloc_thread_rlock(lock) -> None:
    py_dealloc_thread_lock(lock)


@c_abi_export("py_threading_event_new")
def py_threading_event_new():
    o = _alloc_obj(24, 40)
    if ptr_is_null(o):
        return o
    m = pcc_mutex_new()
    c = pcc_cond_new()
    if ptr_is_null(m) or ptr_is_null(c):
        pcc_mutex_free(m)
        pcc_cond_free(c)
        pcc_gc_free_object_memory(o)
        return null()
    store_ptr(o, 16, m)
    store_ptr(o, 24, c)
    store_i64(o, 32, 0)
    return o


@c_abi_export("py_threading_event_set")
def py_threading_event_set(event) -> int:
    if ptr_is_null(event):
        return -1
    m = load_ptr(event, 16)
    c = load_ptr(event, 24)
    if pcc_mutex_lock(m) != 0:
        return -1
    store_i64(event, 32, 1)
    pcc_cond_broadcast(c)
    return pcc_mutex_unlock(m)


@c_abi_export("py_threading_event_clear")
def py_threading_event_clear(event) -> int:
    if ptr_is_null(event):
        return -1
    m = load_ptr(event, 16)
    if pcc_mutex_lock(m) != 0:
        return -1
    store_i64(event, 32, 0)
    return pcc_mutex_unlock(m)


@c_abi_export("py_threading_event_is_set")
def py_threading_event_is_set(event) -> int:
    if ptr_is_null(event):
        return 0
    return 1 if load_i64(event, 32) != 0 else 0


@c_abi_export("py_threading_event_wait")
def py_threading_event_wait(event) -> int:
    if ptr_is_null(event):
        return -1
    m = load_ptr(event, 16)
    c = load_ptr(event, 24)
    if pcc_mutex_lock(m) != 0:
        return -1
    while load_i64(event, 32) == 0:
        if pcc_cond_wait(c, m) != 0:
            break
    return pcc_mutex_unlock(m)


@c_abi_export("py_threading_event_wait_vthread")
def py_threading_event_wait_vthread(event) -> int:
    return py_threading_event_wait(event)


@c_abi_export("py_dealloc_thread_event")
def py_dealloc_thread_event(event) -> None:
    if ptr_is_null(event):
        return
    pcc_cond_free(load_ptr(event, 24))
    pcc_mutex_free(load_ptr(event, 16))
    pcc_gc_free_object_memory(event)


@c_abi_export("py_threading_condition_new")
def py_threading_condition_new(lock):
    return py_threading_event_new()


@c_abi_export("py_threading_condition_acquire")
def py_threading_condition_acquire(cond) -> int:
    if ptr_is_null(cond):
        return -1
    return pcc_mutex_lock(load_ptr(cond, 16))


@c_abi_export("py_threading_condition_release")
def py_threading_condition_release(cond) -> int:
    if ptr_is_null(cond):
        return -1
    return pcc_mutex_unlock(load_ptr(cond, 16))


@c_abi_export("py_threading_condition_wait")
def py_threading_condition_wait(cond) -> int:
    if ptr_is_null(cond):
        return -1
    return pcc_cond_wait(load_ptr(cond, 24), load_ptr(cond, 16))


@c_abi_export("py_threading_condition_wait_vthread")
def py_threading_condition_wait_vthread(cond) -> int:
    return py_threading_condition_wait(cond)


@c_abi_export("py_threading_condition_notify")
def py_threading_condition_notify(cond) -> int:
    if ptr_is_null(cond):
        return -1
    return pcc_cond_signal(load_ptr(cond, 24))


@c_abi_export("py_dealloc_thread_condition")
def py_dealloc_thread_condition(cond) -> None:
    py_dealloc_thread_event(cond)


@c_abi_export("py_threading_semaphore_new")
def py_threading_semaphore_new(initial: int):
    o = py_threading_event_new()
    if ptr_is_null(o) == 0:
        store_i32(o, 8, 26)
        store_i64(o, 32, initial)
    return o


@c_abi_export("py_threading_semaphore_acquire")
def py_threading_semaphore_acquire(sem) -> int:
    if ptr_is_null(sem):
        return -1
    m = load_ptr(sem, 16)
    c = load_ptr(sem, 24)
    if pcc_mutex_lock(m) != 0:
        return -1
    while load_i64(sem, 32) <= 0:
        if pcc_cond_wait(c, m) != 0:
            break
    v: int = load_i64(sem, 32)
    if v > 0:
        store_i64(sem, 32, v - 1)
    return pcc_mutex_unlock(m)


@c_abi_export("py_threading_semaphore_acquire_vthread")
def py_threading_semaphore_acquire_vthread(sem) -> int:
    return py_threading_semaphore_acquire(sem)


@c_abi_export("py_threading_semaphore_release")
def py_threading_semaphore_release(sem) -> int:
    if ptr_is_null(sem):
        return -1
    m = load_ptr(sem, 16)
    c = load_ptr(sem, 24)
    if pcc_mutex_lock(m) != 0:
        return -1
    store_i64(sem, 32, load_i64(sem, 32) + 1)
    pcc_cond_signal(c)
    return pcc_mutex_unlock(m)


@c_abi_export("py_dealloc_thread_semaphore")
def py_dealloc_thread_semaphore(sem) -> None:
    py_dealloc_thread_event(sem)


@c_abi_export("py_threading_thread_new")
def py_threading_thread_new(callable, args):
    # Layout mirrors the C PyThreadObject enough for the no-C archive:
    # header, handle, callable, args, result, started, joined, finished.
    o = _alloc_obj(27, 72)
    if ptr_is_null(o):
        return o
    store_ptr(o, 16, null())
    store_ptr(o, 24, null())
    store_ptr(o, 32, null())
    store_ptr(o, 40, null())
    pcc_gc_store_ptr(o, ptr_add(o, 24), callable)
    pcc_gc_store_ptr(o, ptr_add(o, 32), args)
    store_i64(o, 48, 0)
    store_i64(o, 56, 0)
    store_i64(o, 64, 0)
    return o


@c_abi_export("py_threading_thread_start")
def py_threading_thread_start(thread) -> int:
    if ptr_is_null(thread):
        return -1
    # No real pthread dispatch in the pcc-Python archive; run the target
    # synchronously so simple Thread programs work in libpy_runtime_pcc_py.a.
    store_i64(thread, 48, 1)
    callable_obj = pcc_gc_load_ptr(thread, ptr_add(thread, 24))
    args_obj = pcc_gc_load_ptr(thread, ptr_add(thread, 32))
    if ptr_is_null(callable_obj) == 0:
        result = py_obj_call(callable_obj, args_obj, null())
        pcc_gc_store_ptr(thread, ptr_add(thread, 40), result)
        py_decref_extern(result)
    store_i64(thread, 56, 1)
    store_i64(thread, 64, 1)
    return 0


@c_abi_export("py_threading_thread_join")
def py_threading_thread_join(thread) -> int:
    return 0 if ptr_is_null(thread) == 0 else -1


@c_abi_export("py_threading_thread_is_alive")
def py_threading_thread_is_alive(thread) -> int:
    if ptr_is_null(thread):
        return 0
    return 1 if load_i64(thread, 48) != 0 and load_i64(thread, 56) == 0 and load_i64(thread, 64) == 0 else 0


@c_abi_export("py_dealloc_thread_thread")
def py_dealloc_thread_thread(thread) -> None:
    if ptr_is_null(thread):
        return
    py_decref_extern(pcc_gc_load_ptr(thread, ptr_add(thread, 24)))  # callable
    py_decref_extern(pcc_gc_load_ptr(thread, ptr_add(thread, 32)))  # args
    py_decref_extern(pcc_gc_load_ptr(thread, ptr_add(thread, 40)))  # result
    pcc_gc_free_object_memory(thread)
