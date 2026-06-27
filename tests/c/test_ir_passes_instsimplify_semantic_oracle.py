"""Behavior oracle for the instsimplify family (AUD-P2-IR-PASS-INSTSIMPLIFY).

Executes the SAME function before the pass, after pcc's pass, and after an
independent upstream ``opt -passes=instsimplify`` run, over a bounded input
matrix — semantic equality is the claim; IR text remains a secondary
diagnostic only. Modeled on test_ir_passes_lower_expect_semantic_oracle.py.
"""
from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import llvmlite.binding as llvm
import pytest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.instsimplify import simplify_module_text
from pcc.ir_passes.parity import run_upstream_opt


ORACLE_MODE = "host llvmlite-MCJIT behavior + independent LLVM opt instsimplify"
OPT_VERDICT = probe_executable_dependency("opt")

# A chain of identity-simplifiable operations: instsimplify folds every step,
# and the function must still compute f(x) == x for every input.
SIMPLIFY_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 0
  %b = mul i32 %a, 1
  %c = and i32 %b, -1
  %d = or i32 %c, 0
  %e = xor i32 %d, 0
  %g = sub i32 %e, 0
  ret i32 %g
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


def test_instsimplify_matches_original_and_independent_llvm_opt_behavior():
    if not OPT_VERDICT.available:
        pytest.skip(OPT_VERDICT.skip_reason())
    pcc_ir, changed = simplify_module_text(SIMPLIFY_IR)
    assert changed is True
    upstream = run_upstream_opt(SIMPLIFY_IR, "instsimplify")
    assert upstream.returncode == 0, upstream.stderr

    values = (-(2**31), -257, -1, 0, 1, 7, 255, 256, 1024, 2**31 - 1)
    original_results = _execute_i32_unary(SIMPLIFY_IR, values)
    pcc_results = _execute_i32_unary(pcc_ir, values)
    upstream_results = _execute_i32_unary(upstream.ir_text, values)
    expected = values

    assert original_results == expected
    assert pcc_results == expected
    assert upstream_results == expected


def test_selected_instsimplify_claim_cannot_regress_to_structure_only():
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_instsimplify_matches_original_and_independent_llvm_opt_behavior"
    )
    calls = {
        node.func.id
        for node in ast.walk(selected)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"simplify_module_text", "run_upstream_opt", "_execute_i32_unary"} <= calls
    for node in ast.walk(selected):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        compared_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        assert not ({"pcc_ir", "upstream"} & compared_names), (
            "selected instsimplify claim must use execution behavior, "
            "not IR substring membership"
        )
