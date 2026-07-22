"""Behavior oracle for the loop-rotate family (AUD-P2-IR-PASS-LOOP-ROTATE).

Executes one bounded counting loop across ZERO, ONE, and MANY iteration
counts before the pass, after pcc's pass, and after an independent upstream
``opt -passes=loop-rotate`` run — result equality is the claim; the rotated
CFG shape stays a secondary diagnostic (CFG resemblance is not semantic
proof). Modeled on test_ir_passes_lower_expect_semantic_oracle.py.
"""
from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import llvmlite.binding as llvm
import pytest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.loop_rotate import loop_rotate_module
from pcc.ir_passes.parity import run_upstream_opt


ORACLE_MODE = "host llvmlite-MCJIT behavior + independent LLVM opt loop-rotate"
OPT_VERDICT = probe_executable_dependency("opt")

# Single-step counting loop (the subset's rotate candidate): returns the loop
# variable after exit, i.e. f(n) == max(n, 0). n <= 0 exercises the
# zero-iteration guard the rotation must preserve; n == 1 one iteration;
# larger n many iterations.
LOOP_IR = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
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


def test_loop_rotate_matches_original_and_independent_llvm_opt_behavior():
    if not OPT_VERDICT.available:
        pytest.fail(OPT_VERDICT.skip_reason())
    pcc_ir, changed = loop_rotate_module(LOOP_IR)
    assert changed is True
    upstream = run_upstream_opt(LOOP_IR, "loop-rotate")
    assert upstream.returncode == 0, upstream.stderr

    values = (-100, -1, 0, 1, 2, 7, 100, 4096)
    original_results = _execute_i32_unary(LOOP_IR, values)
    pcc_results = _execute_i32_unary(pcc_ir, values)
    upstream_results = _execute_i32_unary(upstream.ir_text, values)
    expected = tuple(max(value, 0) for value in values)

    assert original_results == expected
    assert pcc_results == expected
    assert upstream_results == expected


def test_selected_loop_rotate_claim_cannot_regress_to_structure_only():
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_loop_rotate_matches_original_and_independent_llvm_opt_behavior"
    )
    calls = {
        node.func.id
        for node in ast.walk(selected)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"loop_rotate_module", "run_upstream_opt", "_execute_i32_unary"} <= calls
    for node in ast.walk(selected):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        compared_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        assert not ({"pcc_ir", "upstream"} & compared_names), (
            "selected loop-rotate claim must use execution behavior, "
            "not IR substring membership"
        )
