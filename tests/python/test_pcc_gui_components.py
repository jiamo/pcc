"""Behavioral contract for bounded keyed component render commit."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
COMPONENTS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_components.py"
KERNEL = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_kit.py"


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


def test_components_have_one_production_owner_and_atomic_kernel_primitive() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")
    kernel = KERNEL.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    assert "pcc_gui_components" in makefile.split("FREESTANDING_PY_MODULES =", 1)[1]
    assert '@c_abi_typed_export("pcc_gui_component_render_commit"' in source
    assert 'function_addr("pcc_gui_component_node_removed")' in source
    assert "call_i32_ptr1(" in source
    assert "MAX_DESCRIPTORS = 1024" in source
    assert "MAX_EFFECTS = 2048" in source
    assert '@c_abi_typed_export("pcc_kit_replace_children"' in kernel
    validate_at = kernel.index("while i < count:", kernel.index("def pcc_kit_replace_children"))
    mutate_at = kernel.index("child = _n8(parent, 8)", validate_at)
    assert validate_at < mutate_at


_KEYED_COMMIT_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import cstr, define_global_i64, function_addr, global_addr, load_i32, load_i64, load_ptr, ptr_add, ptr_to_int, stack_alloc, store_i32, store_i64

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
kit_live = extern("pcc_kit_live_nodes", (), c_int64)
kit_first = extern("pcc_kit_first_child", (c_int64,), c_int64)
kit_next = extern("pcc_kit_next_sibling", (c_int64,), c_int64)
kit_destroy = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)

components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
valid_component = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
commit = extern("pcc_gui_component_render_commit", (c_int64,c_ptr,c_int32,c_ptr,c_int32,c_ptr,c_ptr), c_int32)
binding_count = extern("pcc_gui_component_binding_count", (c_int64,), c_int64)
node_for_key = extern("pcc_gui_component_node_for_key", (c_int64,c_int64), c_int64)
owner_for_node = extern("pcc_gui_component_owner_for_node", (c_int64,), c_int64)
unmount = extern("pcc_gui_component_unmount", (c_int64,), c_int32)

define_global_i64("component_render_mode", 0)

def descriptor(arena, index: int, component: int, key: int, kind: int, flags: int, mask: int, p0: int, p1: int, p2: int, p3: int) -> None:
    slot = ptr_add(arena, index * 72)
    store_i64(slot, 0, component)
    store_i64(slot, 8, key)
    store_i32(slot, 16, kind)
    store_i32(slot, 20, flags)
    store_i64(slot, 24, mask)
    store_i64(slot, 32, p0)
    store_i64(slot, 40, p1)
    store_i64(slot, 48, p2)
    store_i64(slot, 56, p3)
    store_i64(slot, 64, 0)

@c_abi_typed_export("keyed_render_callback", "i32", ("ptr",))
def keyed_render_callback(context) -> int:
    component = load_i64(context, 8)
    arena = load_ptr(context, 48)
    capacity = load_i32(context, 56)
    count_out = load_ptr(context, 64)
    mode = load_i64(global_addr("component_render_mode"), 0)
    if mode == 4:
        return -116
    if mode == 5:
        store_i32(count_out, 0, 2)
        return 2
    if mode == 9:
        store_i32(count_out, 0, 2)
        return 1
    if mode == 7:
        store_i32(count_out, 0, 0)
        return 0
    if mode == 8:
        if capacity < 5:
            return -101
        i = 0
        while i < 5:
            descriptor(arena, i, component, 100 + i, 1, 0xFF101010 + i, 1, i, i, 9, 9)
            i = i + 1
        store_i32(count_out, 0, 5)
        return 5
    if mode == 2:
        if capacity < 1:
            return -101
        descriptor(arena, 0, component, 20, 1, 0xFF445566, 1, 3, 4, 30, 12)
        store_i32(count_out, 0, 1)
        return 1
    if capacity < 2:
        return -101
    if mode == 3:
        descriptor(arena, 0, component, 20, 1, 0xFF000001, 1, 0, 0, 5, 5)
        descriptor(arena, 1, component, 20, 2, 0, 1, ptr_to_int(cstr("dup")), 3, 12, 0xFF000002)
        store_i32(count_out, 0, 2)
        return 2
    if mode == 6:
        descriptor(arena, 0, component + 1, 20, 1, 0, 1, 0, 0, 5, 5)
        store_i32(count_out, 0, 1)
        return 1
    if mode == 0:
        descriptor(arena, 0, component, 10, 1, 0xFF112233, 1, 0, 0, 10, 10)
        descriptor(arena, 1, component, 20, 2, 0, 1, ptr_to_int(cstr("alpha")), 5, 12, 0xFFABCDEF)
        store_i32(count_out, 0, 2)
        return 2
    if capacity < 3:
        return -101
    descriptor(arena, 0, component, 20, 2, 0, 1, ptr_to_int(cstr("alpha")), 5, 12, 0xFFABCDEF)
    descriptor(arena, 1, component, 10, 1, 0xFF112233, 1, 0, 0, 20, 10)
    descriptor(arena, 2, component, 30, 1, 0xFF778899, 1, 5, 6, 7, 8)
    store_i32(count_out, 0, 3)
    return 3

def effect_kind(effects, index: int) -> int:
    return load_i32(effects, index * 48 + 28)

def main() -> int:
    if kit_init(5) != 0 or components_init(4, 4, 8) != 0:
        return 1
    if register(7, function_addr("keyed_render_callback")) != 0:
        return 2
    if register(7, function_addr("keyed_render_callback")) != -102:
        return 3
    root = kit_create(-1)
    component = mount(-1, root, 7, stack_alloc(24), 0, stack_alloc(24), 0)
    if component < 0 or valid_component(component) != 1:
        return 4
    descriptors = stack_alloc(8 * 72)
    effects = stack_alloc(16 * 48)
    effect_count = stack_alloc(4)
    error = stack_alloc(24)

    if commit(component, descriptors, 8, effects, 16, effect_count, error) != 0:
        return 5
    if load_i32(effect_count, 0) != 2 or effect_kind(effects, 0) != 1 or effect_kind(effects, 1) != 1:
        return 6
    key10 = node_for_key(component, 10)
    key20 = node_for_key(component, 20)
    if key10 < 0 or key20 < 0 or binding_count(component) != 2:
        return 7
    if owner_for_node(root) != component or owner_for_node(key10) != component:
        return 8
    if kit_first(root) != key10 or kit_next(key10) != key20 or kit_live() != 3:
        return 9

    store_i64(global_addr("component_render_mode"), 0, 1)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != 0:
        return 10
    if load_i32(effect_count, 0) != 4:
        return 11
    if effect_kind(effects, 0) != 2 or effect_kind(effects, 1) != 2 or effect_kind(effects, 2) != 3 or effect_kind(effects, 3) != 1:
        return 12
    key30 = node_for_key(component, 30)
    if node_for_key(component, 10) != key10 or node_for_key(component, 20) != key20:
        return 13
    if kit_first(root) != key20 or kit_next(key20) != key10 or kit_next(key10) != key30:
        return 14

    store_i64(global_addr("component_render_mode"), 0, 2)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != 0:
        return 15
    replacement = node_for_key(component, 20)
    if load_i32(effect_count, 0) != 3 or effect_kind(effects, 0) != 4 or effect_kind(effects, 1) != 5 or effect_kind(effects, 2) != 5:
        return 16
    if replacement == key20 or kit_valid(key20) != 0 or kit_valid(key10) != 0 or kit_valid(key30) != 0:
        return 17
    if binding_count(component) != 1 or kit_live() != 2 or kit_first(root) != replacement:
        return 18

    store_i64(global_addr("component_render_mode"), 0, 3)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != -102:
        return 19
    if load_i32(error, 0) != -102 or node_for_key(component, 20) != replacement or kit_live() != 2:
        return 20

    store_i64(global_addr("component_render_mode"), 0, 5)
    if commit(component, descriptors, 1, effects, 16, effect_count, error) != -101:
        return 21
    if node_for_key(component, 20) != replacement or kit_live() != 2:
        return 22

    store_i64(global_addr("component_render_mode"), 0, 4)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != -116:
        return 23
    if node_for_key(component, 20) != replacement or kit_live() != 2:
        return 24

    store_i64(global_addr("component_render_mode"), 0, 9)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != -116:
        return 25
    if node_for_key(component, 20) != replacement or kit_live() != 2:
        return 26

    store_i64(global_addr("component_render_mode"), 0, 6)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != -105:
        return 27
    if node_for_key(component, 20) != replacement or kit_live() != 2:
        return 28

    store_i64(global_addr("component_render_mode"), 0, 1)
    if commit(component, descriptors, 8, effects, 0, effect_count, error) != -101:
        return 29
    if node_for_key(component, 20) != replacement or kit_live() != 2:
        return 30

    store_i64(global_addr("component_render_mode"), 0, 8)
    if commit(component, descriptors, 8, effects, 16, effect_count, error) != -101:
        return 31
    if node_for_key(component, 20) != replacement or kit_live() != 2 or binding_count(component) != 1:
        return 32

    if kit_destroy(replacement) != 1:
        return 33
    if owner_for_node(replacement) != -1 or binding_count(component) != 0:
        return 34
    if unmount(component) != 0 or valid_component(component) != 0 or kit_valid(root) != 0:
        return 35
    print("PCC_GUI_KEYED_COMMIT_OK")
    return 0

main()
'''


def test_descriptor_reuse_reorder_replace_and_failed_work_rolls_back(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "PCC_GUI_KEYED_COMMIT_OK" in _compile_run(
        tmp_path,
        pcc_py_runtime_archive,
        "gui_keyed_commit",
        _KEYED_COMMIT_PROGRAM,
    )
