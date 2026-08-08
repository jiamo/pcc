"""Behavioral contract for bounded queued GUI state scheduling."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCHEDULER = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_scheduler.py"


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


def test_scheduler_has_one_archive_owner_and_verified_reducer_abi() -> None:
    source = SCHEDULER.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    unsafe = (REPO / "pcc" / "unsafe" / "__init__.py").read_text(
        encoding="utf-8"
    )
    lowering = (
        REPO / "pcc" / "py_frontend" / "codegen" / "unsafe_lowering.py"
    ).read_text(encoding="utf-8")
    assert "pcc_gui_scheduler" in makefile.split("FREESTANDING_PY_MODULES =", 1)[1]
    assert "UPDATE_SIZE = 64" in source
    assert "load_i64(_update_at(i), 0) == 0" in source
    assert "WORK_EVALUATING = 4" in source
    assert "UPDATE_REPLAY_ALWAYS = 1" in source
    assert "UPDATE_REDUCER_EVALUATED = 2" in source
    assert "call_i32_i64_i64_ptr" in unsafe
    assert 'if intrinsic == "call_i32_i64_i64_ptr"' in lowering
    assert "ir.FunctionType(_I32, [_I64, _I64, _CSTR])" in lowering
    assert "SLOT_MANAGED" not in source


_SCHEDULER_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import define_global_i64, function_addr, global_addr, int_to_ptr, load_i32, load_i64, load_ptr, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
register_handles = extern("pcc_gui_component_register_handle_ops", (c_ptr,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
unmount = extern("pcc_gui_component_unmount", (c_int64,), c_int32)
state_value = extern("pcc_gui_component_state_slot_value", (c_int64,c_int32), c_int64)

scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
register_reducer = extern("pcc_gui_scheduler_register_reducer", (c_int32,c_ptr,c_int32), c_int32)
enqueue_set = extern("pcc_gui_scheduler_enqueue_set", (c_int64,c_int32,c_int32,c_int64,c_ptr), c_int32)
enqueue_reduce = extern("pcc_gui_scheduler_enqueue_reduce", (c_int64,c_int32,c_int32,c_int32,c_int64,c_ptr), c_int32)
pending = extern("pcc_gui_scheduler_pending", (c_int64,), c_int32)
cancel = extern("pcc_gui_scheduler_cancel", (c_int64,), c_int32)
run_sync = extern("pcc_gui_scheduler_run_sync", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
run_budgeted = extern("pcc_gui_scheduler_run_budgeted", (c_int64,c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
last_lane = extern("pcc_gui_scheduler_last_lane", (c_int64,), c_int32)
restart_count = extern("pcc_gui_scheduler_restart_count", (c_int64,), c_int32)
render_count = extern("pcc_gui_scheduler_render_count", (c_int64,), c_int32)

define_global_i64("scheduler_component", -1)
define_global_i64("scheduler_render_mode", 0)
define_global_i64("scheduler_render_calls", 0)
define_global_i64("scheduler_reducer_calls", 0)
define_global_i64("scheduler_handle_balance", 0)

@c_abi_typed_export("scheduler_handle_retain", "i64", ("i64",))
def scheduler_handle_retain(handle: int) -> int:
    store_i64(global_addr("scheduler_handle_balance"), 0, load_i64(global_addr("scheduler_handle_balance"), 0) + 1)
    return handle

@c_abi_typed_export("scheduler_handle_release", "i64", ("i64",))
def scheduler_handle_release(handle: int) -> int:
    store_i64(global_addr("scheduler_handle_balance"), 0, load_i64(global_addr("scheduler_handle_balance"), 0) - 1)
    return handle

@c_abi_typed_export("scheduler_add_reducer", "i32", ("i64", "i64", "ptr"))
def scheduler_add_reducer(old: int, operand: int, result_out) -> int:
    store_i64(global_addr("scheduler_reducer_calls"), 0, load_i64(global_addr("scheduler_reducer_calls"), 0) + 1)
    if operand == -999:
        return -1
    store_i64(result_out, 0, old + operand)
    return 0

@c_abi_typed_export("scheduler_reentrant_reducer", "i32", ("i64", "i64", "ptr"))
def scheduler_reentrant_reducer(old: int, operand: int, result_out) -> int:
    component = load_i64(global_addr("scheduler_component"), 0)
    status = enqueue_set(component, 0, 0, old + operand, int_to_ptr(0))
    if status != -103:
        return -1
    store_i64(result_out, 0, old)
    return -1

@c_abi_typed_export("scheduler_render", "i32", ("ptr",))
def scheduler_render(context) -> int:
    store_i64(global_addr("scheduler_render_calls"), 0, load_i64(global_addr("scheduler_render_calls"), 0) + 1)
    mode = load_i64(global_addr("scheduler_render_mode"), 0)
    if mode == 1:
        return -116
    if mode == 2:
        component = load_i64(context, 8)
        state = load_ptr(context, 32)
        if enqueue_set(component, 2, 0, load_i64(state, 8) + 20, int_to_ptr(0)) != 0:
            return -116
        store_i64(global_addr("scheduler_render_mode"), 0, 0)
    store_i32(load_ptr(context, 64), 0, 0)
    return 0

def main() -> int:
    if kit_init(4) != 0 or components_init(2, 2, 4) != 0:
        return 1
    if register_render(1, function_addr("scheduler_render")) != 0:
        return 2
    if register_handles(function_addr("scheduler_handle_retain"), function_addr("scheduler_handle_release")) != 0:
        return 3
    root = kit_create(-1)
    state = stack_alloc(48)
    store_i32(state, 0, 1)
    store_i64(state, 8, 0)
    store_i64(state, 16, 0)
    store_i32(state, 24, 2)
    store_i64(state, 32, 100)
    store_i64(state, 40, 0)
    component = mount(-1, root, 1, int_to_ptr(0), 0, state, 2)
    if component < 0 or load_i64(global_addr("scheduler_handle_balance"), 0) != 1:
        return 4
    store_i64(global_addr("scheduler_component"), 0, component)
    if scheduler_init(2, 64, 4) != 0:
        return 5
    if register_reducer(1, function_addr("scheduler_add_reducer"), 1) != 0:
        return 6
    if register_reducer(1, function_addr("scheduler_add_reducer"), 1) != -102:
        return 7
    if register_reducer(3, function_addr("scheduler_add_reducer"), 0) != -105:
        return 8
    if register_reducer(2, function_addr("scheduler_reentrant_reducer"), 1) != 0:
        return 9

    descriptors = stack_alloc(72)
    effects = stack_alloc(48)
    effect_count = stack_alloc(4)
    error = stack_alloc(24)
    null = int_to_ptr(0)

    # Scalar eager bailout does not allocate queue work or render.
    if enqueue_set(component, 0, 0, 0, null) != 0 or pending(component) != 0:
        return 10

    # A render-time update is appended after the frozen active tail.
    store_i64(global_addr("scheduler_render_mode"), 0, 2)
    if enqueue_set(component, 0, 0, 1, null) != 0:
        return 11
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 12
    if state_value(component, 0) != 1 or pending(component) != 1 or render_count(component) != 1:
        return 13
    if run_budgeted(component, 64, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 14
    if state_value(component, 0) != 21 or pending(component) != 0:
        return 15
    if enqueue_set(component, 0, 0, 0, null) != 0 or run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 16

    # Low SET then high reducer exposes high work first and converges to 6.
    if enqueue_set(component, 3, 0, 5, null) != 0:
        return 17
    if enqueue_reduce(component, 0, 0, 1, 1, null) != 0:
        return 18
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 19
    if state_value(component, 0) != 1 or pending(component) != 2:
        return 20
    if run_budgeted(component, 64, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 21
    if state_value(component, 0) != 6 or pending(component) != 0:
        return 22

    # Low reducer then high SET converges to the later SET in enqueue order.
    if enqueue_reduce(component, 3, 0, 1, 1, null) != 0 or enqueue_set(component, 0, 0, 5, null) != 0:
        return 23
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0 or state_value(component, 0) != 5 or pending(component) != 2:
        return 24
    if run_budgeted(component, 64, descriptors, 1, effects, 1, effect_count, error) != 0 or state_value(component, 0) != 5 or pending(component) != 0:
        return 25

    # Budgeted work retries from its base.  Higher-priority enqueue invalidates
    # the yielded lower pass; reducers run again but no partial state commits.
    calls_before = load_i64(global_addr("scheduler_reducer_calls"), 0)
    renders_before = render_count(component)
    if enqueue_reduce(component, 3, 0, 1, 2, null) != 0 or enqueue_reduce(component, 3, 0, 1, 3, null) != 0:
        return 26
    if run_budgeted(component, 1, descriptors, 1, effects, 1, effect_count, error) != 1:
        return 27
    if state_value(component, 0) != 5 or pending(component) != 2 or render_count(component) != renders_before:
        return 28
    if load_i64(global_addr("scheduler_reducer_calls"), 0) != calls_before + 1:
        return 29
    if enqueue_reduce(component, 0, 0, 1, 1, null) != 0 or restart_count(component) != 1:
        return 30
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0 or state_value(component, 0) != 6 or pending(component) != 3:
        return 31
    if run_budgeted(component, 64, descriptors, 1, effects, 1, effect_count, error) != 0 or state_value(component, 0) != 11 or pending(component) != 0:
        return 32
    if load_i64(global_addr("scheduler_reducer_calls"), 0) != calls_before + 5:
        return 61

    # Reducer errors and reentrant reducer side effects fail closed.  The
    # committed state and original update remain until explicit cancellation.
    if enqueue_reduce(component, 0, 0, 1, -999, null) != 0:
        return 33
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != -104:
        return 34
    if state_value(component, 0) != 11 or pending(component) != 1 or cancel(component) != 0:
        return 35
    if enqueue_reduce(component, 0, 0, 2, 1, null) != 0:
        return 36
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != -104:
        return 37
    if state_value(component, 0) != 11 or pending(component) != 1 or cancel(component) != 0:
        return 38

    # Render failure rolls state back and retains the original queue.
    store_i64(global_addr("scheduler_render_mode"), 0, 1)
    if enqueue_set(component, 0, 0, 12, null) != 0:
        return 39
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != -116:
        return 40
    if state_value(component, 0) != 11 or pending(component) != 1 or cancel(component) != 0:
        return 41
    store_i64(global_addr("scheduler_render_mode"), 0, 0)

    # Explicit aging eventually selects background despite a discrete stream.
    if enqueue_set(component, 3, 0, 50, null) != 0:
        return 42
    i = 0
    while i < 32:
        if enqueue_reduce(component, 0, 0, 1, 1, null) != 0:
            return 43
        if run_budgeted(component, 128, descriptors, 1, effects, 1, effect_count, error) != 0 or last_lane(component) != 0:
            return 44
        i = i + 1
    if run_budgeted(component, 128, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 45
    if last_lane(component) != 3 or state_value(component, 0) != 82 or pending(component) != 0:
        return 46

    # Handle queue/base snapshots own independent retains.  Same-value SET
    # preserves the live state reference; overflow never retains the rejected
    # value, and cancel releases each admitted queue reference exactly once.
    if enqueue_set(component, 0, 1, 100, null) != 0 or run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 47
    if load_i64(global_addr("scheduler_handle_balance"), 0) != 1:
        return 48
    if enqueue_set(component, 3, 1, 300, null) != 0 or enqueue_set(component, 0, 0, 83, null) != 0:
        return 49
    if run_sync(component, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 50
    if state_value(component, 1) != 100 or pending(component) != 2 or load_i64(global_addr("scheduler_handle_balance"), 0) != 3:
        return 51
    if run_budgeted(component, 64, descriptors, 1, effects, 1, effect_count, error) != 0:
        return 52
    if state_value(component, 0) != 83 or state_value(component, 1) != 300 or pending(component) != 0:
        return 53
    if load_i64(global_addr("scheduler_handle_balance"), 0) != 1:
        return 54

    if scheduler_init(2, 2, 4) != 0:
        return 55
    if enqueue_set(component, 3, 1, 200, null) != 0 or enqueue_set(component, 3, 1, 300, null) != 0:
        return 56
    if enqueue_set(component, 3, 1, 400, null) != -101:
        return 57
    if load_i64(global_addr("scheduler_handle_balance"), 0) != 3 or cancel(component) != 0:
        return 58
    if load_i64(global_addr("scheduler_handle_balance"), 0) != 1:
        return 59
    if unmount(component) != 0 or load_i64(global_addr("scheduler_handle_balance"), 0) != 0:
        return 60

    print("PCC_GUI_STATE_LANES_OK")
    return 0

main()
'''


def test_state_lane_replay_priority_yield_failure_and_handle_ownership(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "PCC_GUI_STATE_LANES_OK" in _compile_run(
        tmp_path,
        pcc_py_runtime_archive,
        "gui_state_lanes",
        _SCHEDULER_PROGRAM,
    )
