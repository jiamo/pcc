"""Direct contract and behavior tests for the canonical GUI tree kernel."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RUNTIME_KIT = REPO / "pcc" / "py_runtime" / "py" / "pcc_gui_kit.py"
PROJECT_WRAPPER = REPO / "projects" / "mac_diff_app" / "pcc_gui_kit.py"
APP = REPO / "projects" / "mac_diff_app" / "app.py"
KIT_WINDOW = REPO / "projects" / "mac_diff_app" / "kit_window.py"


def _compile_run(
    tmp_path: Path,
    pcc_py_runtime_archive: Path,
    name: str,
    source: str,
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


def test_mac_diff_uses_one_canonical_runtime_kit_owner() -> None:
    runtime = RUNTIME_KIT.read_text(encoding="utf-8")
    wrapper = PROJECT_WRAPPER.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    kit_window = KIT_WINDOW.read_text(encoding="utf-8")
    makefile = (REPO / "pcc" / "py_runtime" / "Makefile").read_text(
        encoding="utf-8"
    )

    assert "pcc_gui_kit" in makefile.split("FREESTANDING_PY_MODULES =", 1)[1]
    assert "@c_abi_typed_export(\"pcc_kit_destroy_subtree\"" in runtime
    assert "@c_abi_typed_export(\"pcc_kit_route_event_v2\"" in runtime
    assert "@c_abi_typed_export(\"pcc_kit_hit_path_v1\"" in runtime

    assert "contains no tree implementation" in wrapper
    assert "extern(\"pcc_kit_create\"" in wrapper
    assert "define_global_i64(" not in wrapper
    assert "calloc(" not in wrapper
    assert "def pcc_kit_create(" not in wrapper

    assert "import pcc_gui_kit as kit" in app
    assert "def kit_create(" not in app
    assert "define_global_i64(\"kit_pool\"" not in app
    assert "kit.pcc_kit_render(" in app
    assert "import pcc_gui_kit as kit" in kit_window


_MUTATION_PROGRAM = r'''
from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import define_global_i64_array, function_addr, global_addr, load_i64, stack_alloc, store_i64

init = extern("pcc_kit_init", (c_int64,), c_int32)
create = extern("pcc_kit_create", (c_int64,), c_int64)
valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
live = extern("pcc_kit_live_nodes", (), c_int64)
rect = extern("pcc_kit_rect", (c_int64,c_int64,c_int64,c_int64,c_int64,c_int32), c_void)
destroy = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)
reorder = extern("pcc_kit_reorder", (c_int64,c_int64,c_int64), c_int32)
focus = extern("pcc_kit_focus", (c_int64,), c_void)
focused = extern("pcc_kit_focused", (c_int64,), c_int32)
hover = extern("pcc_kit_hover", (c_int64,c_int64,c_int64,c_int32), c_int64)
hovered = extern("pcc_kit_hovered", (c_int64,), c_int32)
hit = extern("pcc_kit_hit", (c_int64,c_int64,c_int64), c_int64)
path = extern("pcc_kit_hit_path_v1", (c_int64,c_int64,c_int64,c_ptr,c_int64), c_int64)
route_v2 = extern("pcc_kit_route_event_v2", (c_int64,c_int64,c_int64,c_int64,c_ptr,c_int64), c_int64)
handler = extern("pcc_kit_handler", (c_int64,c_int32), c_void)
set_hook = extern("pcc_kit_set_removal_hook", (c_ptr,), c_void)

define_global_i64_array("removed_order", 0, 0, 0, 0)

@c_abi_typed_export("record_removed", "i64", ("i64",))
def record_removed(node: int) -> int:
    count = load_i64(global_addr("removed_order"), 0)
    store_i64(global_addr("removed_order"), 8 + count * 8, node)
    store_i64(global_addr("removed_order"), 0, count + 1)
    return 0

def main() -> int:
    if init(4) != 0:
        return 1
    root = create(-1)
    a = create(root)
    b = create(root)
    leaf = create(a)
    if root != 0 or a != 1 or b != 2 or leaf != 3 or live() != 4:
        return 2
    rect(root, 0, 0, 100, 100, 0xFF000000)
    rect(a, 0, 0, 80, 80, 0xFF111111)
    rect(b, 60, 60, 30, 30, 0xFF222222)
    rect(leaf, 0, 0, 40, 40, 0xFF333333)
    focus(leaf)
    if hover(root, 10, 10, 1) != leaf or hovered(leaf) != 1:
        return 3
    set_hook(function_addr("record_removed"))
    if destroy(a) != 2:
        return 4
    if valid(a) != 0 or valid(leaf) != 0 or focused(leaf) != 0:
        return 5
    if hovered(leaf) != 0:
        return 6
    if load_i64(global_addr("removed_order"), 0) != 2:
        return 7
    if load_i64(global_addr("removed_order"), 8) != leaf:
        return 8
    if load_i64(global_addr("removed_order"), 16) != a:
        return 9
    c = create(root)
    if c == a or valid(c) == 0 or live() != 3:
        return 10
    rect(b, 0, 0, 80, 80, 0xFF222222)
    rect(c, 0, 0, 80, 80, 0xFF444444)
    if hit(root, 10, 10) != c:
        return 11
    if reorder(root, c, b) != 0 or hit(root, 10, 10) != b:
        return 12
    if reorder(root, b, c) != 0 or hit(root, 10, 10) != c:
        return 13
    out = stack_alloc(24)
    if path(root, 10, 10, out, 1) != -2:
        return 14
    if path(root, 10, 10, out, 3) != 2:
        return 15
    if load_i64(out, 0) != c or load_i64(out, 8) != root:
        return 16
    handler(root, 1)
    if route_v2(root, 10, 10, 7, out, 3) != 2:
        return 17
    print("kit-mutation-ok")
    return 0

main()
'''


def test_reclaim_reorder_stale_ids_and_path(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "kit-mutation-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "kit_mutation", _MUTATION_PROGRAM
    )


_LAYOUT_RENDER_PROGRAM = r'''
from pcc.extern import c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, load_i64, stack_alloc, store_i64

init = extern("pcc_kit_init", (c_int64,), c_int32)
create = extern("pcc_kit_create", (c_int64,), c_int64)
rect = extern("pcc_kit_rect", (c_int64,c_int64,c_int64,c_int64,c_int64,c_int32), c_void)
text = extern("pcc_kit_text", (c_int64,c_int64,c_int64,c_ptr,c_int64,c_int64,c_int32), c_void)
layout = extern("pcc_kit_layout", (c_int64,c_int32), c_void)
dock = extern("pcc_kit_dock", (c_int64,c_int32), c_void)
padding = extern("pcc_kit_padding", (c_int64,c_int64,c_int64,c_int64,c_int64), c_void)
gap = extern("pcc_kit_gap", (c_int64,c_int64), c_void)
clip = extern("pcc_kit_clip_children", (c_int64,c_int32), c_void)
scroll_container = extern("pcc_kit_scroll_container", (c_int64,c_int32), c_void)
scroll = extern("pcc_kit_scroll", (c_int64,c_int64), c_int64)
scroll_max = extern("pcc_kit_scroll_max", (c_int64,), c_int64)
layout_tree = extern("pcc_kit_layout_tree", (c_int64,c_int64,c_int64), c_void)
geom = extern("pcc_kit_geometry_get", (c_int64,c_int64), c_int64)
render = extern("pcc_kit_render", (c_int64,c_ptr,c_ptr,c_ptr,c_ptr,c_ptr), c_void)

def main() -> int:
    if init(12) != 0:
        return 1
    root = create(-1)
    left = create(root)
    top = create(root)
    fill = create(root)
    rect(root, 0, 0, 100, 80, 0xFF010203)
    rect(left, 0, 0, 10, 0, 0xFF111111)
    rect(top, 0, 0, 0, 15, 0xFF222222)
    rect(fill, 0, 0, 0, 0, 0xFF333333)
    layout(root, 2)
    padding(root, 5, 6, 7, 8)
    gap(root, 3)
    dock(left, 1)
    dock(top, 2)
    dock(fill, 5)
    scroll_container(fill, 1)
    one = create(fill)
    two = create(fill)
    rect(one, 0, 0, 0, 40, 0xFF444444)
    rect(two, 0, 0, 0, 40, 0xFF555555)
    gap(fill, 2)
    layout_tree(root, 100, 80)
    if geom(left, 32) != 5 or geom(left, 40) != 6:
        return 2
    if geom(left, 48) != 10 or geom(left, 56) != 66:
        return 3
    if geom(top, 32) != 18 or geom(top, 40) != 6:
        return 4
    if geom(top, 48) != 75 or geom(top, 56) != 15:
        return 5
    if geom(fill, 32) != 18 or geom(fill, 40) != 24:
        return 6
    if geom(fill, 48) != 75 or geom(fill, 56) != 48:
        return 7
    if scroll_max(fill) != 34 or scroll(fill, 999) != 34:
        return 8
    layout_tree(root, 100, 80)
    if geom(one, 40) != -10 or geom(two, 40) != 32:
        return 9

    if init(4) != 0:
        return 10
    clip_root = create(-1)
    label = create(clip_root)
    partial = create(clip_root)
    rect(clip_root, 0, 0, 50, 20, 0xFFABCDEF)
    clip(clip_root, 1)
    text(label, -7, -5, cstr("0123456789"), 10, 14, 0xFF102030)
    rect(partial, -10, 5, 20, 10, 0xFF405060)
    rects = stack_alloc(128)
    colors = stack_alloc(16)
    texts = stack_alloc(192)
    rn = stack_alloc(8)
    tn = stack_alloc(8)
    store_i64(rn, 0, 0)
    store_i64(tn, 0, 0)
    render(clip_root, rects, colors, rn, texts, tn)
    if load_i64(rn, 0) != 2 or load_i64(tn, 0) != 1:
        return 11
    if load_i64(rects, 32) != 0 or load_i64(rects, 48) != 10:
        return 12
    if load_i64(texts, 0) != 0 or load_i64(texts, 8) != 0:
        return 13
    if load_i64(texts, 16) != 7:
        return 14
    print("kit-layout-render-ok")
    return 0

main()
'''


def test_dock_scroll_padding_gap_and_clipped_commands(
    tmp_path: Path, pcc_py_runtime_archive: Path
) -> None:
    assert "kit-layout-render-ok" in _compile_run(
        tmp_path, pcc_py_runtime_archive, "kit_layout_render", _LAYOUT_RENDER_PROGRAM
    )
