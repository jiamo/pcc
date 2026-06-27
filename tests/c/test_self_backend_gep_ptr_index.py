"""Regression: aarch64 self-backend getelementptr with symbol / pointer indices.

Before this fix ``materialize_index_to_x10`` in
``pcc/backend/self_backend_aarch64_darwin_addr.py`` raised
``BackendUnavailable`` for two well-defined index-operand forms:

* a *symbol-valued* index (``getelementptr i8, ptr null, i64 @g``), which by
  LLVM's implicit ``ptrtoint`` semantics means "index == address of ``@g``"; and
* a *pointer-typed* index SSA value (``getelementptr i32, ptr @tab, ptr %ip``),
  whose 64-bit bit pattern is the index.

These are uncommon but legal, and this is the runnable (arm64-apple-darwin)
self-backend target, so we drive the real ``emit_self_asm`` lowering, assemble
with the system ``cc``, and run the executable — a true end-to-end run-diff,
not an oracle. The dedicated file exists because the natural gate file
``tests/c/test_self_backend.py`` is concurrently owned by another slice.

Native reference (``cc``) for the underlying arithmetic:
``int tab[4] = {10,20,30,40}; tab[2] == 30`` and
``(char*)null + &g == &g`` (see the values asserted below).
"""

import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_aarch64_darwin_addr import (
    emit_gep_offset,
    materialize_index_to_x10,
)
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_module_symbols import prepare_module_symbols
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_prepare import prepare_parsed_function
from pcc.backend.self_backend_stackprep import assign_stack_slots

_TRIPLE = 'target triple = "arm64-apple-darwin25.5.0"\n'


def _assemble_and_run(asm_text, tmp_path, name):
    asm_path = tmp_path / f"{name}.s"
    asm_path.write_text(asm_text, encoding="utf-8")
    exe_path = tmp_path / f"{name}.out"
    subprocess.run(
        ["cc", str(asm_path), "-o", str(exe_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return subprocess.run(
        [str(exe_path)], capture_output=True, text=True, timeout=60
    )


def test_self_backend_gep_pointer_typed_index_uses_bit_pattern(tmp_path):
    """A pointer-typed SSA value used as a GEP index is its own 64-bit index.

    ``%ip = inttoptr i64 2`` then ``getelementptr i32, ptr @tab, ptr %ip`` must
    land on ``tab[2] == 30``. Previously this raised BackendUnavailable
    ("does not support pointer-typed getelementptr indices").
    """
    ir_text = _TRIPLE + (
        "@tab = global [4 x i32] [i32 10, i32 20, i32 30, i32 40]\n"
        "define i32 @main() {\n"
        "entry:\n"
        "  %ip = inttoptr i64 2 to ptr\n"
        "  %p = getelementptr i32, ptr @tab, ptr %ip\n"
        "  %v = load i32, ptr %p\n"
        "  ret i32 %v\n"
        "}\n"
    )
    asm_text = emit_self_asm(ir_text)
    run = _assemble_and_run(asm_text, tmp_path, "ptr_idx")
    assert run.returncode == 30


def test_self_backend_gep_symbol_valued_index_is_symbol_address(tmp_path):
    """A symbol used as a GEP index means ``ptrtoint(symbol)`` (its address).

    ``getelementptr i8, ptr null, i64 @g`` therefore equals ``&g`` exactly, so
    it must compare equal to ``getelementptr i8, ptr @g, i64 0`` (also ``&g``),
    yielding 1. Previously the symbol index raised BackendUnavailable
    ("does not support symbol-valued getelementptr indices").
    """
    ir_text = _TRIPLE + (
        "@g = global i32 7\n"
        "define i32 @main() {\n"
        "entry:\n"
        "  %p = getelementptr i8, ptr null, i64 @g\n"
        "  %q = getelementptr i8, ptr @g, i64 0\n"
        "  %pi = ptrtoint ptr %p to i64\n"
        "  %qi = ptrtoint ptr %q to i64\n"
        "  %eq = icmp eq i64 %pi, %qi\n"
        "  %r = zext i1 %eq to i32\n"
        "  ret i32 %r\n"
        "}\n"
    )
    asm_text = emit_self_asm(ir_text)
    run = _assemble_and_run(asm_text, tmp_path, "sym_idx")
    assert run.returncode == 1


def _prepare_symbol_func(ir_text):
    module = parse_self_backend_module(ir_text)
    symbols = prepare_module_symbols(
        ir_text, list(module.globals_), list(module.functions)
    )
    func = module.functions[0]
    prepare_parsed_function(func)
    assign_stack_slots(func, aggregate_returned_indirect=lambda _ty: False)
    return func, symbols


def test_materialize_index_to_x10_symbol_needs_module_symbols():
    """Without module symbols the symbol-index path reports a precise error
    rather than the old blanket "does not support" refusal."""
    func, _symbols = _prepare_symbol_func(
        _TRIPLE
        + "@g = global i32 0\n"
        + "define i32 @main() {\nentry:\n  ret i32 0\n}\n"
    )
    with pytest.raises(BackendUnavailable, match="without module symbols"):
        materialize_index_to_x10(func, "@g")


def test_materialize_index_to_x10_symbol_materializes_address():
    """A symbol index materializes the symbol *address* into x10 (defined
    global -> PAGE/PAGEOFF), matching ptrtoint(symbol) semantics."""
    func, symbols = _prepare_symbol_func(
        _TRIPLE
        + "@g = global i32 0\n"
        + "define i32 @main() {\nentry:\n  ret i32 0\n}\n"
    )
    lines = materialize_index_to_x10(func, "@g", symbols)
    assert lines == [
        "  adrp x10, _g@PAGE",
        "  add x10, x10, _g@PAGEOFF",
    ]


def test_emit_gep_offset_threads_module_symbols_for_symbol_index():
    """emit_gep_offset forwards module symbols so a symbol first index scales
    the symbol address by the element stride (i32 -> lsl #2)."""
    func, symbols = _prepare_symbol_func(
        _TRIPLE
        + "@g = global i32 0\n"
        + "define i32 @main() {\nentry:\n  ret i32 0\n}\n"
    )
    lines = emit_gep_offset(
        func, TypeDesc("int", 32), ((TypeDesc("int", 64), "@g"),), symbols
    )
    assert lines == [
        "  adrp x10, _g@PAGE",
        "  add x10, x10, _g@PAGEOFF",
        "  add x11, x9, x10, lsl #2",
    ]
