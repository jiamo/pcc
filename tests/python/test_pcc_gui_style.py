"""Namespaced GUI theme utilities and selective invalidation contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
STYLE = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_style.py"
THEME = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_theme_anim.py"
EVENTS = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_events.py"


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


def test_style_owner_freezes_namespaces_operations_and_unmount_hook() -> None:
    style = STYLE.read_text(encoding="utf-8")
    theme = THEME.read_text(encoding="utf-8")
    events = EVENTS.read_text(encoding="utf-8")
    components = (
        REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_components.py"
    ).read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )
    modules = makefile.split("FREESTANDING_PY_MODULES =", 1)[1].splitlines()[0]
    assert modules.split().count("pcc_gui_style") == 1
    assert "STYLE_OPERATION_SIZE = 40" in style
    assert "NAMESPACE_COLOUR = 0" in style
    assert "NAMESPACE_FONT = 1" in style
    assert "NAMESPACE_SIZE = 2" in style
    assert "NAMESPACE_SPACING = 3" in style
    assert 'define_global_i64("pcc_gui_theme_active", 0)' in theme
    assert '"pcc_gui_theme_token_generation"' in theme
    assert '"pcc_gui_theme_namespace_generation"' in theme
    assert '"pcc_gui_style_theme_token_changed"' in theme
    assert '"pcc_gui_style_component_unmounted"' in events
    assert "_style_component_unmounted(component_id)" in events
    retire_body = components.split("def _retire_component(", 1)[1].split(
        "def _component_is_descendant(", 1
    )[0]
    assert "_style_component_unmounted(component_id)" in retire_body


_STYLE_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import calloc, function_addr, load_i64, ptr_is_null, stack_alloc, store_i32

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
kit_rect = extern("pcc_kit_rect", (c_int64,c_int64,c_int64,c_int64,c_int64,c_int32), c_void)
kit_layout = extern("pcc_kit_layout_tree", (c_int64,c_int64,c_int64), c_void)
geometry = extern("pcc_kit_geometry_get", (c_int64,c_int64), c_int64)
style_get = extern("pcc_kit_style_get", (c_int64,c_int32), c_int64)

components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
unmount = extern("pcc_gui_component_unmount", (c_int64,), c_int32)
scheduler_init = extern("pcc_gui_scheduler_init", (c_int64,c_int64,c_int64), c_int32)
events_init = extern("pcc_gui_events_init", (c_int64,c_int64,c_int64,c_int64), c_int32)

theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
theme_set = extern("pcc_gui_theme_set_token", (c_ptr,c_int32,c_int32,c_int64), c_int32)
theme_activate = extern("pcc_gui_theme_activate", (c_ptr,), c_int32)
theme_bump_namespace = extern("pcc_gui_theme_bump_namespace", (c_int32,), c_int32)
theme_resolve_prefix = extern("pcc_gui_theme_resolve_prefix", (c_int32,), c_int32)

style_init = extern("pcc_gui_style_init", (c_int64,c_int64), c_int32)
register_utility = extern("pcc_gui_style_register_utility", (c_int32,c_int32,c_int32,c_int32), c_int32)
generate = extern("pcc_gui_style_generate", (c_int32,c_int32,c_int32,c_ptr), c_int32)
apply = extern("pcc_gui_style_apply", (c_int64,c_int64,c_ptr), c_int32)
style_bg = extern("pcc_gui_style_bg", (c_int64,c_int32), c_int32)
style_tx = extern("pcc_gui_style_tx", (c_int64,c_int32,c_int32), c_int32)
style_size = extern("pcc_gui_style_size", (c_int64,c_int32,c_int32), c_int32)
style_pad = extern("pcc_gui_style_pad", (c_int64,c_int32), c_int32)
style_gap = extern("pcc_gui_style_gap", (c_int64,c_int32), c_int32)
dirty = extern("pcc_gui_style_component_dirty", (c_int64,), c_int32)
next_dirty = extern("pcc_gui_style_next_dirty", (), c_int64)
did_commit = extern("pcc_gui_style_component_did_commit", (c_int64,), c_int32)

@c_abi_typed_export("style_empty_render", "i32", ("ptr",))
def style_empty_render(context) -> int:
    return 0

def theme_value(theme, namespace: int, token: int, value: int) -> int:
    return theme_set(theme, namespace, token, value)

def main() -> int:
    if kit_init(12) != 0 or components_init(4, 2, 8) != 0:
        return 1
    if scheduler_init(4, 8, 1) != 0 or events_init(4, 4, 4, 8) != 0:
        return 2
    if style_init(8, 16) != 0 or style_init(8, 16) != -103:
        return 3
    if theme_resolve_prefix(1) != 0 or theme_resolve_prefix(4) != 3 or theme_resolve_prefix(5) != -1:
        return 38
    if register_render(1, function_addr("style_empty_render")) != 0:
        return 4
    first = calloc(64, 8)
    second = calloc(64, 8)
    third = calloc(64, 8)
    if ptr_is_null(first) or ptr_is_null(second) or ptr_is_null(third):
        return 5
    if theme_init(first) != 0 or theme_init(second) != 0 or theme_init(third) != 0:
        return 6
    # colour 0/1, font 0, size 0, spacing padding/gap/x
    if theme_value(first, 0, 0, 101) != 0 or theme_value(first, 0, 1, 202) != 0:
        return 7
    if theme_value(first, 1, 0, 303) != 0 or theme_value(first, 2, 0, 40) != 0:
        return 8
    if theme_value(first, 3, 0, 7) != 0 or theme_value(first, 3, 1, 5) != 0 or theme_value(first, 3, 2, 9) != 0:
        return 9
    if theme_value(second, 0, 0, 101) != 0 or theme_value(second, 0, 1, 909) != 0:
        return 10
    if theme_value(second, 1, 0, 303) != 0 or theme_value(second, 2, 0, 40) != 0:
        return 11
    if theme_value(second, 3, 0, 7) != 0 or theme_value(second, 3, 1, 5) != 0 or theme_value(second, 3, 2, 9) != 0:
        return 12
    if theme_value(third, 0, 0, 111) != 0 or theme_value(third, 0, 1, 909) != 0:
        return 42
    if theme_value(third, 1, 0, 404) != 0 or theme_value(third, 2, 0, 50) != 0:
        return 43
    if theme_value(third, 3, 0, 8) != 0 or theme_value(third, 3, 1, 6) != 0 or theme_value(third, 3, 2, 10) != 0:
        return 44
    if theme_activate(first) != 0:
        return 13
    # utility id, namespace, kernel style field, negative policy
    if register_utility(1, 0, 1, 0) != 0 or register_utility(2, 3, 6, 0) != 0:
        return 14
    if register_utility(3, 3, 7, 0) != 0 or register_utility(4, 3, 8, 1) != 0:
        return 15
    if register_utility(5, 0, 7, 0) != -105 or register_utility(1, 0, 1, 0) != -102:
        return 16

    root_a = kit_create(-1)
    root_b = kit_create(-1)
    component_a = mount(-1, root_a, 1, first, 0, first, 0)
    component_b = mount(-1, root_b, 1, first, 0, first, 0)
    if component_a < 0 or component_b < 0:
        return 17
    child_a = kit_create(root_a)
    child_b = kit_create(root_a)
    kit_rect(child_a, 0, 0, 10, 4, 1)
    kit_rect(child_b, 0, 0, 10, 4, 1)

    operation_a = stack_alloc(40)
    operation_b = stack_alloc(40)
    if generate(1, 0, 0, operation_a) != 0 or apply(component_a, root_a, operation_a) != 0:
        return 18
    if generate(1, 1, 0, operation_b) != 0 or apply(component_b, root_b, operation_b) != 0:
        return 19
    if style_get(root_a, 1) != 101 or style_get(root_b, 1) != 202:
        return 20
    invalid_operation = stack_alloc(40)
    if generate(1, 0, 0, invalid_operation) != 0:
        return 55
    store_i32(invalid_operation, 4, 99)
    # Shape validation must reject the namespace before consulting the theme
    # table; an arbitrary operation record cannot cause an out-of-range read.
    if apply(component_a, root_a, invalid_operation) != -106:
        return 56
    if style_pad(root_a, 0) != 0 or style_gap(root_a, 1) != 0:
        return 21
    if style_tx(root_a, 0, 0) != 0:
        return 45
    kit_layout(root_a, 100, 100)
    if geometry(child_a, 32) != 7 or geometry(child_a, 40) != 7:
        return 22
    if geometry(child_b, 40) != 16:
        return 23
    negative_operation = stack_alloc(40)
    if generate(1, 0, 1, negative_operation) != -103:
        return 39
    if generate(4, 2, 1, negative_operation) != 0 or apply(component_a, root_a, negative_operation) != 0:
        return 40
    if style_get(root_a, 8) != -9:
        return 41
    if style_size(root_a, 0, 2) != 0:
        return 46

    # Unreferenced token changes remain clean.
    if theme_value(first, 0, 2, 33) != 0 or dirty(component_a) != 0 or dirty(component_b) != 0:
        return 24
    # Exact token edit dirties only its user, and the old immutable op is stale.
    if theme_value(first, 0, 0, 111) != 0 or dirty(component_a) != 1 or dirty(component_b) != 0:
        return 25
    if next_dirty() != component_a or apply(component_a, root_a, operation_a) != -106:
        return 26
    if generate(1, 0, 0, operation_a) != 0 or apply(component_a, root_a, operation_a) != 0 or did_commit(component_a) != 0:
        return 27
    if dirty(component_a) != 0:
        return 28

    # Namespace invalidation remains selective: font reaches A only; colour
    # reaches both colour dependants.
    if theme_bump_namespace(1) != 0 or dirty(component_a) != 1 or dirty(component_b) != 0:
        return 29
    if style_tx(root_a, 0, 0) != 0 or did_commit(component_a) != 0:
        return 54
    if theme_bump_namespace(0) != 0 or dirty(component_a) != 1 or dirty(component_b) != 1:
        return 30
    if generate(1, 0, 0, operation_a) != 0 or generate(1, 1, 0, operation_b) != 0:
        return 31
    if apply(component_a, root_a, operation_a) != 0 or apply(component_b, root_b, operation_b) != 0:
        return 32
    if did_commit(component_a) != 0 or did_commit(component_b) != 0:
        return 33

    # Theme swap changes only colour token 1, so only component B is dirty.
    if theme_value(second, 0, 0, 111) != 0 or theme_activate(second) != 0:
        return 34
    if dirty(component_a) != 0 or dirty(component_b) != 1 or style_bg(root_b, 1) != 0:
        return 35
    if did_commit(component_b) != 0 or dirty(component_b) != 0:
        return 36
    # A second swap changes font, size and spacing but preserves both colours.
    # Only component A depends on those namespaces, and typed helpers consume
    # the new canonical values.
    if theme_activate(third) != 0 or dirty(component_a) != 1 or dirty(component_b) != 0:
        return 47
    if style_tx(root_a, 0, 0) != 0 or style_size(root_a, 0, 2) != 0:
        return 48
    if style_pad(root_a, 0) != 0 or style_gap(root_a, 1) != 0:
        return 49
    if style_get(root_a, 2) != 111 or style_get(root_a, 3) != 404:
        return 50
    if style_get(root_a, 4) != 50 or style_get(root_a, 5) != 50:
        return 51
    if style_get(root_a, 6) != 8 or style_get(root_a, 7) != 6:
        return 52
    if did_commit(component_a) != 0 or dirty(component_a) != 0:
        return 53
    if unmount(component_b) != 0 or next_dirty() == component_b:
        return 37
    print("gui-style-ok")
    return 0

main()
'''


def test_style_tokens_selective_dirtying_and_layout_geometry(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "gui-style-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "gui_style", _STYLE_PROGRAM
    )
