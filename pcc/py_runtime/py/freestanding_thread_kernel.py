"""Default single-thread runtime kernel authored in freestanding pcc-Python.

The production pcc-Python archive is built with ``PCC_WITH_THREADS=0`` unless
an explicitly threaded runtime is requested.  This module owns that default
ABI exactly: reference counts are non-atomic, stop-the-world operations are
no-ops, native thread creation fails, and mutex/condition constructors return
stable non-null sentinels.  It does not claim pthread parallelism.
"""

from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    define_global_i8,
    define_global_i32,
    define_thread_local_i32,
    global_addr,
    load_i64,
    null,
    ptr_is_null,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

define_global_i32("pcc_thread_stop_requested", 0)
define_thread_local_i32("pcc_native_thread_identity_token", 0)
define_global_i8("pcc_mutex_stub", 0)
define_global_i8("pcc_cond_stub", 0)


@c_abi_export("pcc_threads_enabled")
def pcc_threads_enabled() -> i64:
    return 0


@c_abi_export("pcc_refcount_strategy")
def pcc_refcount_strategy() -> i64:
    return 0


@c_abi_export("pcc_current_native_thread_token")
def pcc_current_native_thread_token():
    return global_addr("pcc_native_thread_identity_token")


@c_abi_export("pcc_refcount_incref")
def pcc_refcount_incref(slot) -> i64:
    if ptr_is_null(slot):
        return 0
    value = load_i64(slot, 0) + 1
    store_i64(slot, 0, value)
    return value


@c_abi_export("pcc_refcount_decref")
def pcc_refcount_decref(slot) -> i64:
    if ptr_is_null(slot):
        return 0
    value = load_i64(slot, 0) - 1
    store_i64(slot, 0, value)
    return value


@c_abi_export("pcc_refcount_load")
def pcc_refcount_load(slot) -> i64:
    if ptr_is_null(slot):
        return 0
    return load_i64(slot, 0)


@c_abi_export("pcc_refcount_forget")
def pcc_refcount_forget(slot) -> None:
    return


@c_abi_export("pcc_current_thread_id")
def pcc_current_thread_id() -> i64:
    return 1


@c_abi_export("pcc_thread_safepoint")
def pcc_thread_safepoint() -> None:
    return


@c_abi_export("pcc_stop_the_world")
def pcc_stop_the_world() -> i64:
    return 0


@c_abi_export("pcc_resume_world")
def pcc_resume_world() -> i64:
    return 0


@c_abi_export("pcc_thread_start")
def pcc_thread_start(out, entry, arg) -> i64:
    return -1


@c_abi_export("pcc_thread_join")
def pcc_thread_join(thread, result) -> i64:
    if ptr_is_null(result) == 0:
        store_ptr(result, 0, null())
    return -1


@c_abi_export("pcc_thread_detach")
def pcc_thread_detach(thread) -> None:
    return


@c_abi_export("pcc_mutex_new")
def pcc_mutex_new():
    return global_addr("pcc_mutex_stub")


@c_abi_export("pcc_mutex_free")
def pcc_mutex_free(mutex) -> None:
    return


@c_abi_export("pcc_mutex_lock")
def pcc_mutex_lock(mutex) -> i64:
    return 0


@c_abi_export("pcc_mutex_unlock")
def pcc_mutex_unlock(mutex) -> i64:
    return 0


@c_abi_export("pcc_cond_new")
def pcc_cond_new():
    return global_addr("pcc_cond_stub")


@c_abi_export("pcc_cond_free")
def pcc_cond_free(cond) -> None:
    return


@c_abi_export("pcc_cond_wait")
def pcc_cond_wait(cond, mutex) -> i64:
    return 0


@c_abi_export("pcc_cond_timedwait_ms")
def pcc_cond_timedwait_ms(cond, mutex, timeout_ms: i64) -> i64:
    return 0


@c_abi_export("pcc_cond_signal")
def pcc_cond_signal(cond) -> i64:
    return 0


@c_abi_export("pcc_cond_broadcast")
def pcc_cond_broadcast(cond) -> i64:
    return 0
