"""Behavior oracle for the inline family (AUD-P2-IR-PASS-INLINE).

Executes one bounded caller/callee program before the pass, after pcc's
pass, and after an independent upstream ``opt -passes=inline`` run, over a
bounded input matrix — return behavior is the claim (mode:
host llvmlite-MCJIT execution), not the shape of the inlined body. Modeled
on test_ir_passes_lower_expect_semantic_oracle.py.
"""
from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import llvmlite.binding as llvm
import pytest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.inline import inline_module
from pcc.ir_passes.parity import run_upstream_opt


ORACLE_MODE = "host llvmlite-MCJIT behavior + independent LLVM opt inline"
OPT_VERDICT = probe_executable_dependency("opt")

# Internal single-block callee: the subset inliner replaces the call and the
# caller must still compute f(x) == 2*x + 1 for every input (i32 wraparound
# included — the matrix has both int32 extremes).
CALL_IR = """
define internal i32 @dbl(i32 %x) {
entry:
  %r = mul i32 %x, 2
  ret i32 %r
}
define i32 @f(i32 %x) {
entry:
  %a = call i32 @dbl(i32 %x)
  %b = add i32 %a, 1
  ret i32 %b
}
"""


def _execute_i32_unary(ir_text: str, values: tuple[int, ...]) -> tuple[int, ...]:
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    module = llvm.parse_assembly(ir_text)
    module.verify()
    machine = llvm.Target.from_default_triple().create_target_machine()
    engine = llvm.create_mcjit_compiler(module, machine)
    engine.finalize_object()
    address = engine.get_function_address("f")
    assert address != 0
    function = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32)(address)
    return tuple(int(function(value)) for value in values)


def _i32(value: int) -> int:
    wrapped = (value & 0xFFFFFFFF)
    return wrapped - 2**32 if wrapped >= 2**31 else wrapped


def test_inline_matches_original_and_independent_llvm_opt_behavior():
    if not OPT_VERDICT.available:
        pytest.skip(OPT_VERDICT.skip_reason())
    pcc_ir, changed = inline_module(CALL_IR)
    assert changed is True
    upstream = run_upstream_opt(CALL_IR, "inline")
    assert upstream.returncode == 0, upstream.stderr

    values = (-(2**31), -4096, -1, 0, 1, 21, 255, 2**30, 2**31 - 1)
    original_results = _execute_i32_unary(CALL_IR, values)
    pcc_results = _execute_i32_unary(pcc_ir, values)
    upstream_results = _execute_i32_unary(upstream.ir_text, values)
    expected = tuple(_i32(_i32(value * 2) + 1) for value in values)

    assert original_results == expected
    assert pcc_results == expected
    assert upstream_results == expected


def test_selected_inline_claim_cannot_regress_to_structure_only():
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_inline_matches_original_and_independent_llvm_opt_behavior"
    )
    calls = {
        node.func.id
        for node in ast.walk(selected)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"inline_module", "run_upstream_opt", "_execute_i32_unary"} <= calls
    for node in ast.walk(selected):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        compared_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        assert not ({"pcc_ir", "upstream"} & compared_names), (
            "selected inline claim must use execution behavior, "
            "not IR substring membership"
        )
