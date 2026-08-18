"""Compiler-recognized unsafe intrinsics for pcc runtime code.

This module is intentionally not a normal runtime library. The
pcc-Python frontend consumes imports from ``pcc.unsafe`` at compile
time and lowers each call to raw LLVM/platform operations. Calling
these helpers under CPython is a misuse and raises loudly.
"""
from __future__ import annotations

from typing import Any


def _trap(name: str) -> None:
    raise NotImplementedError(
        f"pcc.unsafe.{name}() must be lowered by the pcc compiler"
    )


def malloc(size: int) -> Any:
    _trap("malloc")


def cstr(value: str) -> Any:
    _trap("cstr")


def global_addr(symbol: str) -> Any:
    _trap("global_addr")


def function_addr(symbol: str) -> Any:
    """Return the raw address of a function defined in the current module."""
    _trap("function_addr")


def global_load_ptr(symbol: str) -> Any:
    _trap("global_load_ptr")


def global_store_ptr(symbol: str, value: Any) -> None:
    _trap("global_store_ptr")


def abi_constant(name: str) -> int:
    """Return one compiler-generated freestanding ABI constant."""
    _trap("abi_constant")


def define_global_ptr_null(symbol: str) -> None:
    _trap("define_global_ptr_null")


def define_global_i8(symbol: str, value: int) -> None:
    _trap("define_global_i8")


def define_global_i32(symbol: str, value: int) -> None:
    _trap("define_global_i32")


def define_global_i64(symbol: str, value: int) -> None:
    _trap("define_global_i64")


def define_thread_local_ptr_null(symbol: str) -> None:
    """Define a zero-initialized pointer with native thread-local storage."""
    _trap("define_thread_local_ptr_null")


def define_thread_local_i32(symbol: str, value: int) -> None:
    """Define an i32 with native thread-local storage."""
    _trap("define_thread_local_i32")


def define_global_i64_array(symbol: str, *values: int) -> None:
    _trap("define_global_i64_array")


def define_global_struct_words(symbol: str, *fields) -> None:
    """Define a linker-visible struct global with a flat 8-byte-word layout.

    Each ``fields`` item is either an ``int`` (an i64 word) or a ``str``
    naming another global whose address is stored in that word slot.  This is
    the raw-layout primitive the C-API type-token globals need: a struct that
    mixes scalar words (refcount/type_tag/flags) with pointer references
    (tp_name) at exact offsets, which ``define_global_i64_array`` cannot
    express because its elements are all constants.
    """
    _trap("define_global_struct_words")


def define_global_ptr_to_global(symbol: str, target: str) -> None:
    _trap("define_global_ptr_to_global")


def calloc(count: int, size: int) -> Any:
    _trap("calloc")


def realloc(ptr: Any, size: int) -> Any:
    _trap("realloc")


def free(ptr: Any) -> None:
    _trap("free")


def ptr_add(ptr: Any, offset: int) -> Any:
    _trap("ptr_add")


def ptr_diff(lhs: Any, rhs: Any) -> int:
    _trap("ptr_diff")


def int_to_ptr(value: int) -> Any:
    """Reinterpret one pointer-width integer as an opaque raw pointer."""
    _trap("int_to_ptr")


def ptr_to_int(value: Any) -> int:
    """Reinterpret an opaque raw pointer as a pointer-width integer."""
    _trap("ptr_to_int")


def wrapping_mul_i64(lhs: int, rhs: int) -> int:
    """Return the low 64 bits of a signed integer multiplication."""
    _trap("wrapping_mul_i64")


def logical_shift_right_i64(value: int, bits: int) -> int:
    """Return a raw i64 logical right shift without Python exceptions."""
    _trap("logical_shift_right_i64")


def logical_shift_left_i64(value: int, bits: int) -> int:
    """Return a raw i64 left shift; caller proves ``0 <= bits < 64``."""
    _trap("logical_shift_left_i64")


def unsigned_div_i64(value: int, divisor: int) -> int:
    """Return raw i64 unsigned division; caller proves divisor is nonzero."""
    _trap("unsigned_div_i64")


def unsigned_rem_i64(value: int, divisor: int) -> int:
    """Return raw i64 unsigned remainder; caller proves divisor is nonzero."""
    _trap("unsigned_rem_i64")


def unsigned_greater_i64(lhs: int, rhs: int) -> bool:
    """Compare two raw i64 bit patterns as unsigned integers."""
    _trap("unsigned_greater_i64")


def mul_overflow_i64(lhs: int, rhs: int) -> bool:
    """Return whether signed 64-bit multiplication would overflow."""
    _trap("mul_overflow_i64")


def float_to_i64(value: float) -> int:
    """Truncate a proven-in-range finite f64 value to signed i64."""
    _trap("float_to_i64")


def i64_to_float(value: int) -> float:
    """Convert signed i64 to f64."""
    _trap("i64_to_float")


def f64_div(lhs: float, rhs: float) -> float:
    """Perform one raw IEEE-754 binary64 division.

    Unlike Python ``/``, this machine-boundary operation deliberately does
    not translate a zero divisor into ``ZeroDivisionError``.  Freestanding
    libc numeric owners use it where the C ABI requires an infinity/NaN and
    the corresponding floating-point status flag.
    """
    _trap("f64_div")


def f64_signbit(value: float) -> int:
    """Return the IEEE-754 sign bit, including for negative zero/NaN."""
    _trap("f64_signbit")


def f64_bits(value: float) -> int:
    """Return the raw IEEE-754 binary64 bit pattern as an i64 lane."""
    _trap("f64_bits")


def f64_pair_make(first: float, second: float) -> complex:
    """Build the compiler-owned ``{f64,f64}`` machine aggregate."""
    _trap("f64_pair_make")


def f64_pair_first(value: complex) -> float:
    """Extract lane zero from a compiler-owned ``{f64,f64}`` aggregate."""
    _trap("f64_pair_first")


def f64_pair_second(value: complex) -> float:
    """Extract lane one from a compiler-owned ``{f64,f64}`` aggregate."""
    _trap("f64_pair_second")


def null() -> Any:
    _trap("null")


def ptr_eq(lhs: Any, rhs: Any) -> bool:
    _trap("ptr_eq")


def ptr_is_null(ptr: Any) -> bool:
    _trap("ptr_is_null")


def is_tagged_int(ptr: Any) -> bool:
    _trap("is_tagged_int")


def tag_int(value: int) -> Any:
    _trap("tag_int")


def untag_int(ptr: Any) -> int:
    _trap("untag_int")


def load_i64(ptr: Any, offset: int) -> int:
    _trap("load_i64")


def load_i64x4(ptr: Any, offset: int) -> Any:
    """Load four adjacent raw i64 lanes as one compiler-owned value."""
    _trap("load_i64x4")


def load_i64x4_strided(ptr: Any, offset: int, stride: int) -> Any:
    """Load four raw i64 lanes separated by a byte stride."""
    _trap("load_i64x4_strided")


def load_i32(ptr: Any, offset: int) -> int:
    _trap("load_i32")


def load_i8(ptr: Any, offset: int) -> int:
    _trap("load_i8")


def load_ptr(ptr: Any, offset: int) -> Any:
    _trap("load_ptr")


def load_f64(ptr: Any, offset: int) -> float:
    _trap("load_f64")


def store_i64(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i64")


def store_i32(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i32")


def store_i8(ptr: Any, offset: int, value: int) -> None:
    _trap("store_i8")


def store_ptr(ptr: Any, offset: int, value: Any) -> None:
    _trap("store_ptr")


def store_f64(ptr: Any, offset: int, value: float) -> None:
    _trap("store_f64")


def memset(ptr: Any, value: int, size: int) -> Any:
    _trap("memset")


def memcpy(dst: Any, src: Any, size: int) -> Any:
    _trap("memcpy")


def memmove(dst: Any, src: Any, size: int) -> Any:
    _trap("memmove")


def write(fd: int, ptr: Any, size: int) -> int:
    _trap("write")


def read(fd: int, ptr: Any, size: int) -> int:
    _trap("read")


def close(fd: int) -> int:
    _trap("close")


def seek_file(fd: int, offset: int, whence: int) -> int:
    """Seek an open file descriptor and return its new offset.

    Linux returns the raw negative errno from the syscall boundary; Darwin
    follows libSystem's ``lseek`` contract and returns ``-1`` on failure.
    """
    _trap("seek_file")


def open_readonly(path: Any) -> int:
    """Open a path read-only and return fd or a negative errno."""
    _trap("open_readonly")


def darwin_current_rss_bytes() -> int:
    """Read the current RSS through Darwin's Mach task-info ABI.

    Non-Darwin lowering returns ``-1`` without importing Darwin symbols.
    """
    _trap("darwin_current_rss_bytes")


def darwin_peak_rss_bytes() -> int:
    """Read peak RSS through Darwin's getrusage ABI.

    Non-Darwin lowering returns ``-1`` without importing Darwin symbols.
    """
    _trap("darwin_peak_rss_bytes")


def open_file(path: Any, access_mode: int, disposition: int) -> int:
    """Open a file using target-neutral access/disposition selectors.

    access_mode is 0=read, 1=write, 2=read/write. disposition is
    0=existing, 1=create/truncate, 2=create/append, 3=create/exclusive.
    """
    _trap("open_file")


def rename_file(source: Any, destination: Any) -> int:
    """Atomically rename a path, replacing the destination when permitted."""
    _trap("rename_file")


def chmod_file(path: Any, mode: int) -> int:
    """Set POSIX permission bits on a path."""
    _trap("chmod_file")


def sync_file(fd: int) -> int:
    """Synchronize one open file descriptor's data and metadata."""
    _trap("sync_file")


def socket_open(family: int, socket_type: int, protocol: int) -> int:
    _trap("socket_open")


def socket_connect(fd: int, address: Any, address_len: int) -> int:
    _trap("socket_connect")


def socket_bind(fd: int, address: Any, address_len: int) -> int:
    _trap("socket_bind")


def socket_listen(fd: int, backlog: int) -> int:
    _trap("socket_listen")


def socket_setsockopt(
    fd: int, level: int, option: int, value: Any, value_len: int
) -> int:
    _trap("socket_setsockopt")


def socket_getsockopt(
    fd: int, level: int, option: int, value: Any, value_capacity: int
) -> int:
    """Read a socket option and return its byte length or a negative errno."""
    _trap("socket_getsockopt")


def fd_control(fd: int, command: int, value: int) -> int:
    _trap("fd_control")


def eventfd_create(initial_value: int, flags: int) -> int:
    """Create a Linux eventfd, returning its fd or a negative errno.

    This is a compiler-owned raw-syscall boundary. Unsupported targets return
    ``-ENOSYS`` so a caller cannot mistake a host fallback for a live eventfd.
    """
    _trap("eventfd_create")


def socket_send(fd: int, buffer: Any, size: int, flags: int) -> int:
    _trap("socket_send")


def socket_recv(fd: int, buffer: Any, size: int, flags: int) -> int:
    _trap("socket_recv")


def socket_accept(fd: int) -> int:
    """Accept one connection, returning its fd or a negative errno."""
    _trap("socket_accept")


def socket_shutdown(fd: int, how: int) -> int:
    """Shut down one or both directions, returning zero or a negative errno."""
    _trap("socket_shutdown")


def socket_sockname(fd: int, address: Any, capacity: int) -> int:
    """Write the local sockaddr and return its byte length or a negative errno."""
    _trap("socket_sockname")


def socket_peername(fd: int, address: Any, capacity: int) -> int:
    """Write the peer sockaddr and return its byte length or a negative errno."""
    _trap("socket_peername")


def poll_fd(fd: int, events: int, timeout_ms: int) -> int:
    """Return poll(2) revents for one descriptor, zero, or a negative errno."""
    _trap("poll_fd")


def poll_readable_pair(fd0: int, fd1: int, timeout_ms: int) -> int:
    """Poll two descriptors for reads; return readiness bits or negative errno."""
    _trap("poll_readable_pair")


def getpid() -> int:
    _trap("getpid")


def getcwd(buffer: Any, size: int) -> Any:
    _trap("getcwd")


def readlink(path: Any, buffer: Any, size: int) -> int:
    _trap("readlink")


def mkdir(path: Any, mode: int) -> int:
    _trap("mkdir")


def unlinkat(path: Any, remove_directory: int) -> int:
    """Remove a path relative to AT_FDCWD; nonzero selects directory mode."""
    _trap("unlinkat")


def uname(buffer: Any) -> int:
    _trap("uname")


def uname_field(buffer: Any, index: int) -> Any:
    _trap("uname_field")


def cpu_query(buffer: Any, size: int) -> int:
    """Return a platform CPU query encoding consumed by the system port."""
    _trap("cpu_query")


def clock_gettime(kind: int, buffer: Any) -> int:
    """Read wall (kind=0) or monotonic (kind=1) time into two i64 fields."""
    _trap("clock_gettime")


def nanosleep(request: Any, remaining: Any) -> int:
    _trap("nanosleep")


def waitpid(pid: int, status: Any, options: int) -> int:
    """Wait for a child and return either its pid or a negative errno."""
    _trap("waitpid")


def kill(pid: int, signal_number: int) -> int:
    """Send one signal and return zero or a negative errno."""
    _trap("kill")


def process_exit(status: int) -> None:
    """Terminate the complete process without running host-libc teardown."""
    _trap("process_exit")


def spawn_process(
    path: Any, argv: Any, envp: Any, capture_output: int
) -> int:
    """Spawn one resolved executable in its own process group.

    Returns the child pid or a negative platform error.  PATH resolution is
    deliberately outside this machine-boundary intrinsic.
    """
    _trap("spawn_process")


def spawn_process_pipe(
    path: Any,
    argv: Any,
    envp: Any,
    parent_reads: int,
    fd_out: Any,
) -> int:
    """Spawn with one parent/child pipe; return pid and store parent fd."""
    _trap("spawn_process_pipe")


def stack_alloc(size: int) -> Any:
    _trap("stack_alloc")


def strlen(ptr: Any) -> int:
    _trap("strlen")


def getenv(name: Any) -> Any:
    _trap("getenv")


def setenv(name: Any, value: Any, overwrite: int) -> int:
    _trap("setenv")


def unsetenv(name: Any) -> int:
    _trap("unsetenv")


def initial_environ() -> Any:
    _trap("initial_environ")


def access(path: Any, mode: int) -> int:
    _trap("access")


def stat_kind(path: Any) -> int:
    _trap("stat_kind")


def stat_mtime(path: Any) -> float:
    _trap("stat_mtime")


def target_sys_platform() -> Any:
    _trap("target_sys_platform")


def target_platform_machine() -> Any:
    _trap("target_platform_machine")


def atomic_load_i32(ptr: Any, offset: int, order: str) -> int:
    _trap("atomic_load_i32")


def atomic_load_i64(ptr: Any, offset: int, order: str) -> int:
    _trap("atomic_load_i64")


def atomic_store_i32(ptr: Any, offset: int, value: int, order: str) -> None:
    _trap("atomic_store_i32")


def atomic_store_i64(ptr: Any, offset: int, value: int, order: str) -> None:
    _trap("atomic_store_i64")


def atomic_rmw_i32(op: str, ptr: Any, offset: int, value: int, order: str) -> int:
    """Atomic read-modify-write; returns the OLD value (LLVM atomicrmw)."""
    _trap("atomic_rmw_i32")


def atomic_rmw_i64(op: str, ptr: Any, offset: int, value: int, order: str) -> int:
    """Atomic read-modify-write; returns the OLD value (LLVM atomicrmw)."""
    _trap("atomic_rmw_i64")


def atomic_cas_i32(
    ptr: Any,
    offset: int,
    expected: int,
    desired: int,
    order: str,
    fail_order: str,
) -> int:
    """Strong compare-and-swap; returns the OLD value (== expected on success)."""
    _trap("atomic_cas_i32")


def atomic_cas_i64(
    ptr: Any,
    offset: int,
    expected: int,
    desired: int,
    order: str,
    fail_order: str,
) -> int:
    """Strong compare-and-swap; returns the OLD value (== expected on success)."""
    _trap("atomic_cas_i64")


def atomic_fence(order: str) -> None:
    _trap("atomic_fence")


def atomic_test_and_set(ptr: Any, offset: int, order: str) -> int:
    """Atomically set the i8 flag byte to 1; returns 1 if it was already set."""
    _trap("atomic_test_and_set")


def atomic_clear(ptr: Any, offset: int, order: str) -> None:
    """Atomically clear the i8 flag byte (store 0; release-class orderings)."""
    _trap("atomic_clear")


def syscall6(nr: int, a1: Any, a2: Any, a3: Any, a4: Any, a5: Any, a6: Any) -> int:
    """Raw Linux x86_64 syscall (musl syscall_arch.h ABI); returns raw i64.

    Linux x86_64 only. Darwin raw syscalls are unsupported by policy —
    macOS code keeps using named libSystem externs.
    """
    _trap("syscall6")


def page_alloc(size: int) -> Any:
    """Map one read/write page range; return null on platform failure."""
    _trap("page_alloc")


def page_free(ptr: Any, size: int) -> int:
    """Unmap one range returned by page_alloc; return the platform status."""
    _trap("page_free")


def va_start() -> Any:
    """Create a cursor over the current variadic C-ABI arguments."""
    _trap("va_start")


def va_arg_i64(cursor: Any) -> int:
    """Consume one ABI-promoted integer/pointer-width argument."""
    _trap("va_arg_i64")


def va_arg_i32(cursor: Any) -> int:
    """Consume one default-promoted signed ``int`` argument."""
    _trap("va_arg_i32")


def va_arg_u32(cursor: Any) -> int:
    """Consume one default-promoted unsigned ``int`` argument."""
    _trap("va_arg_u32")


def va_arg_ptr(cursor: Any) -> Any:
    """Consume one pointer argument."""
    _trap("va_arg_ptr")


def va_arg_f64(cursor: Any) -> float:
    """Consume one default-promoted floating-point argument."""
    _trap("va_arg_f64")


def va_cursor(ap: Any) -> Any:
    """Adapt a fixed C ``va_list`` parameter for ``va_arg_*``."""
    _trap("va_cursor")


def va_end(cursor: Any) -> None:
    """Finish a cursor created by :func:`va_start`."""
    _trap("va_end")


def call_ptr1(fn: Any, arg0: Any) -> Any:
    _trap("call_ptr1")


def call_ptr0(fn: Any) -> Any:
    """Call ``void *(*)(void)``."""
    _trap("call_ptr0")


def call_void_ptr0(fn: Any) -> None:
    _trap("call_void_ptr0")


def call_void_ptr1(fn: Any, arg0: Any) -> None:
    _trap("call_void_ptr1")


def call_void_ptr2(fn: Any, arg0: Any, arg1: Any) -> None:
    """Call ``void (*)(void *, void *)`` through a raw pointer."""
    _trap("call_void_ptr2")


def call_void_ptr_i64_ptr(fn: Any, arg0: Any, arg1: int, arg2: Any) -> None:
    """Call ``void (*)(void *, int64_t, void *)`` through a raw pointer."""
    _trap("call_void_ptr_i64_ptr")


def call_ptr2(fn: Any, arg0: Any, arg1: Any) -> Any:
    _trap("call_ptr2")


def call_ptr_ptr_i64(fn: Any, arg0: Any, arg1: int) -> Any:
    """Call ``void *(*)(void *, int64_t)`` through a raw pointer."""
    _trap("call_ptr_ptr_i64")


def call_ptr_ptr_ptr_i32(fn: Any, arg0: Any, arg1: Any, arg2: int) -> Any:
    """Call ``void *(*)(void *, void *, int32_t)`` through a raw pointer."""
    _trap("call_ptr_ptr_ptr_i32")


def call_ptr_ptr_ptr_i64_ptr(fn: Any, arg0: Any, arg1: Any, arg2: int, arg3: Any) -> Any:
    """Call ``void *(*)(void *, void *, int64_t, void *)`` (vectorcall shape)."""
    _trap("call_ptr_ptr_ptr_i64_ptr")


def call_ptr3(fn: Any, arg0: Any, arg1: Any, arg2: Any) -> Any:
    """Call ``void *(*)(void *, void *, void *)`` through a raw pointer."""
    _trap("call_ptr3")


def call_ptr4(fn: Any, arg0: Any, arg1: Any, arg2: Any, arg3: Any) -> Any:
    """Call ``void *(*)(void *, void *, void *, void *)`` through a raw pointer."""
    _trap("call_ptr4")


def call_i64_i64_ptr(fn: Any, arg0: int, arg1: Any) -> int:
    """Call ``int64_t (*)(uint64_t, void *)`` through a raw pointer."""
    _trap("call_i64_i64_ptr")


def call_i32_ptr1(fn: Any, arg0: Any) -> int:
    """Call ``int32_t (*)(void *)`` and sign-extend its result."""
    _trap("call_i32_ptr1")


def call_i32_ptr_i64(fn: Any, arg0: Any, arg1: int) -> int:
    """Call ``int32_t (*)(void *, uint64_t)`` exactly."""
    _trap("call_i32_ptr_i64")


def call_i32_ptr_i32(fn: Any, arg0: Any, arg1: int) -> int:
    """Call ``int32_t (*)(void *, int32_t)`` and sign-extend its result."""
    _trap("call_i32_ptr_i32")


def call_i32_ptr_i32_i32(fn: Any, arg0: Any, arg1: int, arg2: int) -> int:
    """Call ``int32_t (*)(void *, int32_t, int32_t)`` exactly."""
    _trap("call_i32_ptr_i32_i32")


def call_i32_ptr_i32_i32_i32(
    fn: Any, arg0: Any, arg1: int, arg2: int, arg3: int
) -> int:
    """Call ``int32_t (*)(void *, int32_t, int32_t, int32_t)``."""
    _trap("call_i32_ptr_i32_i32_i32")


def call_i32_ptr_i32_i32_i32_i32_i32_ptr_i32(
    fn: Any,
    arg0: Any,
    arg1: int,
    arg2: int,
    arg3: int,
    arg4: int,
    arg5: int,
    arg6: Any,
    arg7: int,
) -> int:
    """Call zlib's exact ``deflateInit2_`` ABI through a raw pointer."""
    _trap("call_i32_ptr_i32_i32_i32_i32_i32_ptr_i32")


def call_i32_i32_ptr_i64(fn: Any, arg0: int, arg1: Any, arg2: int) -> int:
    """Call ``int32_t (*)(int32_t, void *, uint64_t)`` exactly."""
    _trap("call_i32_i32_ptr_i64")


def call_i32_i64_i64_ptr(fn: Any, arg0: int, arg1: int, arg2: Any) -> int:
    """Call ``int32_t (*)(int64_t, int64_t, int64_t *)`` exactly."""
    _trap("call_i32_i64_i64_ptr")


def call_i32_i64_i32_i64(fn: Any, arg0: int, arg1: int, arg2: int) -> int:
    """Call ``int32_t (*)(uint64_t, uint32_t, int64_t)`` exactly."""
    _trap("call_i32_i64_i32_i64")


def call_i64_i64(fn: Any, arg0: int) -> int:
    """Call ``int64_t (*)(int64_t)`` through a raw pointer."""
    _trap("call_i64_i64")


def call_i64_ptr1(fn: Any, arg0: Any) -> int:
    """Call ``int64_t (*)(void *)`` through a raw pointer."""
    _trap("call_i64_ptr1")


def call_i64_ptr_i64(fn: Any, arg0: Any, arg1: int) -> int:
    """Call ``int64_t (*)(void *, int64_t)`` through a raw pointer."""
    _trap("call_i64_ptr_i64")


def call_i64_ptr_ptr_ptr_i64(fn: Any, arg0: Any, arg1: Any, arg2: Any, arg3: int) -> int:
    """Call ``int64_t (*)(void *, void *, void *, int64_t)`` through a raw pointer."""
    _trap("call_i64_ptr_ptr_ptr_i64")


def call_i64_ptr2(fn: Any, arg0: Any, arg1: Any) -> int:
    """Call ``int64_t (*)(void *, void *)`` through a raw pointer."""
    _trap("call_i64_ptr2")


def call_i64_ptr3(fn: Any, arg0: Any, arg1: Any, arg2: Any) -> int:
    """Call ``int64_t (*)(void *, void *, void *)`` through a raw pointer."""
    _trap("call_i64_ptr3")


def call_i64_ptr3_i64_i64_i64(fn: Any, arg0: Any, arg1: Any, arg2: Any, arg3: int, arg4: int, arg5: int) -> int:
    """Call ``int64_t (*)(void *, void *, void *, int64_t, int64_t, int64_t)``."""
    _trap("call_i64_ptr3_i64_i64_i64")


def call_i64_ptr4_i64_i64(fn: Any, arg0: Any, arg1: Any, arg2: Any, arg3: Any, arg4: int, arg5: int) -> int:
    """Call ``int64_t (*)(void *, void *, void *, void *, int64_t, int64_t)``."""
    _trap("call_i64_ptr4_i64_i64")


def call_ptr_i64_i64(fn: Any, arg0: Any, arg1: int, arg2: int) -> Any:
    """Call ``void *(*)(void *, int64_t, int64_t)`` through a raw pointer."""
    _trap("call_ptr_i64_i64")


def call_i64_i64_i64_ptr(fn: Any, arg0: int, arg1: int, arg2: Any) -> int:
    """Call ``int64_t (*)(int64_t, int64_t, void *)`` through a raw pointer."""
    _trap("call_i64_i64_i64_ptr")


def call_i64_ptr_i64_ptr(fn: Any, arg0: Any, arg1: int, arg2: Any) -> int:
    """Call ``int64_t (*)(void *, int64_t, void *)``."""
    _trap("call_i64_ptr_i64_ptr")


def call_i64_ptr_i64_i64(fn: Any, arg0: Any, arg1: int, arg2: int) -> int:
    """Call ``int64_t (*)(void *, int64_t, int64_t)``."""
    _trap("call_i64_ptr_i64_i64")


def darwin_errno_location() -> Any:
    """Return Darwin's current-thread ``errno`` slot, or NULL off Darwin.

    This is a compiler-owned named host-ABI boundary.  It deliberately calls
    ``__error`` directly so callers can snapshot errno before any lazy symbol
    lookup or allocation has a chance to overwrite it.
    """
    _trap("darwin_errno_location")


def call_variadic_i64_ptr_i64_ptr(fn: Any, arg0: Any, arg1: int, arg2: Any) -> int:
    """Call ``int64_t (*)(void *, int64_t, ...)`` passing one pointer.

    Use this only when the callee's fixed argument and return really are
    64-bit.  ``curl_easy_setopt`` uses the exact i32 variants below because
    its return and option types are C enums.
    """
    _trap("call_variadic_i64_ptr_i64_ptr")


def call_variadic_i64_ptr_i64_i64(fn: Any, arg0: Any, arg1: int, arg2: int) -> int:
    """Call ``int64_t (*)(void *, int64_t, ...)`` passing one integer."""
    _trap("call_variadic_i64_ptr_i64_i64")


def call_variadic_i32_ptr_i32_ptr(fn: Any, arg0: Any, arg1: int, arg2: Any) -> int:
    """Call ``int32_t (*)(void *, int32_t, ...)`` passing one pointer.

    This is the exact ABI shape of ``curl_easy_setopt`` for pointer-valued
    options: ``CURLcode`` and ``CURLoption`` are C enums (32-bit on the
    supported targets), while the option value is an unnamed argument.
    """
    _trap("call_variadic_i32_ptr_i32_ptr")


def call_variadic_i32_ptr_i32_i64(fn: Any, arg0: Any, arg1: int, arg2: int) -> int:
    """Call ``int32_t (*)(void *, int32_t, ...)`` passing one C ``long``."""
    _trap("call_variadic_i32_ptr_i32_i64")


def call_i64_ptr_i64_ptr_i64(
    fn: Any, arg0: Any, arg1: int, arg2: Any, arg3: int
) -> int:
    """Call ``int64_t (*)(void *, uint64_t, void *, uint64_t)``."""
    _trap("call_i64_ptr_i64_ptr_i64")


def call_i64_ptr_i64_i64_ptr(
    fn: Any, arg0: Any, arg1: int, arg2: int, arg3: Any
) -> int:
    """Call ``uint64_t (*)(void *, uint64_t, uint64_t, void *)``."""
    _trap("call_i64_ptr_i64_i64_ptr")


def call_i64_ptr_i64_ptr_ptr_ptr_ptr_bool(
    fn: Any,
    arg0: Any,
    arg1: int,
    arg2: Any,
    arg3: Any,
    arg4: Any,
    arg5: Any,
    arg6: int,
) -> int:
    """Call the fixed source-bridge ABI used by native accelerator shims."""
    _trap("call_i64_ptr_i64_ptr_ptr_ptr_ptr_bool")


def call_i64_ptr_ptr_ptr_ptr_ptr_bool(
    fn: Any,
    arg0: Any,
    arg1: Any,
    arg2: Any,
    arg3: Any,
    arg4: Any,
    arg5: int,
) -> int:
    """Call the fixed prebuilt-library bridge ABI."""
    _trap("call_i64_ptr_ptr_ptr_ptr_ptr_bool")


def dynamic_library_open(path: Any, target_platform: str = "") -> Any:
    """Open a local-binding library, optionally only on one target platform.

    A compile-time target mismatch returns NULL without a loader dependency.
    """
    _trap("dynamic_library_open")


def dynamic_library_open_global(path: Any) -> Any:
    """Open one platform dynamic library with immediate, global binding."""
    _trap("dynamic_library_open_global")


def dynamic_library_symbol(
    handle: Any, name: Any, target_platform: str = ""
) -> Any:
    """Resolve a symbol, optionally only on one target platform.

    A compile-time target mismatch returns NULL without a loader dependency.
    """
    _trap("dynamic_library_symbol")


def darwin_libsystem_symbol(name: Any) -> Any:
    """Resolve a symbol from Darwin's named libSystem owner.

    The compiler emits no dynamic-loader dependency on non-Darwin targets and
    returns NULL there.  A lock-free once cache owns one libSystem handle;
    racing initializers close their duplicate handle.  This is for host-ABI
    objects (for example ``FILE *``) that must not accidentally bind to a
    same-named freestanding pcc symbol.
    """
    _trap("darwin_libsystem_symbol")


def dynamic_library_close(handle: Any, target_platform: str = "") -> int:
    """Close a handle, optionally only on one target platform.

    A compile-time target mismatch is a successful no-op.
    """
    _trap("dynamic_library_close")


def kqueue_create() -> int:
    """Create a Darwin/BSD kqueue, returning a fd or negative errno."""
    _trap("kqueue_create")


def kevent_call(
    kq: int,
    changes: Any,
    nchanges: int,
    events: Any,
    nevents: int,
    timeout: Any,
) -> int:
    """Call Darwin/BSD kevent, returning a count or negative errno."""
    _trap("kevent_call")


def epoll_create1(flags: int) -> int:
    """Create a Linux epoll descriptor, returning its fd or negative errno."""
    _trap("epoll_create1")


def epoll_ctl(
    epoll_fd: int,
    operation: int,
    fd: int,
    events: int,
    token: int,
) -> int:
    """Apply one epoll registration with a compiler-owned 64-bit token."""
    _trap("epoll_ctl")


def epoll_wait(
    epoll_fd: int,
    events: Any,
    max_events: int,
    timeout_ms: int,
) -> int:
    """Fill packed Linux epoll_event records and return their count/errno."""
    _trap("epoll_wait")


def thread_safepoint() -> None:
    """Cooperate with the pcc runtime stop-the-world protocol."""
    _trap("thread_safepoint")


def gc_backend_current() -> int:
    """Return the initialized pcc GC backend kind (0 through 4)."""
    _trap("gc_backend_current")


__all__ = [
    "malloc",
    "cstr",
    "global_addr",
    "function_addr",
    "global_load_ptr",
    "global_store_ptr",
    "abi_constant",
    "define_global_ptr_null",
    "define_global_i64",
    "define_thread_local_ptr_null",
    "define_thread_local_i32",
    "define_global_i64_array",
    "define_global_struct_words",
    "define_global_ptr_to_global",
    "calloc",
    "realloc",
    "free",
    "ptr_add",
    "ptr_diff",
    "int_to_ptr",
    "ptr_to_int",
    "wrapping_mul_i64",
    "logical_shift_right_i64",
    "logical_shift_left_i64",
    "unsigned_div_i64",
    "unsigned_rem_i64",
    "unsigned_greater_i64",
    "mul_overflow_i64",
    "float_to_i64",
    "i64_to_float",
    "f64_div",
    "f64_signbit",
    "f64_bits",
    "f64_pair_make",
    "f64_pair_first",
    "f64_pair_second",
    "null",
    "ptr_eq",
    "ptr_is_null",
    "is_tagged_int",
    "tag_int",
    "untag_int",
    "load_i64",
    "load_i32",
    "load_i8",
    "load_ptr",
    "load_f64",
    "store_i64",
    "store_i32",
    "store_i8",
    "store_ptr",
    "store_f64",
    "memset",
    "memcpy",
    "memmove",
    "write",
    "read",
    "close",
    "seek_file",
    "open_readonly",
    "darwin_current_rss_bytes",
    "darwin_peak_rss_bytes",
    "open_file",
    "rename_file",
    "chmod_file",
    "sync_file",
    "socket_open",
    "socket_connect",
    "socket_bind",
    "socket_listen",
    "socket_setsockopt",
    "socket_getsockopt",
    "fd_control",
    "eventfd_create",
    "socket_send",
    "socket_recv",
    "socket_accept",
    "socket_shutdown",
    "socket_sockname",
    "socket_peername",
    "poll_fd",
    "poll_readable_pair",
    "getpid",
    "getcwd",
    "readlink",
    "mkdir",
    "unlinkat",
    "uname",
    "uname_field",
    "cpu_query",
    "clock_gettime",
    "nanosleep",
    "waitpid",
    "kill",
    "process_exit",
    "spawn_process",
    "spawn_process_pipe",
    "stack_alloc",
    "strlen",
    "getenv",
    "setenv",
    "unsetenv",
    "initial_environ",
    "access",
    "stat_kind",
    "stat_mtime",
    "target_sys_platform",
    "target_platform_machine",
    "atomic_load_i32",
    "atomic_load_i64",
    "atomic_store_i32",
    "atomic_store_i64",
    "atomic_rmw_i32",
    "atomic_rmw_i64",
    "atomic_cas_i32",
    "atomic_cas_i64",
    "atomic_fence",
    "atomic_test_and_set",
    "atomic_clear",
    "syscall6",
    "page_alloc",
    "page_free",
    "va_start",
    "va_arg_i64",
    "va_arg_i32",
    "va_arg_u32",
    "va_arg_ptr",
    "va_arg_f64",
    "va_cursor",
    "va_end",
    "call_ptr1",
    "call_ptr0",
    "call_void_ptr0",
    "call_void_ptr1",
    "call_void_ptr2",
    "call_void_ptr_i64_ptr",
    "call_ptr2",
    "call_ptr_ptr_i64",
    "call_ptr_ptr_ptr_i64_ptr",
    "call_ptr_ptr_ptr_i32",
    "call_ptr3",
    "call_ptr4",
    "call_i64_i64",
    "call_i64_i64_ptr",
    "call_i32_ptr1",
    "call_i32_ptr_i64",
    "call_i32_ptr_i32",
    "call_i32_ptr_i32_i32",
    "call_i32_ptr_i32_i32_i32",
    "call_i32_ptr_i32_i32_i32_i32_i32_ptr_i32",
    "call_i32_i32_ptr_i64",
    "call_i32_i64_i64_ptr",
    "call_i32_i64_i32_i64",
    "call_i64_ptr1",
    "call_i64_ptr_i64",
    "call_i64_ptr_ptr_ptr_i64",
    "call_i64_ptr2",
    "call_i64_ptr3",
    "call_i64_i64_i64_ptr",
    "call_i64_ptr_i64_ptr",
    "call_i64_ptr_i64_i64",
    "call_variadic_i64_ptr_i64_ptr",
    "call_variadic_i64_ptr_i64_i64",
    "call_variadic_i32_ptr_i32_ptr",
    "call_variadic_i32_ptr_i32_i64",
    "darwin_errno_location",
    "call_i64_ptr_i64_ptr_i64",
    "call_i64_ptr_i64_i64_ptr",
    "call_i64_ptr_i64_ptr_ptr_ptr_ptr_bool",
    "call_i64_ptr_ptr_ptr_ptr_ptr_bool",
    "dynamic_library_open",
    "dynamic_library_open_global",
    "dynamic_library_symbol",
    "darwin_libsystem_symbol",
    "dynamic_library_close",
    "kqueue_create",
    "kevent_call",
    "epoll_create1",
    "epoll_ctl",
    "epoll_wait",
    "thread_safepoint",
    "gc_backend_current",
]
