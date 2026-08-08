"""Freestanding path-query primitives authored in pcc-Python.

Darwin lowers these helpers to the explicitly named libSystem ABI. Linux
x86_64 lowers the same source to raw syscalls, so the generated object has no
libc dependency there.
"""

from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    access,
    atomic_rmw_i64,
    define_global_i64,
    getcwd,
    getpid,
    global_addr,
    load_i8,
    logical_shift_right_i64,
    mkdir,
    null,
    ptr_add,
    ptr_is_null,
    readlink,
    stack_alloc,
    stat_kind,
    stat_mtime,
    store_i8,
)

__pcc_freestanding__ = True


define_global_i64("pcc_platform_mkdtemp_counter", 0)


@c_abi_export("pcc_platform_access")
def pcc_platform_access(path, mode: i64) -> i64:
    return access(path, mode)


@c_abi_export("pcc_platform_getcwd")
def pcc_platform_getcwd(buffer, size: i64):
    return getcwd(buffer, size)


@c_abi_export("pcc_platform_stat_kind")
def pcc_platform_stat_kind(path) -> i64:
    return stat_kind(path)


@c_abi_export("pcc_platform_stat_mtime")
def pcc_platform_stat_mtime(path) -> float:
    return stat_mtime(path)


@c_abi_export("pcc_platform_fs_bounded_cstr_len")
def _bounded_cstr_len(value, limit: i64) -> i64:
    offset: i64 = 0
    while offset < limit:
        if load_i8(value, offset) == 0:
            return offset
        offset = offset + 1
    return -1


@c_abi_export("pcc_platform_fs_copy_cstr")
def _copy_cstr(output, value, capacity: i64) -> i64:
    if capacity <= 0:
        return -1
    offset: i64 = 0
    while offset + 1 < capacity:
        byte = load_i8(value, offset)
        store_i8(output, offset, byte)
        if byte == 0:
            return offset
        offset = offset + 1
    store_i8(output, capacity - 1, 0)
    return -1


@c_abi_export("pcc_platform_fs_pop_component")
def _pop_component(output, length: i64) -> i64:
    if length <= 1:
        store_i8(output, 1, 0)
        return 1
    offset = length - 1
    while offset > 0 and load_i8(output, offset) != 47:
        offset = offset - 1
    if offset <= 0:
        offset: i64 = 1
    store_i8(output, offset, 0)
    return offset


@c_abi_export("pcc_platform_realpath")
def pcc_platform_realpath(path, output, size: i64):
    # Walk components and follow relative or absolute symlink targets with
    # readlink(2). Keep the fixed 40-link POSIX loop bound and fail instead of
    # truncating the caller's buffer.
    if ptr_is_null(path) or ptr_is_null(output) or size < 2:
        return null()
    if load_i8(path, 0) == 0:
        return null()

    pending_a = stack_alloc(8192)
    pending_b = stack_alloc(8192)
    pending = pending_a
    scratch = pending_b
    if load_i8(path, 0) == 47:
        if _copy_cstr(pending, path, 8192) < 0:
            return null()
    else:
        if ptr_is_null(getcwd(pending, 8192)):
            return null()
        cwd_len = _bounded_cstr_len(pending, 8192)
        if cwd_len < 0 or cwd_len + 2 >= 8192:
            return null()
        if cwd_len > 1 and load_i8(pending, cwd_len - 1) != 47:
            store_i8(pending, cwd_len, 47)
            cwd_len = cwd_len + 1
        source_offset: i64 = 0
        while True:
            byte = load_i8(path, source_offset)
            if cwd_len + source_offset + 1 >= 8192:
                return null()
            store_i8(pending, cwd_len + source_offset, byte)
            if byte == 0:
                break
            source_offset = source_offset + 1

    store_i8(output, 0, 47)
    store_i8(output, 1, 0)
    resolved_len: i64 = 1
    cursor: i64 = 0
    link_count: i64 = 0

    while cursor < 8192:
        while load_i8(pending, cursor) == 47:
            cursor = cursor + 1
        if load_i8(pending, cursor) == 0:
            return output

        component_start = cursor
        while load_i8(pending, cursor) != 0 and load_i8(pending, cursor) != 47:
            cursor = cursor + 1
        component_len = cursor - component_start

        if component_len == 1 and load_i8(pending, component_start) == 46:
            continue
        if (
            component_len == 2
            and load_i8(pending, component_start) == 46
            and load_i8(pending, component_start + 1) == 46
        ):
            resolved_len = _pop_component(output, resolved_len)
            continue

        parent_len = resolved_len
        if resolved_len > 1:
            if resolved_len + 1 >= size:
                return null()
            store_i8(output, resolved_len, 47)
            resolved_len = resolved_len + 1
        copy_offset: i64 = 0
        while copy_offset < component_len:
            if resolved_len + 1 >= size:
                return null()
            store_i8(
                output,
                resolved_len,
                load_i8(pending, component_start + copy_offset),
            )
            resolved_len = resolved_len + 1
            copy_offset = copy_offset + 1
        store_i8(output, resolved_len, 0)

        link_len = readlink(output, scratch, 8191)
        if link_len >= 0:
            if link_len >= 8191 or link_count >= 40:
                return null()
            store_i8(scratch, link_len, 0)
            absolute_target = load_i8(scratch, 0) == 47
            if absolute_target:
                resolved_len: i64 = 1
                store_i8(output, 1, 0)
            else:
                resolved_len = parent_len
                store_i8(output, resolved_len, 0)

            new_len = link_len
            remainder = cursor
            while load_i8(pending, remainder) != 0:
                if new_len + 1 >= 8192:
                    return null()
                store_i8(scratch, new_len, load_i8(pending, remainder))
                new_len = new_len + 1
                remainder = remainder + 1
            store_i8(scratch, new_len, 0)

            old_pending = pending
            pending = scratch
            scratch = old_pending
            cursor: i64 = 0
            link_count = link_count + 1

    return null()


@c_abi_export("pcc_platform_mkdtemp")
def pcc_platform_mkdtemp(path_template):
    if ptr_is_null(path_template):
        return null()
    length = _bounded_cstr_len(path_template, 8192)
    if length < 6:
        return null()
    check: i64 = 0
    while check < 6:
        if load_i8(path_template, length - 1 - check) != 88:
            return null()
        check = check + 1

    ticket = atomic_rmw_i64(
        "add",
        global_addr("pcc_platform_mkdtemp_counter"),
        0,
        1,
        "relaxed",
    )
    seed = getpid() * 1103515245 + ticket * 2654435761
    attempt: i64 = 0
    while attempt < 256:
        value = seed + attempt * 114007148193231
        digit_offset: i64 = 0
        while digit_offset < 6:
            digit = value & 31
            byte = digit + 48
            if digit >= 10:
                byte = digit + 87
            store_i8(path_template, length - 1 - digit_offset, byte)
            value = logical_shift_right_i64(value, 5)
            digit_offset = digit_offset + 1
        if mkdir(path_template, 448) == 0:
            return path_template
        attempt = attempt + 1
    return null()
