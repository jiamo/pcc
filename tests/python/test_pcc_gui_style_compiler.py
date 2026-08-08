"""Bounded class-string compiler and generation-selective style cache."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
STYLE = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_style.py"


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


def test_compiler_owner_uses_frozen_cache_record_and_fixed_warm_arenas() -> None:
    source = STYLE.read_text(encoding="utf-8")
    assert "CACHE_ENTRY_SIZE = 80" in source
    assert "CACHE_CLASS_BYTES = 257" in source
    assert "CACHE_OPERATION_CAPACITY = 64" in source
    assert '"pcc_gui_style_register_named_utility"' in source
    assert '"pcc_gui_style_compile"' in source
    assert '"pcc_gui_style_apply_class"' in source
    apply_body = source.split("def pcc_gui_style_apply_class(", 1)[1].split(
        '@c_abi_typed_export("pcc_gui_style_parser_invocations"', 1
    )[0]
    assert "calloc(" not in apply_body
    assert "_compile_candidates(" not in apply_body
    assert "pcc_gui_style_apply(component_id, node_id, operation)" in apply_body


_COMPILER_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import calloc, cstr, function_addr, load_i32, load_i64, load_i8, ptr_is_null, stack_alloc

kit_init = extern("pcc_kit_init", (c_int64,), c_int32)
kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
style_get = extern("pcc_kit_style_get", (c_int64,c_int32), c_int64)
components_init = extern("pcc_gui_components_init", (c_int64,c_int64,c_int64), c_int32)
register_render = extern("pcc_gui_component_register_render", (c_int32,c_ptr), c_int32)
mount = extern("pcc_gui_component_mount", (c_int64,c_int64,c_int32,c_ptr,c_int32,c_ptr,c_int32), c_int64)
theme_init = extern("pcc_gui_theme_init", (c_ptr,), c_int32)
theme_set = extern("pcc_gui_theme_set_token", (c_ptr,c_int32,c_int32,c_int64), c_int32)
theme_activate = extern("pcc_gui_theme_activate", (c_ptr,), c_int32)
theme_bump_namespace = extern("pcc_gui_theme_bump_namespace", (c_int32,), c_int32)
style_init = extern("pcc_gui_style_init", (c_int64,c_int64), c_int32)
compiler_init = extern("pcc_gui_style_compiler_init", (c_int64,), c_int32)
register_named = extern("pcc_gui_style_register_named_utility", (c_int32,c_int32,c_int32,c_int32,c_int32), c_int32)
compile_style = extern("pcc_gui_style_compile", (c_ptr,c_int64), c_int32)
copy_operations = extern("pcc_gui_style_cached_operations", (c_ptr,c_int64,c_ptr,c_int32), c_int32)
apply_class = extern("pcc_gui_style_apply_class", (c_int64,c_int64,c_ptr,c_int64), c_int32)
style_gap = extern("pcc_gui_style_gap", (c_int64,c_int32), c_int32)
parser_calls = extern("pcc_gui_style_parser_invocations", (), c_int64)
allocation_count = extern("pcc_gui_style_cache_allocation_count", (), c_int64)
hit_count = extern("pcc_gui_style_cache_hit_count", (), c_int64)
miss_count = extern("pcc_gui_style_cache_miss_count", (), c_int64)
dirty = extern("pcc_gui_style_component_dirty", (c_int64,), c_int32)
did_commit = extern("pcc_gui_style_component_did_commit", (c_int64,), c_int32)

@c_abi_typed_export("style_compiler_render", "i32", ("ptr",))
def style_compiler_render(context) -> int:
    return 0

def raw_len(text) -> int:
    n = 0
    while load_i8(text, n) != 0:
        n = n + 1
    return n

def compile_text(text) -> int:
    return compile_style(text, raw_len(text))

def apply_text(component: int, node: int, text) -> int:
    return apply_class(component, node, text, raw_len(text))

def main() -> int:
    if kit_init(2) != 0 or components_init(1, 1, 2) != 0:
        return 1
    if style_init(12, 16) != 0 or compiler_init(4) != 0:
        return 2
    if allocation_count() != 5 or compiler_init(1) != -103:
        return 3
    if register_render(1, function_addr("style_compiler_render")) != 0:
        return 4
    # Prefix ids: bg=1, font=3, pad=6, gap=7, x=8.
    if register_named(10, 1, 0, 1, 0) != 0:
        return 5
    if register_named(11, 7, 3, 7, 1) != 0 or register_named(12, 8, 3, 8, 1) != 0:
        return 6
    if register_named(13, 6, 3, 6, 0) != 0 or register_named(14, 3, 1, 3, 0) != 0:
        return 7
    theme = calloc(64, 8)
    if ptr_is_null(theme) or theme_init(theme) != 0:
        return 8
    if theme_set(theme, 0, 0, 0xFF112233) != 0 or theme_set(theme, 0, 1, 0xFF445566) != 0:
        return 9
    if theme_set(theme, 1, 0, 77) != 0 or theme_set(theme, 3, 2, 8) != 0 or theme_set(theme, 3, 3, 12) != 0:
        return 10
    if theme_activate(theme) != 0:
        return 11
    root = kit_create(-1)
    component = mount(-1, root, 1, theme, 0, theme, 0)
    if component < 0:
        return 12
    # A direct dependency can overlap a class dependency.  Replacing the
    # class later must retire only the class side of that ownership.
    if style_gap(root, 2) != 0:
        return 45

    ordered = cstr("bg-accent gap-2 x-3")
    if compile_text(ordered) != 3 or parser_calls() != 1 or miss_count() != 1:
        return 13
    operations = stack_alloc(3 * 40)
    if copy_operations(ordered, raw_len(ordered), operations, 3) != 3:
        return 14
    if load_i32(operations, 0) != 10 or load_i32(operations, 40) != 11 or load_i32(operations, 80) != 12:
        return 15
    if apply_text(component, root, ordered) != 0:
        return 16
    if style_get(root, 1) != 0xFF112233 or style_get(root, 7) != 8 or style_get(root, 8) != 12:
        return 17
    i = 0
    while i < 8:
        if apply_text(component, root, ordered) != 0:
            return 18
        i = i + 1
    if parser_calls() != 1 or allocation_count() != 5 or hit_count() < 10:
        return 19

    # An unrelated token edit keeps the entry warm.
    if theme_set(theme, 0, 1, 0xFF778899) != 0 or apply_text(component, root, ordered) != 0:
        return 20
    if parser_calls() != 1:
        return 21
    # An exact token edit recompiles only this entry and refreshes the op.
    if theme_set(theme, 3, 2, 9) != 0 or dirty(component) != 1:
        return 22
    if apply_text(component, root, ordered) != 0 or parser_calls() != 2 or style_get(root, 7) != 9:
        return 23
    if did_commit(component) != 0:
        return 24
    # Namespace and utility-schema generations are independent invalidators.
    if theme_bump_namespace(0) != 0 or apply_text(component, root, ordered) != 0 or parser_calls() != 3:
        return 25
    if did_commit(component) != 0:
        return 26
    if register_named(15, 4, 2, 4, 0) != 0:
        return 27
    if apply_text(component, root, ordered) != 0 or parser_calls() != 4:
        return 28

    # Source order is retained; the later operation for the same field wins.
    override = cstr("bg-accent bg-muted")
    if apply_text(component, root, override) != 0 or style_get(root, 1) != 0xFF778899:
        return 29
    copied = stack_alloc(80)
    if copy_operations(override, raw_len(override), copied, 2) != 2:
        return 30
    if load_i32(copied, 0) != 10 or load_i32(copied, 40) != 10:
        return 31
    # The old x candidate is gone, so editing its token is unrelated.  The
    # overlapping gap token remains a dependency because it was also applied
    # through the direct helper before the class was compiled.
    if theme_set(theme, 3, 3, 13) != 0 or dirty(component) != 0:
        return 46
    if theme_set(theme, 3, 2, 10) != 0 or dirty(component) != 1:
        return 47
    if style_gap(root, 2) != 0 or did_commit(component) != 0:
        return 48

    # Named and arbitrary modifiers are distinct flag shapes and have exact
    # generated values.  Arbitrary dense means 50 percent in this bounded v1.
    modifiers = cstr("bg-accent/50 x-3/[dense]")
    copied_modifiers = stack_alloc(80)
    if copy_operations(modifiers, raw_len(modifiers), copied_modifiers, 2) != 2:
        return 32
    if (load_i32(copied_modifiers, 12) & 2) == 0 or (load_i32(copied_modifiers, 52) & 4) == 0:
        return 33
    if load_i64(copied_modifiers, 16) != 0x7F112233 or load_i64(copied_modifiers, 56) != 6:
        return 34
    negative = cstr("-x-3/[dense]")
    if copy_operations(negative, raw_len(negative), copied_modifiers, 2) != 1:
        return 35
    if load_i64(copied_modifiers, 16) != -6:
        return 36

    if compile_text(cstr("bg-accent bg-accent")) != -102:
        return 37
    if compile_text(cstr("unknown-x")) != -118:
        return 38
    if compile_text(cstr("-bg-accent")) != -117:
        return 39
    if compile_text(cstr("font-body/50")) != -117:
        return 40
    if compile_text(cstr("bg-accent/[mystery]")) != -117:
        return 41
    # More than one registered generator for one prefix is an explicit
    # ambiguity, never first-registration-wins.
    if register_named(20, 1, 0, 2, 0) != 0:
        return 42
    if compile_text(cstr("bg-accent")) != -119:
        return 43
    if allocation_count() != 5:
        return 44
    print("gui-style-compiler-ok")
    return 0

main()
'''


def test_candidate_parser_cache_modifiers_order_and_selective_recompile(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "gui-style-compiler-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "gui_style_compiler", _COMPILER_PROGRAM
    )
