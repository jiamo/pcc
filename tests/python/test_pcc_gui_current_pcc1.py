"""Current-pcc1 acceptance for the canonical freestanding GUI kernel."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]


def test_kernel_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the canonical GUI kernel gate")
    shutil.copy(
        REPO / "projects" / "mac_diff_app" / "pcc_gui_kit.py",
        tmp_path / "pcc_gui_kit.py",
    )
    source = tmp_path / "kernel_pcc1.py"
    source.write_text(
        '''from pcc.unsafe import load_i64, stack_alloc
import pcc_gui_kit as kit

def main() -> int:
    if kit.pcc_kit_init(3) != 0:
        return 1
    root = kit.pcc_kit_create(-1)
    first = kit.pcc_kit_create(root)
    second = kit.pcc_kit_create(root)
    kit.pcc_kit_rect(root, 0, 0, 100, 100, 0xFF000000)
    kit.pcc_kit_rect(first, 0, 0, 80, 80, 0xFF111111)
    kit.pcc_kit_rect(second, 0, 0, 80, 80, 0xFF222222)
    if kit.pcc_kit_hit(root, 10, 10) != second:
        return 2
    path = stack_alloc(24)
    if kit.pcc_kit_route_event_v2(root, 10, 10, 1, path, 3) != 2:
        return 3
    if load_i64(path, 0) != second or load_i64(path, 8) != root:
        return 4
    if kit.pcc_kit_destroy_subtree(first) != 1:
        return 5
    replacement = kit.pcc_kit_create(root)
    if replacement == first or kit.pcc_kit_is_valid(first) != 0:
        return 6
    print("PCC1_CANONICAL_GUI_KIT_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "kernel_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_CANONICAL_GUI_KIT_OK" in ran.stdout


def test_keyed_render_commit_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the keyed component commit gate")
    source = tmp_path / "component_commit_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import function_addr, load_i32, load_i64, load_ptr, ptr_add, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_first = extern("pcc_kit_first_child", (c_int64,), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
commit = extern("pcc_gui_component_render_commit", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
node_for_key = extern("pcc_gui_component_node_for_key", (c_int64,c_int64), c_int64)
owner_for_node = extern("pcc_gui_component_owner_for_node", (c_int64,), c_int64)

@c_abi_typed_export("pcc1_component_render", "i32", ("ptr",))
def pcc1_component_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    count_out = load_ptr(context, 64)
    if load_i32(context, 56) < 2:
        return -101
    first = arena
    store_i64(first, 0, component)
    store_i64(first, 8, 41)
    store_i32(first, 16, 1)
    store_i32(first, 20, 0xFF112233)
    store_i64(first, 24, 1)
    store_i64(first, 32, 1)
    store_i64(first, 40, 2)
    store_i64(first, 48, 30)
    store_i64(first, 56, 40)
    store_i64(first, 64, 0)
    second = ptr_add(arena, 72)
    store_i64(second, 0, component)
    store_i64(second, 8, 42)
    store_i32(second, 16, 1)
    store_i32(second, 20, 0xFF445566)
    store_i64(second, 24, 1)
    store_i64(second, 32, 5)
    store_i64(second, 40, 6)
    store_i64(second, 48, 7)
    store_i64(second, 56, 8)
    store_i64(second, 64, 0)
    store_i32(count_out, 0, 2)
    return 2

def main() -> int:
    if kit_init(4) != 0 or components_init(2, 2, 4) != 0:
        return 1
    if register(1, function_addr("pcc1_component_render")) != 0:
        return 2
    root = kit_create(-1)
    component = mount(-1, root, 1, stack_alloc(24), 0, stack_alloc(24), 0)
    descriptors = stack_alloc(144)
    effects = stack_alloc(192)
    count = stack_alloc(4)
    error = stack_alloc(24)
    if commit(component, descriptors, 2, effects, 4, count, error) != 0:
        return 3
    first = node_for_key(component, 41)
    if first < 0 or kit_first(root) != first or owner_for_node(first) != component:
        return 4
    if load_i32(count, 0) != 2:
        return 5
    print("PCC1_GUI_KEYED_COMMIT_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "component_commit_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_KEYED_COMMIT_OK" in ran.stdout


def test_state_lane_scheduler_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI scheduler gate")
    source = tmp_path / "state_lane_scheduler_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import function_addr, int_to_ptr, load_i64, load_ptr, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
state_value = extern("pcc_gui_component_state_slot_value", (c_int64,c_int32), c_int64)
scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
register_reducer = extern("pcc_gui_scheduler_register_reducer", (c_int32,c_ptr,c_int32), c_int32)
enqueue_set = extern("pcc_gui_scheduler_enqueue_set", (c_int64,c_int32,c_int32,c_int64,c_ptr), c_int32)
enqueue_reduce = extern("pcc_gui_scheduler_enqueue_reduce", (c_int64,c_int32,c_int32,c_int32,c_int64,c_ptr), c_int32)
pending = extern("pcc_gui_scheduler_pending", (c_int64,), c_int32)
run_sync = extern("pcc_gui_scheduler_run_sync", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
run_budgeted = extern("pcc_gui_scheduler_run_budgeted", (c_int64,c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)

@c_abi_typed_export("pcc1_scheduler_render", "i32", ("ptr",))
def pcc1_scheduler_render(context) -> int:
    store_i32(load_ptr(context, 64), 0, 0)
    return 0

@c_abi_typed_export("pcc1_scheduler_add", "i32", ("i64", "i64", "ptr"))
def pcc1_scheduler_add(old: int, operand: int, result_out) -> int:
    store_i64(result_out, 0, old + operand)
    return 0

def main() -> int:
    if kit_init(2) != 0 or components_init(1, 1, 1) != 0:
        return 1
    if register_render(1, function_addr("pcc1_scheduler_render")) != 0:
        return 2
    root = kit_create(-1)
    state = stack_alloc(24)
    store_i32(state, 0, 1)
    store_i64(state, 8, 0)
    store_i64(state, 16, 0)
    component = mount(-1, root, 1, int_to_ptr(0), 0, state, 1)
    if component < 0 or scheduler_init(1, 8, 1) != 0:
        return 3
    if register_reducer(1, function_addr("pcc1_scheduler_add"), 1) != 0:
        return 4
    null = int_to_ptr(0)
    descriptors = stack_alloc(72)
    effects = stack_alloc(48)
    count = stack_alloc(4)
    error = stack_alloc(24)
    if enqueue_set(component, 3, 0, 5, null) != 0:
        return 5
    if enqueue_reduce(component, 0, 0, 1, 1, null) != 0:
        return 6
    if run_sync(component, descriptors, 1, effects, 1, count, error) != 0:
        return 7
    if state_value(component, 0) != 1 or pending(component) != 2:
        return 8
    if run_budgeted(component, 8, descriptors, 1, effects, 1, count, error) != 0:
        return 9
    if state_value(component, 0) != 6 or pending(component) != 0:
        return 10
    print("PCC1_GUI_STATE_LANES_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "state_lane_scheduler_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_STATE_LANES_OK" in ran.stdout


def test_event_lifecycle_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI event lifecycle gate")
    source = tmp_path / "event_lifecycle_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import define_global_i64, function_addr, global_addr, int_to_ptr, load_i32, load_i64, load_ptr, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
commit = extern("pcc_gui_component_render_commit", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
state_value = extern("pcc_gui_component_state_slot_value", (c_int64,c_int32), c_int64)
unmount = extern("pcc_gui_component_unmount", (c_int64,), c_int32)
scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
enqueue_set = extern("pcc_gui_scheduler_enqueue_set", (c_int64,c_int32,c_int32,c_int64,c_ptr), c_int32)
run_sync = extern("pcc_gui_scheduler_run_sync", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
events_init = extern("pcc_gui_events_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
register_listener_callback = extern("pcc_gui_events_register_listener_callback", (c_int32,c_ptr), c_int32)
register_effect_callback = extern("pcc_gui_events_register_effect_callback", (c_int32,c_ptr), c_int32)
listen = extern("pcc_gui_events_listen", (c_int64,c_int64,c_int32,c_int32,c_int32,c_int64), c_int32)
dispatch = extern("pcc_gui_events_dispatch", (c_int64,c_int64,c_int64,c_int32,c_ptr,c_ptr,c_int32,c_ptr), c_int32)
register_effect = extern("pcc_gui_events_register_effect", (c_int64,c_int32,c_int64,c_int32,c_int64), c_int32)
drain = extern("pcc_gui_events_drain_passive", (c_int32,c_ptr), c_int32)

define_global_i64("pcc1_event_component", -1)
define_global_i64("pcc1_event_callbacks", 0)

@c_abi_typed_export("pcc1_event_render", "i32", ("ptr",))
def pcc1_event_render(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    if load_i32(context, 56) < 1:
        return -101
    store_i64(arena, 0, component)
    store_i64(arena, 8, 5)
    store_i32(arena, 16, 1)
    store_i32(arena, 20, 0xFF112233)
    store_i64(arena, 24, 1)
    store_i64(arena, 32, 0)
    store_i64(arena, 40, 0)
    store_i64(arena, 48, 40)
    store_i64(arena, 56, 40)
    store_i64(arena, 64, 11)
    store_i32(load_ptr(context, 64), 0, 1)
    return 1

@c_abi_typed_export("pcc1_event_listener", "i32", ("i64", "i64", "ptr"))
def pcc1_event_listener(listener_id: int, target: int, event) -> int:
    if listener_id != 11 or target != load_i64(global_addr("pcc1_event_component"), 0):
        return -1
    return enqueue_set(target, 0, 0, 7, int_to_ptr(0))

@c_abi_typed_export("pcc1_event_effect", "i32", ("i64", "i32", "i64"))
def pcc1_event_effect(component: int, phase: int, payload: int) -> int:
    store_i64(global_addr("pcc1_event_callbacks"), 0, load_i64(global_addr("pcc1_event_callbacks"), 0) + 1)
    return 0

def main() -> int:
    if kit_init(3) != 0 or components_init(1, 1, 2) != 0:
        return 1
    if scheduler_init(1, 4, 1) != 0 or events_init(2, 2, 2, 8) != 0:
        return 2
    if register_render(1, function_addr("pcc1_event_render")) != 0:
        return 3
    if register_listener_callback(2, function_addr("pcc1_event_listener")) != 0 or register_effect_callback(3, function_addr("pcc1_event_effect")) != 0:
        return 4
    root = kit_create(-1)
    state = stack_alloc(24)
    store_i32(state, 0, 1)
    store_i64(state, 8, 0)
    store_i64(state, 16, 0)
    component = mount(-1, root, 1, int_to_ptr(0), 0, state, 1)
    store_i64(global_addr("pcc1_event_component"), 0, component)
    if component < 0 or listen(11, component, 1, 2, 0, 0) != 0:
        return 5
    descriptors = stack_alloc(72)
    effects = stack_alloc(4 * 48)
    count = stack_alloc(4)
    error = stack_alloc(24)
    if commit(component, descriptors, 1, effects, 4, count, error) != 0:
        return 6
    if register_effect(component, 9, 5, 3, 0) != 0 or drain(8, error) != 1:
        return 7
    if dispatch(root, 10, 10, 1, stack_alloc(8), stack_alloc(16), 2, error) != 1:
        return 8
    if run_sync(component, descriptors, 1, effects, 4, count, error) != 0:
        return 9
    if state_value(component, 0) != 7 or drain(8, error) != 2:
        return 10
    if load_i64(global_addr("pcc1_event_callbacks"), 0) != 7:
        return 11
    if unmount(component) != 0 or drain(8, error) != 1:
        return 12
    print("PCC1_GUI_EVENT_LIFECYCLE_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "event_lifecycle_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_EVENT_LIFECYCLE_OK" in ran.stdout


def test_style_token_utilities_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI style token gate")
    source = tmp_path / "style_tokens_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import calloc, function_addr, ptr_is_null, stack_alloc

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
style_get = extern("pcc_kit_style_get", (c_int64,c_int32), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
theme_set = extern("pcc_gui_theme_set_token", (c_ptr,c_int32,c_int32,c_int64), c_int32)
theme_activate = extern("pcc_gui_theme_activate", (c_ptr,), c_int32)
style_init = extern("pcc_gui_style_init", (c_int64,c_int64), c_int32)
register_utility = extern("pcc_gui_style_register_utility", (c_int32,c_int32,c_int32,c_int32), c_int32)
generate = extern("pcc_gui_style_generate", (c_int32,c_int32,c_int32,c_ptr), c_int32)
apply = extern("pcc_gui_style_apply", (c_int64,c_int64,c_ptr), c_int32)
dirty = extern("pcc_gui_style_component_dirty", (c_int64,), c_int32)
did_commit = extern("pcc_gui_style_component_did_commit", (c_int64,), c_int32)

@c_abi_typed_export("pcc1_style_render", "i32", ("ptr",))
def pcc1_style_render(context) -> int:
    return 0

def main() -> int:
    if kit_init(2) != 0 or components_init(1, 1, 1) != 0:
        return 1
    if style_init(2, 2) != 0 or register_render(1, function_addr("pcc1_style_render")) != 0:
        return 2
    theme = calloc(64, 8)
    if ptr_is_null(theme) or theme_init(theme) != 0:
        return 3
    if theme_set(theme, 0, 0, 0xFF123456) != 0 or theme_activate(theme) != 0:
        return 4
    if register_utility(1, 0, 1, 0) != 0:
        return 5
    root = kit_create(-1)
    component = mount(-1, root, 1, theme, 0, theme, 0)
    operation = stack_alloc(40)
    if component < 0 or generate(1, 0, 0, operation) != 0:
        return 6
    if apply(component, root, operation) != 0 or style_get(root, 1) != 0xFF123456:
        return 7
    if theme_set(theme, 0, 1, 55) != 0 or dirty(component) != 0:
        return 8
    if theme_set(theme, 0, 0, 0xFFABCDEF) != 0 or dirty(component) != 1:
        return 9
    if apply(component, root, operation) != -106:
        return 10
    if generate(1, 0, 0, operation) != 0 or apply(component, root, operation) != 0:
        return 11
    if did_commit(component) != 0 or dirty(component) != 0:
        return 12
    print("PCC1_GUI_STYLE_TOKENS_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "style_tokens_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_STYLE_TOKENS_OK" in ran.stdout


def test_style_candidate_compiler_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI style compiler gate")
    source = tmp_path / "style_compiler_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import calloc, cstr, function_addr, load_i8, ptr_is_null

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
style_get = extern("pcc_kit_style_get", (c_int64,c_int32), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
theme_set = extern("pcc_gui_theme_set_token", (c_ptr,c_int32,c_int32,c_int64), c_int32)
theme_activate = extern("pcc_gui_theme_activate", (c_ptr,), c_int32)
style_init = extern("pcc_gui_style_init", (c_int64,c_int64), c_int32)
compiler_init = extern("pcc_gui_style_compiler_init", (c_int64,), c_int32)
register_named = extern("pcc_gui_style_register_named_utility", (c_int32,c_int32,c_int32,c_int32,c_int32), c_int32)
apply_class = extern("pcc_gui_style_apply_class", (c_int64,c_int64,c_ptr,c_int64), c_int32)
parser_calls = extern("pcc_gui_style_parser_invocations", (), c_int64)
allocations = extern("pcc_gui_style_cache_allocation_count", (), c_int64)

@c_abi_typed_export("pcc1_style_compiler_render", "i32", ("ptr",))
def pcc1_style_compiler_render(context) -> int:
    return 0

def raw_len(text) -> int:
    n = 0
    while load_i8(text, n) != 0:
        n = n + 1
    return n

def main() -> int:
    if kit_init(2) != 0 or components_init(1, 1, 2) != 0:
        return 1
    if style_init(4, 4) != 0 or compiler_init(2) != 0:
        return 2
    if register_named(1, 1, 0, 1, 0) != 0 or register_named(2, 7, 3, 7, 1) != 0:
        return 3
    if register_render(1, function_addr("pcc1_style_compiler_render")) != 0:
        return 4
    theme = calloc(64, 8)
    if ptr_is_null(theme) or theme_init(theme) != 0:
        return 5
    if theme_set(theme, 0, 0, 0xFF102030) != 0 or theme_set(theme, 3, 2, 6) != 0 or theme_activate(theme) != 0:
        return 6
    root = kit_create(-1)
    component = mount(-1, root, 1, theme, 0, theme, 0)
    classes = cstr("bg-accent gap-2")
    length = raw_len(classes)
    if component < 0 or apply_class(component, root, classes, length) != 0:
        return 7
    if style_get(root, 1) != 0xFF102030 or style_get(root, 7) != 6:
        return 8
    if apply_class(component, root, classes, length) != 0:
        return 9
    if parser_calls() != 1 or allocations() != 5:
        return 10
    if theme_set(theme, 0, 1, 99) != 0 or apply_class(component, root, classes, length) != 0:
        return 11
    if parser_calls() != 1:
        return 12
    if theme_set(theme, 3, 2, 7) != 0 or apply_class(component, root, classes, length) != 0:
        return 13
    if parser_calls() != 2 or style_get(root, 7) != 7:
        return 14
    print("PCC1_GUI_STYLE_COMPILER_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "style_compiler_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_STYLE_COMPILER_OK" in ran.stdout


def test_command_state_boundary_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI command boundary gate")
    source = tmp_path / "command_state_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import function_addr, load_i32, load_i64, load_ptr, null, ptr_is_null, stack_alloc, store_i32, store_i64, store_ptr

commands_init = extern("pcc_gui_commands_init", (c_int64,c_int64,c_int64,c_int64), c_int32)
state_set = extern("pcc_gui_managed_state_set", (c_int64,c_int64,c_int32,c_int64,c_int64), c_int32)
state_get = extern("pcc_gui_managed_state_get", (c_int64,c_int64,c_ptr), c_int32)
binding_add = extern("pcc_gui_managed_binding_add", (c_int64,c_int64,c_int64,c_int64), c_int32)
register = extern("pcc_gui_commands_register", (c_int32,c_ptr,c_int32,c_int32,c_int64,c_int64), c_int32)
invoke_command = extern("pcc_gui_commands_invoke", (c_ptr,), c_int32)
resolve_result = extern("pcc_gui_commands_resolve_result", (c_int64,c_ptr,c_int64), c_int32)
completion = extern("pcc_gui_commands_completion", (c_int64,c_ptr), c_int32)
release = extern("pcc_gui_commands_release_completion", (c_int64,), c_int32)

@c_abi_typed_export("pcc1_command", "i32", ("ptr", "i64"))
def pcc1_command(packet, resolver: int) -> int:
    payload = load_ptr(packet, 24)
    result = stack_alloc(8)
    if ptr_is_null(payload) or load_i64(packet, 32) != 8:
        return -1
    store_i64(result, 0, load_i64(payload, 0) + load_i64(packet, 40))
    return resolve_result(resolver, result, 8)

def main() -> int:
    if commands_init(4, 2, 2, 2) != 0:
        return 1
    state = stack_alloc(48)
    if state_set(10, 1, 1, 7, 0) != 0 or binding_add(10, 1, 20, 2) != 0:
        return 2
    if state_set(10, 1, 1, 9, 0) != 0 or state_get(20, 2, state) != 0:
        return 3
    if load_i64(state, 24) != 9:
        return 4
    if register(1, function_addr("pcc1_command"), 2, 1, 20, 3) != 0:
        return 5
    payload = stack_alloc(8)
    store_i64(payload, 0, 39)
    packet = stack_alloc(64)
    error = stack_alloc(24)
    store_i64(packet, 0, 100)
    store_i32(packet, 8, 1)
    store_i32(packet, 12, 0)
    store_i64(packet, 16, 20)
    store_ptr(packet, 24, payload)
    store_i64(packet, 32, 8)
    store_i64(packet, 40, 3)
    store_i64(packet, 48, 200)
    store_ptr(packet, 56, error)
    if invoke_command(packet) != 0:
        return 6
    out = stack_alloc(48)
    if completion(200, out) != 0 or load_i32(out, 8) != 1:
        return 7
    if load_i64(load_ptr(out, 16), 0) != 42 or release(200) != 0:
        return 8
    print("PCC1_GUI_COMMAND_STATE_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "command_state_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_COMMAND_STATE_OK" in ran.stdout


def test_app_run_lifecycle_strict_self_no_libpython(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the GUI app lifecycle gate")
    source = tmp_path / "app_lifecycle_pcc1.py"
    source.write_text(
        '''from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import cstr, define_global_i64, function_addr, global_addr, load_i32, load_i64, null, store_i64

app_init = extern("pcc_gui_app_lifecycle_init", (c_int64,c_ptr,c_int64,c_int64,c_ptr,c_ptr), c_int32)
app_startup = extern("pcc_gui_app_lifecycle_post_startup", (), c_int32)
app_post = extern("pcc_gui_app_lifecycle_post", (c_int32,c_int64,c_ptr,c_int64,c_int32,c_int32), c_int32)
app_drain = extern("pcc_gui_app_lifecycle_drain", (c_int32,c_ptr), c_int32)
app_state = extern("pcc_gui_app_lifecycle_state", (), c_int32)
app_pending = extern("pcc_gui_app_lifecycle_pending", (), c_int64)
terminal_count = extern("pcc_gui_app_lifecycle_terminal_count", (), c_int32)

define_global_i64("pcc1_app_trace", 0)
define_global_i64("pcc1_app_cancelled", 0)
define_global_i64("pcc1_app_drains", 0)
define_global_i64("pcc1_app_releases", 0)

@c_abi_typed_export("pcc1_app_callback", "i32", ("ptr",))
def pcc1_app_callback(event) -> int:
    kind = load_i32(event, 8)
    store_i64(global_addr("pcc1_app_trace"), 0, load_i64(global_addr("pcc1_app_trace"), 0) | (1 << kind))
    if kind == 7 and load_i64(global_addr("pcc1_app_cancelled"), 0) == 0:
        store_i64(global_addr("pcc1_app_cancelled"), 0, 1)
        return 1
    return 0

@c_abi_typed_export("pcc1_app_work_drain", "i32", ("ptr",))
def pcc1_app_work_drain(_ignored) -> int:
    store_i64(global_addr("pcc1_app_drains"), 0, load_i64(global_addr("pcc1_app_drains"), 0) + 1)
    return 0

@c_abi_typed_export("pcc1_app_window_release", "i64", ("i64",))
def pcc1_app_window_release(handle: int) -> int:
    if handle != 55:
        return -1
    store_i64(global_addr("pcc1_app_releases"), 0, load_i64(global_addr("pcc1_app_releases"), 0) + 1)
    return 0

def main() -> int:
    if app_init(8, function_addr("pcc1_app_callback"), 9, 55, function_addr("pcc1_app_window_release"), function_addr("pcc1_app_work_drain")) != 0:
        return 1
    if app_startup() != 0 or app_drain(2, null()) != 2:
        return 2
    if app_post(3, 9, null(), 0, 0, 0) != 0 or app_drain(1, null()) != 1:
        return 3
    if app_post(5, 9, cstr("/tmp"), 4, 0, 0) != 0 or app_post(6, 9, null(), 0, 0, 0) != 0:
        return 4
    if app_drain(2, null()) != 2:
        return 5
    if app_post(7, 9, null(), 0, 1, 7) != 0 or app_drain(1, null()) != 1:
        return 6
    if app_state() != 4 or terminal_count() != 0:
        return 7
    if app_post(7, 9, null(), 0, 1, 7) != 0 or app_drain(1, null()) != 1:
        return 8
    if app_state() != 7 or terminal_count() != 1 or app_pending() != 0:
        return 9
    if load_i64(global_addr("pcc1_app_drains"), 0) != 1 or load_i64(global_addr("pcc1_app_releases"), 0) != 1:
        return 10
    trace = load_i64(global_addr("pcc1_app_trace"), 0)
    if (trace & (1 << 1)) == 0 or (trace & (1 << 2)) == 0 or (trace & (1 << 3)) == 0 or (trace & (1 << 5)) == 0 or (trace & (1 << 6)) == 0 or (trace & (1 << 7)) == 0 or (trace & (1 << 8)) == 0:
        return 11
    if app_post(6, 9, null(), 0, 0, 0) != -108:
        return 12
    print("PCC1_GUI_APP_LIFECYCLE_OK")
    return 0

main()
''',
        encoding="utf-8",
    )
    exe = tmp_path / "app_lifecycle_pcc1"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(exe),
        ],
        cwd=tmp_path,
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
    assert "PCC1_GUI_APP_LIFECYCLE_OK" in ran.stdout
