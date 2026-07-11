"""Algorithm guards for hot pcc-Python runtime paths used by GC bootstrap."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GC_BACKEND_PY = _REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_backend.py"
_PY_OBJ_PY = _REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_obj.py"
_PY_CLASS_PY = _REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_class.py"


def _source_text(path: Path = _GC_BACKEND_PY) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(name: str, path: Path = _GC_BACKEND_PY) -> str:
    text = _source_text(path)
    module = ast.parse(text)
    lines = text.splitlines()
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name!r} not found in {path}")


def test_init_config_returns_cached_backend_for_hot_dispatch() -> None:
    init_config = _function_source("_init_config")
    backend = _function_source("pcc_gc_backend")
    c_src = (_REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_gc_backend.c").read_text(
        encoding="utf-8"
    )
    c_backend = c_src.split("int64_t pcc_gc_backend(void)", 1)[1].split("\n}", 1)[0]

    assert "def _init_config() -> int:" in init_config
    assert 'return load_i32(global_addr("pcc_gc_backend_selected"), 0)' in init_config
    assert "return backend" in init_config
    assert "return _init_config()" in backend
    assert "if (pcc_gc_config_initialized) return pcc_gc_selected_backend;" in c_backend
    assert c_backend.index("pcc_gc_config_initialized") < c_backend.index("pcc_gc_init_config()")


def test_hot_gc_paths_reuse_init_config_backend_value() -> None:
    hot_functions = (
        "pcc_gc_try_minor_alloc",
        "pcc_gc_backend4_try_zpage_alloc",
        "_promote_young_if_known",
        "pcc_gc_step",
        "pcc_gc_note_alloc",
        "pcc_gc_note_object_allocated_sized",
        "pcc_gc_select_relocation_set",
        "pcc_gc_backend4_evacuation_page_drain",
        "pcc_gc_note_slot_write_barrier",
        "pcc_gc_note_frame_leave",
    )

    for name in hot_functions:
        body = _function_source(name)
        marker = "backend: int = _init_config()"
        if marker in body:
            suffix = body.split(marker, 1)[1]
        else:
            assert 'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0' in body, name
            assert "backend = _init_config()" in body, name
            assert 'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)' in body, name
            suffix = body.split(
                'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)',
                1,
            )[1]
        assert "pcc_gc_backend()" not in suffix, name


def test_gc1_auto_step_uses_selected_backend_without_exported_query() -> None:
    body = _function_source("_maybe_auto_step")

    assert 'load_i32(global_addr("pcc_gc_backend_selected"), 0) != 1' in body
    assert "pcc_gc_backend()" not in body


def test_py_obj_hot_paths_use_steady_state_backend_fastpath() -> None:
    helper = _function_source("_gc_backend_fast", _PY_OBJ_PY)
    assert 'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0' in helper
    assert "return pcc_gc_backend()" in helper
    assert 'return load_i32(global_addr("pcc_gc_backend_selected"), 0)' in helper

    inlined_hot_functions = (
        "pcc_gc_load_ptr",
        "pcc_gc_store_ptr",
        "py_incref",
        "py_decref",
    )
    inline_markers = (
        'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0',
        "backend = pcc_gc_backend()",
        'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)',
    )
    for name in inlined_hot_functions:
        body = _function_source(name, _PY_OBJ_PY)
        for marker in inline_markers:
            assert marker in body, name
        assert "backend: int = _gc_backend_fast()" not in body, name

    helper_hot_functions = (
        "pcc_gc_release",
        "pcc_gc_load_borrowed_ptr",
        "pcc_gc_resolve_owned_ptr",
        "pcc_gc_store_root",
        "pcc_gc_collect",
    )

    for name in helper_hot_functions:
        body = _function_source(name, _PY_OBJ_PY)
        marker = "backend: int = _gc_backend_fast()"
        assert marker in body, name
        assert "pcc_gc_backend()" not in body.split(marker, 1)[1], name


def test_pcc_gc_release_skips_backend_query_for_null_and_tagged_ints() -> None:
    py_body = _function_source("pcc_gc_release", _PY_OBJ_PY)
    py_prefix = py_body.split("backend: int = _gc_backend_fast()", 1)[0]
    assert "if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:" in py_prefix
    assert "return" in py_prefix
    assert "_gc_backend_fast()" not in py_prefix

    c_src = (_REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_obj.c").read_text(
        encoding="utf-8"
    )
    c_body = c_src.split("void pcc_gc_release(PyObject *o)", 1)[1].split("\n}", 1)[0]
    c_prefix = c_body.split("int64_t backend = pcc_gc_backend();", 1)[0]
    assert "if (o == NULL || PY_IS_TAGGED_INT(o)) return;" in c_prefix
    assert "pcc_gc_backend()" not in c_prefix


def test_py_gc_track_checks_threads_before_backend_query() -> None:
    for name in ("py_gc_track", "py_gc_untrack"):
        body = _function_source(name, _REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_obj_gc.py")
        assert "if pcc_threads_enabled() != 0 and pcc_gc_backend() == 4:" in body, name
        assert "if pcc_gc_backend() == 4 and pcc_threads_enabled() != 0:" not in body, name

    c_src = (_REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_obj_gc.c").read_text(
        encoding="utf-8"
    )
    for name in ("void py_gc_track", "void py_gc_untrack"):
        body = c_src.split(name, 1)[1].split("\n}", 1)[0]
        condition = body.split("py_gc_table_lock", 1)[0]
        assert "pcc_threads_enabled()\n        && pcc_gc_backend()" in condition, name
        assert "pcc_gc_backend() == PCC_GC_KIND_COLORED_RELOCATING\n        && pcc_threads_enabled()" not in condition, name


def test_class_cstr_equality_rejects_prefix_mismatch_before_strlen() -> None:
    body = _function_source("_strs_eq", _PY_CLASS_PY)

    prefix = body.split("n: int = strlen(a)", 1)[0]
    assert "a0: int = load_i8(a, 0) & 0xFF" in prefix
    assert "b0: int = load_i8(b, 0) & 0xFF" in prefix
    assert "if a0 != b0:" in prefix
    assert "a1: int = load_i8(a, 1) & 0xFF" in prefix
    assert "b1: int = load_i8(b, 1) & 0xFF" in prefix
    assert "if a1 != b1:" in prefix
