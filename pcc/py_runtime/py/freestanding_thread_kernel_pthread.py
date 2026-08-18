"""pthread-backed runtime kernel authored in pcc-Python.

Selected only for an explicit ``PCC_WITH_THREADS=1`` runtime archive.  The
module calls the named pthread ABI directly and keeps the stop-the-world,
thread-registration, join/detach, mutex, condition, and atomic-refcount
contracts formerly mixed into ``pcc_threads.c``.
"""

__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_load_i32,
    atomic_cas_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    call_ptr1,
    define_global_i32,
    define_global_i64,
    define_global_ptr_null,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    free,
    function_addr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


define_global_i32("pcc_thread_stop_requested", 0)
define_thread_local_i32("pcc_native_thread_identity_token", 0)
define_thread_local_ptr_null("pcc_tls_thread_id_py")
define_thread_local_i32("pcc_tls_thread_parked_py", 0)
define_thread_local_i32("pcc_tls_no_park_depth_py", 0)
define_thread_local_i32("pcc_tls_unregister_in_progress_py", 0)
define_thread_local_ptr_null("pcc_tls_parked_epoch_py")
define_global_ptr_null("pcc_world_lock_py")
define_global_ptr_null("pcc_world_cond_py")
define_global_i32("pcc_world_init_state_py", 0)
define_global_i64("pcc_next_thread_id_py", 1)
define_global_i64("pcc_live_thread_count_py", 0)
define_global_i64("pcc_parked_thread_count_py", 0)
define_global_i64("pcc_stop_owner_thread_id_py", 0)
define_global_i64("pcc_stop_epoch_py", 0)
define_global_i64("pcc_stop_depth_py", 0)
define_global_i64("pcc_registration_waiter_count_py", 0)


pthread_create = extern(
    "pthread_create", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)
pthread_join = extern("pthread_join", (c_ptr, c_ptr), c_int64)
pthread_detach = extern("pthread_detach", (c_ptr,), c_int64)
pthread_mutex_init = extern("pthread_mutex_init", (c_ptr, c_ptr), c_int64)
pthread_mutex_destroy = extern("pthread_mutex_destroy", (c_ptr,), c_int64)
pthread_mutex_trylock = extern("pthread_mutex_trylock", (c_ptr,), c_int64)
pthread_mutex_lock = extern("pthread_mutex_lock", (c_ptr,), c_int64)
pthread_mutex_unlock = extern("pthread_mutex_unlock", (c_ptr,), c_int64)
pthread_cond_init = extern("pthread_cond_init", (c_ptr, c_ptr), c_int64)
pthread_cond_destroy = extern("pthread_cond_destroy", (c_ptr,), c_int64)
pthread_cond_wait = extern("pthread_cond_wait", (c_ptr, c_ptr), c_int64)
pthread_cond_timedwait = extern(
    "pthread_cond_timedwait", (c_ptr, c_ptr, c_ptr), c_int64
)
pthread_cond_signal = extern("pthread_cond_signal", (c_ptr,), c_int64)
pthread_cond_broadcast = extern("pthread_cond_broadcast", (c_ptr,), c_int64)
sched_yield = extern("sched_yield", (), c_int64)
pcc_platform_wall_time_us = extern("pcc_platform_wall_time_us", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_gc_thread_unregister_buffers = extern(
    "pcc_gc_thread_unregister_buffers", (), c_void
)
py_clear_exception = extern("py_clear_exception", (), c_void)


def _tls_i64(slot) -> int:
    return ptr_to_int(load_ptr(slot, 0))


def _tls_store_i64(slot, value: int) -> None:
    store_ptr(slot, 0, int_to_ptr(value))


def _world_i64(slot) -> int:
    return load_i64(slot, 0)


def _world_store_i64(slot, value: int) -> None:
    store_i64(slot, 0, value)


def _world_init() -> int:
    state = atomic_load_i32(
        global_addr("pcc_world_init_state_py"), 0, "acquire"
    )
    if state == 2:
        return 0
    observed = atomic_cas_i32(
        global_addr("pcc_world_init_state_py"), 0, 0, 1, "acq_rel", "acquire"
    )
    if observed == 0:
        mutex = pcc_mutex_new()
        cond = pcc_cond_new()
        if ptr_is_null(mutex) or ptr_is_null(cond):
            pcc_mutex_free(mutex)
            pcc_cond_free(cond)
            atomic_store_i32(
                global_addr("pcc_world_init_state_py"), 0, 0, "release"
            )
            return -1
        global_store_ptr("pcc_world_lock_py", mutex)
        global_store_ptr("pcc_world_cond_py", cond)
        atomic_store_i32(
            global_addr("pcc_world_init_state_py"), 0, 2, "release"
        )
        return 0
    while atomic_load_i32(
        global_addr("pcc_world_init_state_py"), 0, "acquire"
    ) == 1:
        sched_yield()
    if atomic_load_i32(
        global_addr("pcc_world_init_state_py"), 0, "acquire"
    ) == 2:
        return 0
    return -1


@c_abi_export("pcc_threads_enabled")
def pcc_threads_enabled() -> int:
    return 1


@c_abi_export("pcc_thread_stop_requested_acquire")
def pcc_thread_stop_requested_acquire() -> int:
    return atomic_load_i32(
        global_addr("pcc_thread_stop_requested"), 0, "acquire"
    )


@c_abi_export("pcc_refcount_strategy")
def pcc_refcount_strategy() -> int:
    return 1


@c_abi_export("pcc_current_native_thread_token")
def pcc_current_native_thread_token():
    return global_addr("pcc_native_thread_identity_token")


@c_abi_export("pcc_refcount_incref")
def pcc_refcount_incref(slot) -> int:
    if ptr_is_null(slot):
        return 0
    return atomic_rmw_i64("add", slot, 0, 1, "relaxed") + 1


@c_abi_export("pcc_refcount_decref")
def pcc_refcount_decref(slot) -> int:
    if ptr_is_null(slot):
        return 0
    return atomic_rmw_i64("sub", slot, 0, 1, "acq_rel") - 1


@c_abi_export("pcc_refcount_load")
def pcc_refcount_load(slot) -> int:
    if ptr_is_null(slot):
        return 0
    return atomic_load_i64(slot, 0, "acquire")


@c_abi_export("pcc_refcount_forget")
def pcc_refcount_forget(slot) -> None:
    return


@c_abi_export("pcc_mutex_new")
def pcc_mutex_new():
    mutex = malloc(128)
    if ptr_is_null(mutex):
        return null()
    if pthread_mutex_init(mutex, null()) != 0:
        free(mutex)
        return null()
    return mutex


@c_abi_export("pcc_mutex_free")
def pcc_mutex_free(mutex) -> None:
    if ptr_is_null(mutex):
        return
    pthread_mutex_destroy(mutex)
    free(mutex)


@c_abi_export("pcc_mutex_lock")
def pcc_mutex_lock(mutex) -> int:
    if ptr_is_null(mutex):
        return -1
    while True:
        result = pthread_mutex_trylock(mutex)
        if result == 0:
            return 0
        if result != 16:
            return -1
        pcc_thread_safepoint()
        sched_yield()
    return -1


@c_abi_export("pcc_mutex_unlock")
def pcc_mutex_unlock(mutex) -> int:
    if ptr_is_null(mutex):
        return -1
    if pthread_mutex_unlock(mutex) == 0:
        return 0
    return -1


@c_abi_export("pcc_cond_new")
def pcc_cond_new():
    cond = malloc(128)
    if ptr_is_null(cond):
        return null()
    if pthread_cond_init(cond, null()) != 0:
        free(cond)
        return null()
    return cond


@c_abi_export("pcc_cond_free")
def pcc_cond_free(cond) -> None:
    if ptr_is_null(cond):
        return
    pthread_cond_destroy(cond)
    free(cond)


@c_abi_export("pcc_cond_wait")
def pcc_cond_wait(cond, mutex) -> int:
    if ptr_is_null(cond) or ptr_is_null(mutex):
        return -1
    if pthread_cond_wait(cond, mutex) == 0:
        return 0
    return -1


@c_abi_export("pcc_cond_timedwait_ms")
def pcc_cond_timedwait_ms(cond, mutex, timeout_ms: int) -> int:
    if ptr_is_null(cond) or ptr_is_null(mutex):
        return -1
    if timeout_ms < 0:
        timeout_ms = 0
    now = pcc_platform_wall_time_us()
    target_us = now + timeout_ms * 1000
    timespec = stack_alloc(16)
    store_i64(timespec, 0, target_us // 1000000)
    store_i64(timespec, 8, (target_us % 1000000) * 1000)
    result = pthread_cond_timedwait(cond, mutex, timespec)
    if result == 0:
        return 0
    if result == 60 or result == 110:
        return 1
    return -1


@c_abi_export("pcc_cond_signal")
def pcc_cond_signal(cond) -> int:
    if ptr_is_null(cond) or pthread_cond_signal(cond) != 0:
        return -1
    return 0


@c_abi_export("pcc_cond_broadcast")
def pcc_cond_broadcast(cond) -> int:
    if ptr_is_null(cond) or pthread_cond_broadcast(cond) != 0:
        return -1
    return 0


@c_abi_export("pcc_current_thread_id")
def pcc_current_thread_id() -> int:
    thread_id = _tls_i64(global_addr("pcc_tls_thread_id_py"))
    if thread_id != 0:
        return thread_id
    if _world_init() != 0:
        # Registration is a lease in the stopped-world accounting.  Returning
        # a synthetic id here would let a caller run without joining the live
        # set and would make a no-park region fail open.
        pcc_platform_abort()
        return 0
    lock = global_load_ptr("pcc_world_lock_py")
    cond = global_load_ptr("pcc_world_cond_py")
    pthread_mutex_lock(lock)
    thread_id = _tls_i64(global_addr("pcc_tls_thread_id_py"))
    # A first-time raw/extension pthread is outside the stopped epoch's live
    # count.  It must not return into user code until the owner resumes.
    waiting_for_admission = 0
    while thread_id == 0 and load_i32(
        global_addr("pcc_thread_stop_requested"), 0
    ) != 0:
        if waiting_for_admission == 0:
            _world_store_i64(
                global_addr("pcc_registration_waiter_count_py"),
                _world_i64(global_addr("pcc_registration_waiter_count_py")) + 1,
            )
            waiting_for_admission = 1
        pcc_cond_wait(cond, lock)
        thread_id = _tls_i64(global_addr("pcc_tls_thread_id_py"))
    if waiting_for_admission != 0:
        _world_store_i64(
            global_addr("pcc_registration_waiter_count_py"),
            _world_i64(global_addr("pcc_registration_waiter_count_py")) - 1,
        )
    if thread_id == 0:
        thread_id = _world_i64(global_addr("pcc_next_thread_id_py"))
        _world_store_i64(global_addr("pcc_next_thread_id_py"), thread_id + 1)
        _world_store_i64(
            global_addr("pcc_live_thread_count_py"),
            _world_i64(global_addr("pcc_live_thread_count_py")) + 1,
        )
        _tls_store_i64(global_addr("pcc_tls_thread_id_py"), thread_id)
        pcc_cond_broadcast(global_load_ptr("pcc_world_cond_py"))
    pthread_mutex_unlock(lock)
    return thread_id


@c_abi_export("pcc_thread_no_park_enter")
def pcc_thread_no_park_enter() -> None:
    # Registration is defensive; a raw newcomer must register before it
    # acquires any managed owner/slot.  Do not safepoint an already-registered
    # live caller here: it may have just canonicalized the owner, and
    # live/unparked accounting makes the STW owner wait for outer exit.
    depth = load_i32(global_addr("pcc_tls_no_park_depth_py"), 0)
    if depth < 0 or depth == 2147483647:
        pcc_platform_abort()
        return
    if depth != 0:
        store_i32(global_addr("pcc_tls_no_park_depth_py"), 0, depth + 1)
        return
    pcc_current_thread_id()
    if _tls_i64(global_addr("pcc_tls_thread_id_py")) == 0:
        # Defensive fail-stop: depth must never become nonzero unless this
        # pthread is represented in the live-thread count.
        pcc_platform_abort()
        return
    store_i32(global_addr("pcc_tls_no_park_depth_py"), 0, depth + 1)


@c_abi_export("pcc_thread_no_park_exit")
def pcc_thread_no_park_exit() -> None:
    depth = load_i32(global_addr("pcc_tls_no_park_depth_py"), 0)
    if depth <= 0:
        pcc_platform_abort()
        return
    depth = depth - 1
    store_i32(global_addr("pcc_tls_no_park_depth_py"), 0, depth)
    if depth == 0:
        # Use the real locked safepoint path instead of a racy stop-flag poll.
        pcc_thread_safepoint()


@c_abi_export("pcc_thread_no_park_depth")
def pcc_thread_no_park_depth() -> int:
    return load_i32(global_addr("pcc_tls_no_park_depth_py"), 0)


@c_abi_export("pcc_thread_safepoint")
def pcc_thread_safepoint() -> None:
    if load_i32(global_addr("pcc_tls_no_park_depth_py"), 0) != 0:
        return
    if _world_init() != 0:
        pcc_platform_abort()
        return
    self_id = pcc_current_thread_id()
    lock = global_load_ptr("pcc_world_lock_py")
    cond = global_load_ptr("pcc_world_cond_py")
    pthread_mutex_lock(lock)
    while load_i32(global_addr("pcc_thread_stop_requested"), 0) != 0 and _world_i64(
        global_addr("pcc_stop_owner_thread_id_py")
    ) != self_id:
        epoch = _world_i64(global_addr("pcc_stop_epoch_py"))
        if load_i32(global_addr("pcc_tls_thread_parked_py"), 0) == 0 or _tls_i64(
            global_addr("pcc_tls_parked_epoch_py")
        ) != epoch:
            store_i32(global_addr("pcc_tls_thread_parked_py"), 0, 1)
            _tls_store_i64(global_addr("pcc_tls_parked_epoch_py"), epoch)
            _world_store_i64(
                global_addr("pcc_parked_thread_count_py"),
                _world_i64(global_addr("pcc_parked_thread_count_py")) + 1,
            )
            pcc_cond_broadcast(cond)
        pcc_cond_wait(cond, lock)
    if load_i32(global_addr("pcc_thread_stop_requested"), 0) == 0:
        store_i32(global_addr("pcc_tls_thread_parked_py"), 0, 0)
        _tls_store_i64(global_addr("pcc_tls_parked_epoch_py"), 0)
    pthread_mutex_unlock(lock)


@c_abi_export("pcc_thread_owns_stopped_world")
def pcc_thread_owns_stopped_world() -> int:
    if _world_init() != 0:
        return 0
    self_id = _tls_i64(global_addr("pcc_tls_thread_id_py"))
    if self_id == 0:
        return 0
    lock = global_load_ptr("pcc_world_lock_py")
    pthread_mutex_lock(lock)
    owns = 0
    if load_i32(global_addr("pcc_thread_stop_requested"), 0) != 0 and _world_i64(
        global_addr("pcc_stop_owner_thread_id_py")
    ) == self_id:
        owns = 1
    pthread_mutex_unlock(lock)
    return owns


@c_abi_export("pcc_thread_registration_waiter_count")
def pcc_thread_registration_waiter_count() -> int:
    # The diagnostic itself must not let a raw newcomer cross an active stop.
    pcc_current_thread_id()
    lock = global_load_ptr("pcc_world_lock_py")
    pthread_mutex_lock(lock)
    count = _world_i64(global_addr("pcc_registration_waiter_count_py"))
    pthread_mutex_unlock(lock)
    return count


@c_abi_export("pcc_stop_the_world")
def pcc_stop_the_world() -> int:
    if _world_init() != 0:
        return -1
    self_id = pcc_current_thread_id()
    lock = global_load_ptr("pcc_world_lock_py")
    cond = global_load_ptr("pcc_world_cond_py")
    pthread_mutex_lock(lock)
    while load_i32(global_addr("pcc_thread_stop_requested"), 0) != 0 and _world_i64(
        global_addr("pcc_stop_owner_thread_id_py")
    ) != self_id:
        epoch = _world_i64(global_addr("pcc_stop_epoch_py"))
        if load_i32(global_addr("pcc_tls_thread_parked_py"), 0) == 0 or _tls_i64(
            global_addr("pcc_tls_parked_epoch_py")
        ) != epoch:
            store_i32(global_addr("pcc_tls_thread_parked_py"), 0, 1)
            _tls_store_i64(global_addr("pcc_tls_parked_epoch_py"), epoch)
            _world_store_i64(
                global_addr("pcc_parked_thread_count_py"),
                _world_i64(global_addr("pcc_parked_thread_count_py")) + 1,
            )
            pcc_cond_broadcast(cond)
        pcc_cond_wait(cond, lock)
    if load_i32(global_addr("pcc_thread_stop_requested"), 0) == 0 and load_i32(
        global_addr("pcc_tls_thread_parked_py"), 0
    ) != 0:
        store_i32(global_addr("pcc_tls_thread_parked_py"), 0, 0)
        _tls_store_i64(global_addr("pcc_tls_parked_epoch_py"), 0)
    if load_i32(global_addr("pcc_thread_stop_requested"), 0) != 0:
        _world_store_i64(
            global_addr("pcc_stop_depth_py"),
            _world_i64(global_addr("pcc_stop_depth_py")) + 1,
        )
        pthread_mutex_unlock(lock)
        return 0
    atomic_store_i32(
        global_addr("pcc_thread_stop_requested"), 0, 1, "release"
    )
    _world_store_i64(global_addr("pcc_stop_owner_thread_id_py"), self_id)
    _world_store_i64(global_addr("pcc_stop_depth_py"), 1)
    epoch = _world_i64(global_addr("pcc_stop_epoch_py")) + 1
    if epoch <= 0:
        epoch = 1
    _world_store_i64(global_addr("pcc_stop_epoch_py"), epoch)
    _world_store_i64(global_addr("pcc_parked_thread_count_py"), 0)
    pcc_cond_broadcast(cond)
    while _world_i64(global_addr("pcc_live_thread_count_py")) > 1 and _world_i64(
        global_addr("pcc_parked_thread_count_py")
    ) < _world_i64(global_addr("pcc_live_thread_count_py")) - 1:
        pcc_cond_wait(cond, lock)
    pthread_mutex_unlock(lock)
    return 0


@c_abi_export("pcc_resume_world")
def pcc_resume_world() -> int:
    if _world_init() != 0:
        return -1
    self_id = pcc_current_thread_id()
    lock = global_load_ptr("pcc_world_lock_py")
    cond = global_load_ptr("pcc_world_cond_py")
    pthread_mutex_lock(lock)
    if load_i32(global_addr("pcc_thread_stop_requested"), 0) == 0 or _world_i64(
        global_addr("pcc_stop_owner_thread_id_py")
    ) != self_id:
        pthread_mutex_unlock(lock)
        return -1
    depth = _world_i64(global_addr("pcc_stop_depth_py"))
    if depth > 1:
        _world_store_i64(global_addr("pcc_stop_depth_py"), depth - 1)
        pthread_mutex_unlock(lock)
        return 0
    atomic_store_i32(
        global_addr("pcc_thread_stop_requested"), 0, 0, "release"
    )
    _world_store_i64(global_addr("pcc_stop_owner_thread_id_py"), 0)
    _world_store_i64(global_addr("pcc_stop_depth_py"), 0)
    _world_store_i64(global_addr("pcc_parked_thread_count_py"), 0)
    pcc_cond_broadcast(cond)
    pthread_mutex_unlock(lock)
    return 0


@c_abi_export("pcc_thread_unregister_current")
def pcc_thread_unregister_current() -> None:
    if pcc_thread_no_park_depth() != 0:
        pcc_platform_abort()
        return
    if load_i32(global_addr("pcc_tls_unregister_in_progress_py"), 0) != 0:
        pcc_platform_abort()
        return
    if _tls_i64(global_addr("pcc_tls_thread_id_py")) == 0:
        return
    # The owner must finish the active stop before leaving the live set.  This
    # check precedes exception/buffer cleanup because either can decref and
    # reenter runtime code.
    if pcc_thread_owns_stopped_world() != 0:
        pcc_platform_abort()
        return
    store_i32(global_addr("pcc_tls_unregister_in_progress_py"), 0, 1)
    # The current-exception slot owns its reference and, in the pcc-Python
    # runtime, publishes its native-TLS address through the common GC root
    # registry.  Retire both before the pthread's TLS storage disappears.
    py_clear_exception()
    pcc_gc_thread_unregister_buffers()
    if _world_init() != 0:
        pcc_platform_abort()
        return
    lock = global_load_ptr("pcc_world_lock_py")
    pthread_mutex_lock(lock)
    self_id = _tls_i64(global_addr("pcc_tls_thread_id_py"))
    # Cleanup above can decref and reenter.  Revalidate depth and STW
    # ownership under the same lock that protects live/owner accounting.
    if pcc_thread_no_park_depth() != 0 or (
        self_id != 0
        and load_i32(global_addr("pcc_thread_stop_requested"), 0) != 0
        and _world_i64(global_addr("pcc_stop_owner_thread_id_py")) == self_id
    ):
        pthread_mutex_unlock(lock)
        pcc_platform_abort()
        return
    if self_id != 0:
        if load_i32(global_addr("pcc_tls_thread_parked_py"), 0) != 0:
            store_i32(global_addr("pcc_tls_thread_parked_py"), 0, 0)
            parked = _world_i64(global_addr("pcc_parked_thread_count_py"))
            if parked > 0:
                _world_store_i64(
                    global_addr("pcc_parked_thread_count_py"), parked - 1
                )
            _tls_store_i64(global_addr("pcc_tls_parked_epoch_py"), 0)
        _tls_store_i64(global_addr("pcc_tls_thread_id_py"), 0)
        live = _world_i64(global_addr("pcc_live_thread_count_py"))
        if live > 0:
            _world_store_i64(global_addr("pcc_live_thread_count_py"), live - 1)
        pcc_cond_broadcast(global_load_ptr("pcc_world_cond_py"))
    pthread_mutex_unlock(lock)
    store_i32(global_addr("pcc_tls_unregister_in_progress_py"), 0, 0)


@c_abi_export("pcc_thread_trampoline_py")
def _thread_trampoline(start):
    entry = load_ptr(start, 0)
    arg = load_ptr(start, 8)
    handle = load_ptr(start, 16)
    free(start)
    pcc_current_thread_id()
    pcc_thread_safepoint()
    result = call_ptr1(entry, arg)
    state_lock = load_ptr(handle, 8)
    if pcc_mutex_lock(state_lock) != 0:
        pcc_platform_abort()
        return result
    store_ptr(handle, 24, result)
    store_i32(handle, 16, 1)
    detached = load_i32(handle, 20)
    if pcc_mutex_unlock(state_lock) != 0:
        pcc_platform_abort()
        return result
    if detached != 0:
        pcc_mutex_free(state_lock)
        free(handle)
    # Keep teardown as the final runtime action.  A later mutex/safepoint call
    # could register this pthread again and strand it in the live count.
    pcc_thread_unregister_current()
    return result


@c_abi_export("pcc_thread_start")
def pcc_thread_start(out, entry, arg) -> int:
    if ptr_is_null(out) or ptr_is_null(entry):
        return -1
    handle = malloc(32)
    if ptr_is_null(handle):
        return -1
    state_lock = pcc_mutex_new()
    if ptr_is_null(state_lock):
        free(handle)
        return -1
    store_ptr(handle, 0, null())
    store_ptr(handle, 8, state_lock)
    store_i32(handle, 16, 0)
    store_i32(handle, 20, 0)
    store_ptr(handle, 24, null())
    start = malloc(24)
    if ptr_is_null(start):
        pcc_mutex_free(state_lock)
        free(handle)
        return -1
    store_ptr(start, 0, entry)
    store_ptr(start, 8, arg)
    store_ptr(start, 16, handle)
    if pthread_create(
        handle, null(), function_addr("pcc_thread_trampoline_py"), start
    ) != 0:
        free(start)
        pcc_mutex_free(state_lock)
        free(handle)
        return -1
    store_ptr(out, 0, handle)
    return 0


@c_abi_export("pcc_thread_join")
def pcc_thread_join(handle, result_out) -> int:
    if ptr_is_null(handle):
        return -1
    state_lock = load_ptr(handle, 8)
    while True:
        pcc_mutex_lock(state_lock)
        done = load_i32(handle, 16)
        local_result = load_ptr(handle, 24)
        pcc_mutex_unlock(state_lock)
        if done != 0:
            joined = stack_alloc(8)
            store_ptr(joined, 0, null())
            result = pthread_join(load_ptr(handle, 0), joined)
            if ptr_is_null(result_out) == 0:
                value = local_result
                if result == 0:
                    value = load_ptr(joined, 0)
                store_ptr(result_out, 0, value)
            pcc_mutex_free(state_lock)
            free(handle)
            if result == 0:
                return 0
            return -1
        pcc_thread_safepoint()
        sched_yield()
    return -1


@c_abi_export("pcc_thread_detach")
def pcc_thread_detach(handle) -> None:
    if ptr_is_null(handle):
        return
    pthread_detach(load_ptr(handle, 0))
    state_lock = load_ptr(handle, 8)
    pcc_mutex_lock(state_lock)
    store_i32(handle, 20, 1)
    done = load_i32(handle, 16)
    pcc_mutex_unlock(state_lock)
    if done != 0:
        pcc_mutex_free(state_lock)
        free(handle)
