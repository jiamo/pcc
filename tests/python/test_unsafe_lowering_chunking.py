from __future__ import annotations

import ast
from pathlib import Path

from pcc.py_frontend.codegen.unsafe_lowering import (
    UNSAFE_INTRINSICS,
    _UNSAFE_INTRINSIC_FAMILIES,
    _unsafe_intrinsic_family,
)


REPO = Path(__file__).absolute().parents[2]
SOURCE = REPO / "pcc" / "py_frontend" / "codegen" / "unsafe_lowering.py"

HELPERS = (
    "_emit_unsafe_va_numeric_f64",
    "_emit_unsafe_page_global_alloc",
    "_emit_unsafe_ptr_memory",
    "_emit_unsafe_read_write_rss",
    "_emit_unsafe_file_mutation",
    "_emit_unsafe_socket_control",
    "_emit_unsafe_socket_io_poll",
    "_emit_unsafe_system_info",
    "_emit_unsafe_time_process_control",
    "_emit_unsafe_spawn_process_pipe",
    "_emit_unsafe_spawn_process",
    "_emit_unsafe_env_access_stat",
    "_emit_unsafe_indirect_calls_a",
    "_emit_unsafe_indirect_calls_b",
    "_emit_unsafe_loader_waitset_gc",
)


def _unsafe_mixin_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    mixin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "UnsafeIntrinsicMixin"
    )
    return {
        node.name: node
        for node in mixin.body
        if isinstance(node, ast.FunctionDef)
    }


def test_unsafe_intrinsic_families_are_complete_unique_and_fail_closed() -> None:
    flattened = tuple(
        intrinsic
        for family in _UNSAFE_INTRINSIC_FAMILIES
        for intrinsic in family
    )
    assert len(_UNSAFE_INTRINSIC_FAMILIES) == len(HELPERS)
    assert len(flattened) == len(set(flattened))

    specialized = {
        intrinsic
        for intrinsic in UNSAFE_INTRINSICS
        if intrinsic.startswith("atomic_") or intrinsic.startswith("define_")
    }
    assert set(flattened) | specialized == set(UNSAFE_INTRINSICS)

    for expected_family, family in enumerate(_UNSAFE_INTRINSIC_FAMILIES):
        for intrinsic in family:
            assert _unsafe_intrinsic_family(intrinsic) == expected_family
    assert _unsafe_intrinsic_family("not_a_real_unsafe_intrinsic") == -1


def test_unsafe_intrinsic_dispatch_and_helpers_stay_bounded() -> None:
    methods = _unsafe_mixin_methods()
    dispatcher = methods["_emit_unsafe_intrinsic_call"]
    assert dispatcher.end_lineno is not None
    assert dispatcher.end_lineno - dispatcher.lineno + 1 <= 85

    for helper_name in HELPERS:
        helper = methods[helper_name]
        assert helper.end_lineno is not None
        assert helper.end_lineno - helper.lineno + 1 <= 430, helper_name
