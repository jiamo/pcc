"""Linux x86_64 process entry for C programs using pcc-Python libc.

The self backend receives the kernel's original stack pointer, reconstructs
the SysV ``argc``/``argv``/``envp`` values, initializes pcc's owned environment
table, calls the C translation unit's ``main``, and terminates with a raw
``exit_group`` syscall.  No C or assembly startup source participates.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int, c_ptr, extern
from pcc.unsafe import (
    define_global_null_ptr_array,
    global_addr,
    int_to_ptr,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    null,
    process_exit,
    ptr_add,
    ptr_is_null,
    store_i8,
    store_ptr,
    syscall6,
)

__pcc_freestanding__ = True


# Linux x86_64 uses ELF TLS variant II: compiler-emitted local-exec accesses
# load the thread pointer from ``%fs:0`` and address TLS at negative offsets
# from it.  A no-libc ``_start`` has no loader to create that initial TCB, so
# reserve one compiler-owned block, populate it from the final ELF's PT_TLS,
# and install its final word as the self pointer before any runtime call.  The
# boundary is deliberately finite: the image may occupy at most 4,088 bytes
# and require no more than pointer alignment.
define_global_null_ptr_array("pcc_linux_initial_tls_reserve", 512)


c_main = extern("main", (c_int, c_ptr, c_ptr), c_int)
platform_env_init = extern("pcc_platform_env_init", (c_ptr,), c_int)


@c_abi_export("pcc_linux_initial_tls_setup")
def pcc_linux_initial_tls_setup(initial_stack: c_ptr) -> i64:
    # Decode argc/argv/envp just far enough to reach the kernel auxv.  The
    # bounds are corruption guards, not normal limits.
    argc: i64 = load_i64(initial_stack, 0)
    if argc < 0 or argc >= 1048576:
        return -1
    argv = ptr_add(initial_stack, 8)
    envp = ptr_add(argv, (argc + 1) * 8)
    env_index: i64 = 0
    while env_index < 1048576:
        if ptr_is_null(load_ptr(envp, env_index * 8)) != 0:
            break
        env_index = env_index + 1
    if env_index >= 1048576:
        return -1

    # Elf64_auxv_t is two u64 words.  AT_PHDR/AT_PHENT/AT_PHNUM describe the
    # final executable, so this remains correct when archive selection changes
    # the linked TLS members or their layout.
    auxv = ptr_add(envp, (env_index + 1) * 8)
    phdr_value: i64 = 0
    phent: i64 = 0
    phnum: i64 = 0
    aux_index: i64 = 0
    aux_done: i64 = 0
    while aux_index < 256:
        aux_type: i64 = load_i64(auxv, aux_index * 16)
        aux_value: i64 = load_i64(auxv, aux_index * 16 + 8)
        if aux_type == 0:  # AT_NULL
            aux_done: i64 = 1
            break
        if aux_type == 3:  # AT_PHDR
            phdr_value = aux_value
        elif aux_type == 4:  # AT_PHENT
            phent = aux_value
        elif aux_type == 5:  # AT_PHNUM
            phnum = aux_value
        aux_index = aux_index + 1
    if aux_done == 0 or phdr_value == 0:
        return -1
    if phent < 56 or phent > 4096 or phnum <= 0 or phnum > 4096:
        return -1

    # Elf64_Phdr offsets: type@0, vaddr@16, filesz@32, memsz@40, align@48.
    # GNU ld emits the x86_64 local-exec TLS variables as variant II: the
    # rounded image immediately precedes the thread pointer.
    phdr = int_to_ptr(phdr_value)
    tls_template = null()
    tls_file_size: i64 = 0
    tls_memory_size: i64 = 0
    tls_alignment: i64 = 1
    tls_found: i64 = 0
    ph_index: i64 = 0
    while ph_index < phnum:
        program_header = ptr_add(phdr, ph_index * phent)
        if load_i32(program_header, 0) == 7:  # PT_TLS
            if tls_found != 0:
                return -1
            tls_found: i64 = 1
            tls_template = int_to_ptr(load_i64(program_header, 16))
            tls_file_size = load_i64(program_header, 32)
            tls_memory_size = load_i64(program_header, 40)
            tls_alignment = load_i64(program_header, 48)
        ph_index = ph_index + 1

    rounded_size: i64 = 0
    if tls_found != 0:
        if tls_file_size < 0 or tls_memory_size < 0:
            return -1
        if tls_file_size > tls_memory_size or tls_memory_size > 4088:
            return -1
        if tls_alignment <= 0 or tls_alignment > 8:
            return -1
        if (tls_alignment & (tls_alignment - 1)) != 0:
            return -1
        rounded_size = (tls_memory_size + tls_alignment - 1) & (0 - tls_alignment)
        if rounded_size > 4088:
            return -1

    thread_pointer = ptr_add(global_addr("pcc_linux_initial_tls_reserve"), 4088)
    tls_begin = ptr_add(thread_pointer, 0 - rounded_size)
    zero_index: i64 = 0
    while zero_index < rounded_size:
        store_i8(tls_begin, zero_index, 0)
        zero_index = zero_index + 1
    copy_index: i64 = 0
    while copy_index < tls_file_size:
        store_i8(
            tls_begin,
            copy_index,
            load_i8(tls_template, copy_index),
        )
        copy_index = copy_index + 1
    store_ptr(thread_pointer, 0, thread_pointer)
    # SYS_arch_prctl(ARCH_SET_FS, thread_pointer).
    return syscall6(158, 4098, thread_pointer, 0, 0, 0, 0)


@c_abi_export("_start")
def pcc_c_linux_start(initial_stack: c_ptr) -> None:
    # This is the initial-thread half of the compiler-owned TLS contract; the
    # pthread runtime remains responsible for installing a distinct block for
    # every spawned thread.
    if pcc_linux_initial_tls_setup(initial_stack) != 0:
        process_exit(71)
    argc: i64 = load_i64(initial_stack, 0)
    status: i64 = 64
    if argc >= 0:
        argv = ptr_add(initial_stack, 8)
        envp = ptr_add(argv, (argc + 1) * 8)
        if platform_env_init(envp) == 0:
            status = c_main(argc, argv, envp)
        else:
            status: i64 = 70
    process_exit(status)
