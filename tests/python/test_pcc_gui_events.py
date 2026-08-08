"""Behavioral contract for target-filtered GUI events and lifecycle effects."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
EVENTS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_events.py"
COMPONENTS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_components.py"


def _compile_run(
    tmp_path: Path, pcc_py_runtime_archive: Path, name: str, source: str
) -> str:
    src = tmp_path / f"{name}.py"
    exe = tmp_path / name
    src.write_text(source, encoding="utf-8")
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(exe)], env=env, text=True, capture_output=True, timeout=30
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return ran.stdout


def test_event_owner_freezes_callback_abis_and_component_hooks() -> None:
    source = EVENTS.read_text(encoding="utf-8")
    components = COMPONENTS.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    unsafe = (REPO / "pcc" / "unsafe" / "__init__.py").read_text(
        encoding="utf-8"
    )
    lowering = (
        REPO / "pcc" / "py_frontend" / "codegen" / "unsafe_lowering.py"
    ).read_text(encoding="utf-8")
    modules = makefile.split("FREESTANDING_PY_MODULES =", 1)[1].splitlines()[0]
    assert modules.split().count("pcc_gui_events") == 1
    assert "LISTENER_SIZE = 40" in source
    assert "EFFECT_SIZE = 48" in source
    assert "PASSIVE_SIZE = 40" in source
    assert '"pcc_gui_events_dispatch"' in source
    assert '"pcc_gui_events_before_component_commit"' in source
    assert '"pcc_gui_events_after_component_commit"' in source
    assert '"pcc_gui_events_before_component_unmount"' in source
    assert "call_i32_i64_i32_i64" in unsafe
    assert 'if intrinsic == "call_i32_i64_i32_i64"' in lowering
    assert "ir.FunctionType(_I32, [_I64, _I32, _I64])" in lowering
    assert '"pcc_gui_component_binding_owner_for_node"' in components
    assert '"pcc_gui_component_clear_listener"' in components
    assert "_component_subtree_can_unmount(component_id)" in components
    assert "EFFECT_REMOVE,\n                        key," in components


_EVENT_LIFECYCLE_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import calloc, define_global_i64, function_addr, global_addr, int_to_ptr, load_i32, load_i64, load_ptr, ptr_add, ptr_is_null, ptr_to_int, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
kit_focus = extern("pcc_kit_focus", (c_int64,), c_void)
kit_focused = extern("pcc_kit_focused", (c_int64,), c_int32)
kit_hover = extern("pcc_kit_hover", (c_int64,c_int64,c_int64,c_int32), c_int64)
kit_hovered = extern("pcc_kit_hovered", (c_int64,), c_int32)

components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
commit = extern("pcc_gui_component_render_commit", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
node_for_key = extern("pcc_gui_component_node_for_key", (c_int64,c_int64), c_int64)
state_value = extern("pcc_gui_component_state_slot_value", (c_int64,c_int32), c_int64)
unmount = extern("pcc_gui_component_unmount", (c_int64,), c_int32)

scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
enqueue_set = extern("pcc_gui_scheduler_enqueue_set", (c_int64,c_int32,c_int32,c_int64,c_ptr), c_int32)
run_sync = extern("pcc_gui_scheduler_run_sync", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
pending = extern("pcc_gui_scheduler_pending", (c_int64,), c_int32)
render_count = extern("pcc_gui_scheduler_render_count", (c_int64,), c_int32)

events_init = extern("pcc_gui_events_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
register_listener_callback = extern("pcc_gui_events_register_listener_callback", (c_int32,c_ptr), c_int32)
register_effect_callback = extern("pcc_gui_events_register_effect_callback", (c_int32,c_ptr), c_int32)
listen = extern("pcc_gui_events_listen", (c_int64,c_int64,c_int32,c_int32,c_int32,c_int64), c_int32)
unlisten = extern("pcc_gui_events_unlisten", (c_int64,), c_int32)
dispatch = extern("pcc_gui_events_dispatch", (c_int64,c_int64,c_int64,c_int32,c_ptr,c_ptr,c_int32,c_ptr), c_int32)
register_effect = extern("pcc_gui_events_register_effect", (c_int64,c_int32,c_int64,c_int32,c_int64), c_int32)
drain_passive = extern("pcc_gui_events_drain_passive", (c_int32,c_ptr), c_int32)
listener_count = extern("pcc_gui_events_listener_count", (c_int64,), c_int32)

define_global_i64("event_parent", -1)
define_global_i64("event_child", -1)
define_global_i64("event_listener_trace", 0)
define_global_i64("event_trace_base", 0)
define_global_i64("event_trace_count", 0)

def descriptor(arena, index: int, component: int, key: int, color: int, x: int, y: int, w: int, h: int, listener: int) -> None:
    slot = ptr_add(arena, index * 72)
    store_i64(slot, 0, component)
    store_i64(slot, 8, key)
    store_i32(slot, 16, 1)
    store_i32(slot, 20, color)
    store_i64(slot, 24, 1)
    store_i64(slot, 32, x)
    store_i64(slot, 40, y)
    store_i64(slot, 48, w)
    store_i64(slot, 56, h)
    store_i64(slot, 64, listener)

@c_abi_typed_export("event_parent_render", "i32", ("ptr",))
def event_parent_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    if load_i32(context, 56) < 1:
        return -101
    descriptor(arena, 0, component, 100, 0xFF101010, 0, 0, 100, 100, 102)
    store_i32(load_ptr(context, 64), 0, 1)
    return 1

@c_abi_typed_export("event_child_render", "i32", ("ptr",))
def event_child_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    state = load_ptr(context, 32)
    mode = load_i64(state, 8)
    count = 1 if mode == 2 else 2
    if load_i32(context, 56) < count:
        return -101
    descriptor(arena, 0, component, 10, 0xFF202020, 0, 0, 80, 80, 0)
    if count == 2:
        descriptor(arena, 1, component, 20, 0xFF303030, 0, 0, 80, 80, 202)
    store_i32(load_ptr(context, 64), 0, count)
    return count

@c_abi_typed_export("event_listener_callback", "i32", ("i64", "i64", "ptr"))
def event_listener_callback(listener_id: int, target_component: int, event) -> int:
    child = load_i64(global_addr("event_child"), 0)
    if target_component != child:
        return -1
    trace = load_i64(global_addr("event_listener_trace"), 0)
    store_i64(global_addr("event_listener_trace"), 0, trace * 1000 + listener_id)
    if listener_id == 202:
        if enqueue_set(child, 0, 0, 1, int_to_ptr(0)) != 0:
            return -1
    return 0

@c_abi_typed_export("event_effect_callback", "i32", ("i64", "i32", "i64"))
def event_effect_callback(component_id: int, phase: int, payload: int) -> int:
    base = int_to_ptr(load_i64(global_addr("event_trace_base"), 0))
    count = load_i64(global_addr("event_trace_count"), 0)
    store_i64(base, count * 8, phase * 10 + payload)
    store_i64(global_addr("event_trace_count"), 0, count + 1)
    return 0

def trace_reset() -> None:
    store_i64(global_addr("event_trace_count"), 0, 0)

def trace_is(count: int, a: int, b: int, c: int, d: int, e: int, f: int) -> int:
    if load_i64(global_addr("event_trace_count"), 0) != count:
        return 0
    base = int_to_ptr(load_i64(global_addr("event_trace_base"), 0))
    values = stack_alloc(48)
    store_i64(values, 0, a)
    store_i64(values, 8, b)
    store_i64(values, 16, c)
    store_i64(values, 24, d)
    store_i64(values, 32, e)
    store_i64(values, 40, f)
    i = 0
    while i < count:
        if load_i64(base, i * 8) != load_i64(values, i * 8):
            return 0
        i = i + 1
    return 1

def run_component(component: int) -> int:
    descriptors = stack_alloc(4 * 72)
    effects = stack_alloc(8 * 48)
    effect_count = stack_alloc(4)
    error = stack_alloc(24)
    return run_sync(component, descriptors, 4, effects, 8, effect_count, error)

def main() -> int:
    trace = calloc(32, 8)
    if ptr_is_null(trace):
        return 1
    store_i64(global_addr("event_trace_base"), 0, ptr_to_int(trace))
    if kit_init(8) != 0 or components_init(4, 4, 8) != 0:
        return 2
    if scheduler_init(4, 16, 1) != 0:
        return 3
    if events_init(8, 8, 8, 32) != 0 or events_init(0, 1, 1, 1) != -101:
        return 4
    if register_render(1, function_addr("event_parent_render")) != 0 or register_render(2, function_addr("event_child_render")) != 0:
        return 5
    if register_listener_callback(7, function_addr("event_listener_callback")) != 0:
        return 6
    if register_effect_callback(8, function_addr("event_effect_callback")) != 0:
        return 7

    root = kit_create(-1)
    parent = mount(-1, root, 1, int_to_ptr(0), 0, int_to_ptr(0), 0)
    if parent < 0:
        return 8
    store_i64(global_addr("event_parent"), 0, parent)
    if listen(102, parent, 1, 7, 0, 11) != 0 or listen(102, parent, 1, 7, 0, 11) != -102:
        return 9
    descriptors = stack_alloc(4 * 72)
    effects = stack_alloc(8 * 48)
    effect_count = stack_alloc(4)
    error = stack_alloc(24)
    if commit(parent, descriptors, 4, effects, 8, effect_count, error) != 0:
        return 10
    child_root = node_for_key(parent, 100)
    state = stack_alloc(24)
    store_i32(state, 0, 1)
    store_i64(state, 8, 0)
    store_i64(state, 16, 0)
    child = mount(parent, child_root, 2, int_to_ptr(0), 0, state, 1)
    if child < 0:
        return 11
    store_i64(global_addr("event_child"), 0, child)
    if listen(202, child, 1, 7, 0, 22) != 0:
        return 12
    if commit(child, descriptors, 4, effects, 8, effect_count, error) != 0:
        return 13
    target = node_for_key(child, 20)
    if target < 0:
        return 14
    if register_effect(parent, 1, 0, 8, 1) != 0 or register_effect(child, 2, 20, 8, 2) != 0:
        return 15
    if drain_passive(8, error) != 2 or trace_is(4, 31, 32, 51, 52, 0, 0) == 0:
        return 16

    trace_reset()
    store_i64(global_addr("event_listener_trace"), 0, 0)
    event = stack_alloc(16)
    path = stack_alloc(32)
    if dispatch(root, 10, 10, 1, event, path, 4, error) != 2:
        return 17
    if load_i64(global_addr("event_listener_trace"), 0) != 202102:
        return 18
    if pending(child) != 1 or run_component(child) != 0:
        return 19
    if state_value(child, 0) != 1 or render_count(child) != 1 or render_count(parent) != 0:
        return 20
    if drain_passive(8, error) != 2 or trace_is(5, 2, 12, 32, 42, 52, 0) == 0:
        return 21

    kit_focus(target)
    if kit_hover(root, 10, 10, 1) != target:
        return 22
    trace_reset()
    if enqueue_set(child, 0, 0, 2, int_to_ptr(0)) != 0 or run_component(child) != 0:
        return 23
    if drain_passive(8, error) != 1 or trace_is(3, 2, 12, 42, 0, 0, 0) == 0:
        return 24
    if kit_valid(target) != 0 or kit_focused(target) != 0 or kit_hovered(target) != 0:
        return 25
    if listener_count(child) != 0 or unlisten(202) != -106:
        return 26

    trace_reset()
    if enqueue_set(child, 0, 0, 3, int_to_ptr(0)) != 0 or run_component(child) != 0:
        return 27
    target = node_for_key(child, 20)
    if target < 0 or listen(202, child, 1, 7, 0, 22) != 0:
        return 28
    if register_effect(child, 2, 20, 8, 2) != 0:
        return 29
    if drain_passive(8, error) != 1 or trace_is(2, 32, 52, 0, 0, 0, 0) == 0:
        return 30
    kit_focus(target)
    if kit_hover(root, 10, 10, 1) != target:
        return 31

    trace_reset()
    if unmount(parent) != 0:
        return 32
    if drain_passive(8, error) != 2 or trace_is(6, 2, 12, 1, 11, 42, 41) == 0:
        return 33
    if component_valid(parent) != 0 or component_valid(child) != 0:
        return 34
    if listener_count(parent) != 0 or listener_count(child) != 0 or pending(child) != 0:
        return 35
    if kit_valid(root) != 0 or kit_focused(target) != 0 or kit_hovered(target) != 0:
        return 36
    print("PCC_GUI_EVENT_LIFECYCLE_OK")
    return 0

main()
'''


def test_target_bubble_state_commit_and_lifecycle_order(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "PCC_GUI_EVENT_LIFECYCLE_OK" in _compile_run(
        tmp_path,
        pcc_py_runtime_archive,
        "gui_event_lifecycle",
        _EVENT_LIFECYCLE_PROGRAM,
    )
