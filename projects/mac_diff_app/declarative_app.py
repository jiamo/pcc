"""Canonical declarative mac-diff application and deterministic canary.

The file/diff model is shared by the headless acceptance and the native
AppKit/Metal entrypoint.  UI updates are committed through the component and
scheduler owners; the app never edits committed row nodes in a frame loop.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    calloc,
    cstr,
    define_global_i64,
    function_addr,
    global_addr,
    int_to_ptr,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
    wrapping_mul_i64,
)

import pcc_gui_high as gui
from diff_core import pcc_gui_diff_compute, pcc_gui_diff_init


open_fn = extern("open", (c_ptr, c_int32), c_int64)
read_fn = extern("read", (c_int64, c_ptr, c_int64), c_int64)
close_fn = extern("close", (c_int64,), c_int64)
program_argc = extern("py_program_argc", (), c_int32)
program_argv = extern("py_program_argv", (c_int64,), c_ptr)

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_destroy = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)
kit_valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
kit_rect = extern("pcc_kit_rect", (c_int64,c_int64,c_int64,c_int64,c_int64,c_int32), c_void)
kit_render = extern("pcc_kit_render", (c_int64,c_ptr,c_ptr,c_ptr,c_ptr,c_ptr), c_void)

components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
binding_count = extern("pcc_gui_component_binding_count", (c_int64,), c_int64)
node_for_key = extern("pcc_gui_component_node_for_key", (c_int64,c_int64), c_int64)
state_value = extern("pcc_gui_component_state_slot_value", (c_int64,c_int32), c_int64)

scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
register_reducer = extern("pcc_gui_scheduler_register_reducer", (c_int32,c_ptr,c_int32), c_int32)
enqueue_reduce = extern("pcc_gui_scheduler_enqueue_reduce", (c_int64,c_int32,c_int32,c_int32,c_int64,c_ptr), c_int32)
run_sync = extern("pcc_gui_scheduler_run_sync", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
run_budgeted = extern("pcc_gui_scheduler_run_budgeted", (c_int64,c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
scheduler_pending = extern("pcc_gui_scheduler_pending", (c_int64,), c_int32)
restart_count = extern("pcc_gui_scheduler_restart_count", (c_int64,), c_int32)

events_init = extern("pcc_gui_events_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
register_listener_callback = extern("pcc_gui_events_register_listener_callback", (c_int32,c_ptr), c_int32)
register_effect_callback = extern("pcc_gui_events_register_effect_callback", (c_int32,c_ptr), c_int32)
listen = extern("pcc_gui_events_listen", (c_int64,c_int64,c_int32,c_int32,c_int32,c_int64), c_int32)
dispatch = extern("pcc_gui_events_dispatch", (c_int64,c_int64,c_int64,c_int32,c_ptr,c_ptr,c_int32,c_ptr), c_int32)
register_effect = extern("pcc_gui_events_register_effect", (c_int64,c_int32,c_int64,c_int32,c_int64), c_int32)
listener_count = extern("pcc_gui_events_listener_count", (c_int64,), c_int32)

theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
theme_set = extern("pcc_gui_theme_set_token", (c_ptr,c_int32,c_int32,c_int64), c_int32)
theme_activate = extern("pcc_gui_theme_activate", (c_ptr,), c_int32)
style_init = extern("pcc_gui_style_init", (c_int64,c_int64), c_int32)
style_compiler_init = extern("pcc_gui_style_compiler_init", (c_int64,), c_int32)
register_named_utility = extern("pcc_gui_style_register_named_utility", (c_int32,c_int32,c_int32,c_int32,c_int32), c_int32)
compile_style = extern("pcc_gui_style_compile", (c_ptr,c_int64), c_int32)
cached_operations = extern("pcc_gui_style_cached_operations", (c_ptr,c_int64,c_ptr,c_int32), c_int32)
apply_class = extern("pcc_gui_style_apply_class", (c_int64,c_int64,c_ptr,c_int64), c_int32)
style_dirty = extern("pcc_gui_style_component_dirty", (c_int64,), c_int32)
style_did_commit = extern("pcc_gui_style_component_did_commit", (c_int64,), c_int32)

commands_init = extern("pcc_gui_commands_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
managed_state_set = extern("pcc_gui_managed_state_set", (c_int64,c_int64,c_int32,c_int64,c_int64), c_int32)
managed_state_get = extern("pcc_gui_managed_state_get", (c_int64,c_int64,c_ptr), c_int32)
managed_binding_add = extern("pcc_gui_managed_binding_add", (c_int64,c_int64,c_int64,c_int64), c_int32)
command_register = extern("pcc_gui_commands_register", (c_int32,c_ptr,c_int32,c_int32,c_int64,c_int64), c_int32)
command_invoke = extern("pcc_gui_commands_invoke", (c_ptr,), c_int32)
resolve_result = extern("pcc_gui_commands_resolve_result", (c_int64,c_ptr,c_int64), c_int32)
resolve_error = extern("pcc_gui_commands_resolve_error", (c_int64,c_int32,c_int64), c_int32)
command_completion = extern("pcc_gui_commands_completion", (c_int64,c_ptr), c_int32)
release_completion = extern("pcc_gui_commands_release_completion", (c_int64,), c_int32)

app_init = extern("pcc_gui_app_lifecycle_init", (c_int64,c_ptr,c_int64,c_int64,c_ptr,c_ptr), c_int32)
app_startup = extern("pcc_gui_app_lifecycle_post_startup", (), c_int32)
app_post = extern("pcc_gui_app_lifecycle_post", (c_int32,c_int64,c_ptr,c_int64,c_int32,c_int32), c_int32)
app_drain = extern("pcc_gui_app_lifecycle_drain", (c_int32,c_ptr), c_int32)
app_state = extern("pcc_gui_app_lifecycle_state", (), c_int32)
terminal_count = extern("pcc_gui_app_lifecycle_terminal_count", (), c_int32)


IDS_L = calloc(4096, 8)
IDS_R = calloc(4096, 8)
BUF_L = calloc(65536, 1)
BUF_R = calloc(65536, 1)
LINES_L = calloc(4096, 16)
LINES_R = calloc(4096, 16)
OPS = calloc(256, 24)
USED = calloc(512, 1)

define_global_i64("mac_diff_n", 0)
define_global_i64("mac_diff_root", -1)
define_global_i64("mac_diff_toolbar", -1)
define_global_i64("mac_diff_left", -1)
define_global_i64("mac_diff_right", -1)
define_global_i64("mac_diff_overview", -1)
define_global_i64("mac_diff_status", -1)
define_global_i64("mac_diff_descriptors", 0)
define_global_i64("mac_diff_effects", 0)
define_global_i64("mac_diff_effect_count", 0)
define_global_i64("mac_diff_error", 0)
define_global_i64("mac_diff_listener_calls", 0)
define_global_i64("mac_diff_effect_calls", 0)
define_global_i64("mac_diff_exit_requests", 0)
define_global_i64("mac_diff_exit_ok", 0)
define_global_i64("mac_diff_work_drains", 0)


def _g(name: str) -> int:
    return load_i64(global_addr(name), 0)


def _setg(name: str, value: int) -> None:
    store_i64(global_addr(name), 0, value)


def _raw_len(value) -> int:
    n = 0
    while load_i8(value, n) != 0:
        n = n + 1
    return n


def _read_lines(path, buf, ids, lines) -> int:
    fd = open_fn(path, 0)
    if fd < 0:
        return -1
    total = read_fn(fd, buf, 65536)
    close_fn(fd)
    if total < 0:
        return -1
    count = 0
    start = 0
    h = -7046029254386353131
    i = 0
    while i < total:
        b = load_i8(buf, i) & 255
        if b == 10:
            if count < 4096:
                store_i64(lines, count * 16, start)
                store_i64(lines, count * 16 + 8, i - start)
                store_i64(ids, count * 8, h)
            count = count + 1
            start = i + 1
            h = -7046029254386353131
        else:
            h = wrapping_mul_i64(h, 31) + b
        i = i + 1
    if start < total:
        if count < 4096:
            store_i64(lines, count * 16, start)
            store_i64(lines, count * 16 + 8, total - start)
            store_i64(ids, count * 8, h)
        count = count + 1
    return count


def _common_prefix(pa, la: int, pb, lb: int) -> int:
    limit = la
    if lb < limit:
        limit = lb
    i = 0
    while i < limit and (load_i8(pa, i) & 255) == (load_i8(pb, i) & 255):
        i = i + 1
    return i


def _common_suffix(pa, la: int, pb, lb: int, prefix: int) -> int:
    limit = la - prefix
    if lb - prefix < limit:
        limit = lb - prefix
    i = 0
    while i < limit and (load_i8(pa, la - 1 - i) & 255) == (load_i8(pb, lb - 1 - i) & 255):
        i = i + 1
    return i


def _similarity(left_line: int, right_line: int) -> int:
    lp = ptr_add(BUF_L, load_i64(LINES_L, left_line * 16))
    ll = load_i64(LINES_L, left_line * 16 + 8)
    rp = ptr_add(BUF_R, load_i64(LINES_R, right_line * 16))
    rl = load_i64(LINES_R, right_line * 16 + 8)
    prefix = _common_prefix(lp, ll, rp, rl)
    return prefix + _common_suffix(lp, ll, rp, rl, prefix)


def _repair_ops(count: int) -> int:
    """Keep the accepted kit_window similarity-based 13-op/5-change shape."""
    write = 0
    read = 0
    while read < count:
        kind = load_i64(OPS, read * 24)
        if kind == 1:
            delete_start = read
            while read < count and load_i64(OPS, read * 24) == 1:
                read = read + 1
            delete_end = read
            insert_start = read
            while read < count and load_i64(OPS, read * 24) == 2:
                read = read + 1
            deletes = delete_end - delete_start
            inserts = read - insert_start
            i = 0
            while i < inserts and i < 512:
                store_i8(USED, i, 0)
                i = i + 1
            i = 0
            while i < deletes:
                left_line = load_i64(OPS, (delete_start + i) * 24 + 8)
                best = -1
                best_score = 0
                j = 0
                while j < inserts:
                    if j < 512 and load_i8(USED, j) == 0:
                        right_line = load_i64(OPS, (insert_start + j) * 24 + 16)
                        score = _similarity(left_line, right_line)
                        shortest = load_i64(LINES_L, left_line * 16 + 8)
                        right_length = load_i64(LINES_R, right_line * 16 + 8)
                        if right_length < shortest:
                            shortest = right_length
                        threshold = shortest // 3
                        if threshold < 2:
                            threshold = 2
                        if score >= threshold and score > best_score:
                            best = j
                            best_score = score
                    j = j + 1
                if best >= 0:
                    store_i8(USED, best, 1)
                    store_i64(OPS, write * 24, 3)
                    store_i64(OPS, write * 24 + 8, left_line)
                    store_i64(OPS, write * 24 + 16, load_i64(OPS, (insert_start + best) * 24 + 16))
                else:
                    store_i64(OPS, write * 24, 1)
                    store_i64(OPS, write * 24 + 8, left_line)
                    store_i64(OPS, write * 24 + 16, -1)
                write = write + 1
                i = i + 1
            j = 0
            while j < inserts:
                if j >= 512 or load_i8(USED, j) == 0:
                    store_i64(OPS, write * 24, 2)
                    store_i64(OPS, write * 24 + 8, -1)
                    store_i64(OPS, write * 24 + 16, load_i64(OPS, (insert_start + j) * 24 + 16))
                    write = write + 1
                j = j + 1
        else:
            store_i64(OPS, write * 24, kind)
            store_i64(OPS, write * 24 + 8, load_i64(OPS, read * 24 + 8))
            store_i64(OPS, write * 24 + 16, load_i64(OPS, read * 24 + 16))
            write = write + 1
            read = read + 1
    return write


def _descriptor_rect(arena, index: int, component: int, key: int, color: int, x: int, y: int, width: int, height: int, listener: int) -> None:
    record = ptr_add(arena, index * 72)
    store_i64(record, 0, component)
    store_i64(record, 8, key)
    store_i32(record, 16, 1)
    store_i32(record, 20, color)
    store_i64(record, 24, 1)
    store_i64(record, 32, x)
    store_i64(record, 40, y)
    store_i64(record, 48, width)
    store_i64(record, 56, height)
    store_i64(record, 64, listener)


def _descriptor_text(arena, index: int, component: int, key: int, text, length: int, font: int, color: int) -> None:
    record = ptr_add(arena, index * 72)
    store_i64(record, 0, component)
    store_i64(record, 8, key)
    store_i32(record, 16, 2)
    store_i32(record, 20, 0)
    store_i64(record, 24, 1)
    store_i64(record, 32, ptr_to_int(text))
    store_i64(record, 40, length)
    store_i64(record, 48, font)
    store_i64(record, 56, color)
    store_i64(record, 64, 0)


@c_abi_typed_export("mac_diff_add", "i32", ("i64", "i64", "ptr"))
def mac_diff_add(old: int, operand: int, result_out) -> int:
    store_i64(result_out, 0, old + operand)
    return 0


@c_abi_typed_export("mac_diff_toolbar_render", "i32", ("ptr",))
def mac_diff_toolbar_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    capacity = load_i32(context, 56)
    state = load_ptr(context, 32)
    expanded = 1 if load_i64(state, 8) >= 6 else 0
    count = 9 if expanded != 0 else 8
    if capacity < count:
        return -101
    _descriptor_rect(arena, 0, component, 10, 0xFFE8E8E8, 0, 0, 900, 48, 101)
    _descriptor_rect(arena, 1, component, 11, 0xFFF5F5F5, 16, 8, 90, 30, 0)
    _descriptor_text(arena, 2, component, 12, cstr("Open"), 4, 13, 0xFF333333)
    _descriptor_rect(arena, 3, component, 13, 0xFFF5F5F5, 116, 8, 110, 30, 0)
    _descriptor_text(arena, 4, component, 14, cstr("Diffs"), 5, 13, 0xFF333333)
    _descriptor_rect(arena, 5, component, 15, 0xFFF5F5F5, 240, 8, 80, 30, 0)
    _descriptor_text(arena, 6, component, 16, cstr("Prev"), 4, 13, 0xFF333333)
    _descriptor_text(arena, 7, component, 17, cstr("Next"), 4, 13, 0xFF333333)
    if expanded != 0:
        _descriptor_text(arena, 8, component, 18, cstr("Queued"), 6, 11, 0xFF225522)
    store_i32(load_ptr(context, 64), 0, count)
    return count


def _pane_render(context, side: int) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    count = _g("mac_diff_n")
    if count > 13:
        count = 13
    if load_i32(context, 56) < count:
        return -101
    i = 0
    while i < count:
        kind = load_i64(OPS, i * 24)
        color = 0xFFF8F8F8
        if kind == 3:
            color = 0xFFFFC94A
        elif kind == 1:
            color = 0xFFF0C8C8
        elif kind == 2:
            color = 0xFFC8F0C8
        if (side == 0 and load_i64(OPS, i * 24 + 8) < 0) or (side == 1 and load_i64(OPS, i * 24 + 16) < 0):
            color = 0xFFFFFFFF
        _descriptor_rect(arena, i, component, 1000 + i, color, 0, i * 24, 420, 22, 0)
        i = i + 1
    store_i32(load_ptr(context, 64), 0, count)
    return count


@c_abi_typed_export("mac_diff_left_render", "i32", ("ptr",))
def mac_diff_left_render(context) -> int:
    return _pane_render(context, 0)


@c_abi_typed_export("mac_diff_right_render", "i32", ("ptr",))
def mac_diff_right_render(context) -> int:
    return _pane_render(context, 1)


@c_abi_typed_export("mac_diff_overview_render", "i32", ("ptr",))
def mac_diff_overview_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    limit = load_i32(context, 56)
    total = _g("mac_diff_n")
    count = 0
    i = 0
    while i < total and count < limit:
        kind = load_i64(OPS, i * 24)
        if kind != 0:
            _descriptor_rect(arena, count, component, 2000 + i, 0xFFC0A000, 0, i * 12, 8, 6, 0)
            count = count + 1
        i = i + 1
    store_i32(load_ptr(context, 64), 0, count)
    return count


@c_abi_typed_export("mac_diff_status_render", "i32", ("ptr",))
def mac_diff_status_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    if load_i32(context, 56) < 2:
        return -101
    _descriptor_rect(arena, 0, component, 3000, 0xFFD8D8D8, 0, 0, 900, 34, 0)
    _descriptor_text(arena, 1, component, 3001, cstr("pcc declarative diff"), 20, 13, 0xFF222222)
    store_i32(load_ptr(context, 64), 0, 2)
    return 2


@c_abi_typed_export("mac_diff_listener", "i32", ("i64", "i64", "ptr"))
def mac_diff_listener(listener_id: int, target: int, event) -> int:
    if listener_id != 101 or target != _g("mac_diff_toolbar"):
        return -1
    _setg("mac_diff_listener_calls", _g("mac_diff_listener_calls") + 1)
    return enqueue_reduce(target, 0, 0, 1, 1, null())


@c_abi_typed_export("mac_diff_effect", "i32", ("i64", "i32", "i64"))
def mac_diff_effect(component: int, phase: int, payload: int) -> int:
    _setg("mac_diff_effect_calls", _g("mac_diff_effect_calls") + 1)
    return 0


@c_abi_typed_export("mac_diff_command_result", "i32", ("ptr", "i64"))
def mac_diff_command_result(invoke, resolver: int) -> int:
    value = stack_alloc(8)
    payload = load_ptr(invoke, 24)
    if ptr_is_null(payload) or load_i64(invoke, 32) != 8:
        return -1
    store_i64(value, 0, load_i64(payload, 0) + 1)
    return resolve_result(resolver, value, 8)


@c_abi_typed_export("mac_diff_command_error", "i32", ("ptr", "i64"))
def mac_diff_command_error(invoke, resolver: int) -> int:
    return resolve_error(resolver, -116, load_i32(invoke, 8))


@c_abi_typed_export("mac_diff_root_release", "i64", ("i64",))
def mac_diff_root_release(root: int) -> int:
    return 0 if kit_destroy(root) > 0 else -1


def _run_component(component: int, budget: int) -> int:
    descriptors = int_to_ptr(_g("mac_diff_descriptors"))
    effects = int_to_ptr(_g("mac_diff_effects"))
    effect_count = int_to_ptr(_g("mac_diff_effect_count"))
    error = int_to_ptr(_g("mac_diff_error"))
    if budget < 0:
        return run_sync(component, descriptors, 64, effects, 128, effect_count, error)
    return run_budgeted(component, budget, descriptors, 64, effects, 128, effect_count, error)


@c_abi_typed_export("mac_diff_work_drain", "i32", ("ptr",))
def mac_diff_work_drain(unused) -> int:
    toolbar = _g("mac_diff_toolbar")
    while scheduler_pending(toolbar) > 0:
        if _run_component(toolbar, 128) != 0:
            return -1
    _setg("mac_diff_work_drains", _g("mac_diff_work_drains") + 1)
    return 0


@c_abi_typed_export("mac_diff_app_event", "i32", ("ptr",))
def mac_diff_app_event(event) -> int:
    kind = load_i32(event, 8)
    if kind == 7:
        count = _g("mac_diff_exit_requests")
        _setg("mac_diff_exit_requests", count + 1)
        return 1 if count == 0 else 0
    if kind == 8:
        ok = 1
        if component_valid(_g("mac_diff_toolbar")) != 0:
            ok = 0
        if listener_count(_g("mac_diff_toolbar")) != 0:
            ok = 0
        if kit_valid(_g("mac_diff_root")) != 0:
            ok = 0
        _setg("mac_diff_exit_ok", ok)
    return 0


def _slot(value: int):
    record = stack_alloc(24)
    store_i32(record, 0, 1)
    store_i64(record, 8, value)
    store_i64(record, 16, 0)
    return record


def _mount_component(parent: int, root: int, callback: int) -> int:
    return mount(parent, root, callback, null(), 0, _slot(0), 1)


def _invoke_command(command: int, target: int, request: int, resolver: int, value: int, expect_error: int) -> int:
    packet = stack_alloc(64)
    error = stack_alloc(24)
    payload = stack_alloc(8)
    out = stack_alloc(48)
    store_i64(payload, 0, value)
    store_i64(packet, 0, request)
    store_i32(packet, 8, command)
    store_i32(packet, 12, 0)
    store_i64(packet, 16, target)
    store_ptr(packet, 24, payload if expect_error == 0 else null())
    store_i64(packet, 32, 8 if expect_error == 0 else 0)
    store_i64(packet, 40, 0)
    store_i64(packet, 48, resolver)
    store_ptr(packet, 56, error)
    if command_invoke(packet) != 0 or command_completion(resolver, out) != 0:
        return -1
    if expect_error == 0:
        if load_i32(out, 8) != 1 or load_i64(load_ptr(out, 16), 0) != value + 1:
            return -1
    elif load_i32(out, 8) != 2 or load_i32(out, 12) != -116:
        return -1
    return release_completion(resolver)


def _load_diff() -> int:
    left = cstr("samples/left.txt")
    right = cstr("samples/right.txt")
    if program_argc() >= 3:
        left = program_argv(1)
        right = program_argv(2)
    left_count = _read_lines(left, BUF_L, IDS_L, LINES_L)
    right_count = _read_lines(right, BUF_R, IDS_R, LINES_R)
    if left_count <= 0 or right_count <= 0:
        return -1
    if pcc_gui_diff_init(256) != 0:
        return -1
    count = pcc_gui_diff_compute(IDS_L, left_count, IDS_R, right_count, OPS, 256)
    if count < 0:
        return -1
    count = _repair_ops(count)
    _setg("mac_diff_n", count)
    equal = 0
    deleted = 0
    inserted = 0
    changed = 0
    i = 0
    while i < count:
        kind = load_i64(OPS, i * 24)
        if kind == 0:
            equal = equal + 1
        elif kind == 1:
            deleted = deleted + 1
        elif kind == 2:
            inserted = inserted + 1
        else:
            changed = changed + 1
        i = i + 1
    print("PCC_MAC_DIFF_SMOKE left_rows=", left_count, " right_rows=", right_count, " ops=", count, " equal=", equal, " deleted=", deleted, " inserted=", inserted, " changed=", changed)
    return 0


def run_app(hardware: int) -> int:
    if _load_diff() != 0:
        return 1
    if kit_init(192) != 0 or components_init(8, 8, 160) != 0:
        return 2
    if scheduler_init(8, 64, 4) != 0 or events_init(32, 8, 16, 64) != 0:
        return 3
    if commands_init(16, 8, 16, 4) != 0:
        return 4
    if style_init(16, 64) != 0 or style_compiler_init(8) != 0:
        return 5
    if register_render(1, function_addr("mac_diff_toolbar_render")) != 0:
        return 6
    if register_render(2, function_addr("mac_diff_left_render")) != 0:
        return 6
    if register_render(3, function_addr("mac_diff_right_render")) != 0:
        return 6
    if register_render(4, function_addr("mac_diff_overview_render")) != 0:
        return 6
    if register_render(5, function_addr("mac_diff_status_render")) != 0:
        return 6
    if register_reducer(1, function_addr("mac_diff_add"), 1) != 0:
        return 7
    if register_listener_callback(1, function_addr("mac_diff_listener")) != 0 or register_effect_callback(1, function_addr("mac_diff_effect")) != 0:
        return 8

    root = kit_create(-1)
    toolbar_root = kit_create(root)
    left_root = kit_create(root)
    right_root = kit_create(root)
    overview_root = kit_create(root)
    status_root = kit_create(root)
    if status_root < 0:
        return 9
    kit_rect(root, 0, 0, 900, 600, 0xFFFFFFFF)
    kit_rect(toolbar_root, 0, 0, 900, 48, 0xFFE8E8E8)
    kit_rect(left_root, 8, 52, 420, 514, 0xFFFFFFFF)
    kit_rect(right_root, 436, 52, 420, 514, 0xFFFFFFFF)
    kit_rect(overview_root, 882, 52, 8, 514, 0xFFFFFFFF)
    kit_rect(status_root, 0, 566, 900, 34, 0xFFD8D8D8)
    toolbar = _mount_component(-1, toolbar_root, 1)
    left = _mount_component(-1, left_root, 2)
    right = _mount_component(-1, right_root, 3)
    overview = _mount_component(-1, overview_root, 4)
    status = _mount_component(-1, status_root, 5)
    if toolbar < 0 or left < 0 or right < 0 or overview < 0 or status < 0:
        return 10
    _setg("mac_diff_root", root)
    _setg("mac_diff_toolbar", toolbar)
    _setg("mac_diff_left", left)
    _setg("mac_diff_right", right)
    _setg("mac_diff_overview", overview)
    _setg("mac_diff_status", status)

    managed = stack_alloc(48)
    if managed_state_set(toolbar, 1, 1, _g("mac_diff_n"), 0) != 0:
        return 11
    if managed_binding_add(toolbar, 1, status, 1) != 0:
        return 11
    if managed_state_set(toolbar, 1, 1, _g("mac_diff_n") + 1, 0) != 0:
        return 11
    if managed_state_get(status, 1, managed) != 0:
        return 11
    if load_i32(managed, 0) != 1 or load_i64(managed, 24) != _g("mac_diff_n") + 1:
        return 11

    descriptors = calloc(64, 72)
    effects = calloc(128, 48)
    effect_count = calloc(1, 4)
    error = calloc(1, 24)
    if ptr_is_null(descriptors) or ptr_is_null(effects) or ptr_is_null(effect_count) or ptr_is_null(error):
        return 11
    _setg("mac_diff_descriptors", ptr_to_int(descriptors))
    _setg("mac_diff_effects", ptr_to_int(effects))
    _setg("mac_diff_effect_count", ptr_to_int(effect_count))
    _setg("mac_diff_error", ptr_to_int(error))
    if register_effect(toolbar, 1, 0, 1, 7) != 0:
        return 12

    if enqueue_reduce(toolbar, 3, 0, 1, 2, null()) != 0 or enqueue_reduce(toolbar, 3, 0, 1, 3, null()) != 0:
        return 13
    if _run_component(toolbar, 1) != 1:
        return 14
    if enqueue_reduce(toolbar, 0, 0, 1, 1, null()) != 0 or restart_count(toolbar) != 1:
        return 15
    if _run_component(toolbar, -1) != 0 or _run_component(toolbar, 128) != 0:
        return 16
    if state_value(toolbar, 0) != 6 or binding_count(toolbar) != 9:
        return 17
    components = stack_alloc(32)
    store_i64(components, 0, left)
    store_i64(components, 8, right)
    store_i64(components, 16, overview)
    store_i64(components, 24, status)
    i = 0
    while i < 4:
        if enqueue_reduce(load_i64(components, i * 8), 0, 0, 1, 1, null()) != 0 or _run_component(load_i64(components, i * 8), -1) != 0:
            return 18
        i = i + 1

    theme = calloc(64, 8)
    if ptr_is_null(theme) or theme_init(theme) != 0:
        return 19
    if theme_set(theme, 0, 0, 0xFF000000) != 0 or theme_set(theme, 0, 1, 0xFF112233) != 0 or theme_set(theme, 3, 8, 12) != 0:
        return 20
    if theme_activate(theme) != 0:
        return 21
    if register_named_utility(10, 1, 0, 1, 0) != 0 or register_named_utility(12, 8, 3, 8, 1) != 0:
        return 22
    classes = cstr("bg-accent/50 -x-3/[dense]")
    operations = stack_alloc(80)
    if compile_style(classes, _raw_len(classes)) != 2 or cached_operations(classes, _raw_len(classes), operations, 2) != 2:
        return 23
    toolbar_node = node_for_key(toolbar, 10)
    if toolbar_node < 0 or apply_class(toolbar, toolbar_node, classes, _raw_len(classes)) != 0:
        return 24
    if theme_set(theme, 0, 0, 0xFF445566) != 0 or style_dirty(toolbar) != 0:
        return 25
    if theme_set(theme, 0, 1, 0xFF778899) != 0 or style_dirty(toolbar) != 1:
        return 26
    if apply_class(toolbar, toolbar_node, classes, _raw_len(classes)) != 0 or style_did_commit(toolbar) != 0:
        return 27

    if listen(101, toolbar, 1, 1, 0, toolbar_node) != 0:
        return 28
    path = stack_alloc(32)
    if dispatch(root, 500, 20, 1, null(), path, 4, error) != 1:
        return 29
    if _g("mac_diff_listener_calls") != 1 or _run_component(toolbar, -1) != 0 or state_value(toolbar, 0) != 7:
        return 30

    if command_register(1, function_addr("mac_diff_command_result"), 2, 1, toolbar, 0) != 0:
        return 31
    if command_register(2, function_addr("mac_diff_command_error"), 0, 1, toolbar, 0) != 0:
        return 32
    if _invoke_command(1, toolbar, 1, 101, 41, 0) != 0 or _invoke_command(2, toolbar, 2, 102, 0, 1) != 0:
        return 33

    if hardware != 0:
        if gui.init(cstr("pcc declarative diff"), 900, 600, cstr("./libpcc_gui_metal.dylib")) != 0:
            return 34
        rects = calloc(192, 32)
        colors = calloc(192, 4)
        texts = calloc(192, 48)
        rect_count = stack_alloc(8)
        text_count = stack_alloc(8)
        store_i64(rect_count, 0, 0)
        store_i64(text_count, 0, 0)
        kit_render(root, rects, colors, rect_count, texts, text_count)
        gui.render_scene(rects, colors, load_i64(rect_count, 0), texts, load_i64(text_count, 0), 900, 600)
        print("PCC_GUI_BRIDGE_ACK render_present=", gui.render_ack())

    if app_init(16, function_addr("mac_diff_app_event"), 1, root, function_addr("mac_diff_root_release"), function_addr("mac_diff_work_drain")) != 0:
        return 35
    if app_startup() != 0 or app_drain(2, error) != 2:
        return 36
    if app_post(3, 1, null(), 0, 0, 0) != 0 or app_drain(1, error) != 1:
        return 37
    if app_post(7, 1, null(), 0, 1, 0) != 0 or app_drain(1, error) != 1 or app_state() != 4:
        return 38
    if app_post(7, 1, null(), 0, 1, 0) != 0 or app_drain(1, error) != 1:
        return 39
    if app_state() != 7 or terminal_count() != 1 or _g("mac_diff_exit_ok") != 1:
        return 40
    if _g("mac_diff_work_drains") != 1 or _g("mac_diff_effect_calls") == 0:
        return 41
    if hardware != 0:
        gui.close()
    print("PCC_MAC_DIFF_DECLARATIVE_OK components=5 gc-mode=self-no-libpython")
    return 0
