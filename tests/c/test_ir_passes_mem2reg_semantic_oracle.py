"""Behavior oracle for the mem2reg family (AUD-P2-IR-PASS-MEM2REG).

Executes one branch-merged stack-slot program before the pass, after pcc's
pass, and after an independent upstream ``opt -passes=mem2reg`` run, over a
bounded input matrix — result equality plus verifier validity are asserted
independently of the produced IR text. Modeled on
test_ir_passes_lower_expect_semantic_oracle.py.
"""
from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import llvmlite.binding as llvm
import pytest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.mem2reg import mem2reg_module
from pcc.ir_passes.parity import run_upstream_opt


ORACLE_MODE = "host llvmlite-MCJIT behavior + independent LLVM opt mem2reg"
OPT_VERDICT = probe_executable_dependency("opt")

# Branch-merged stack slot: both arms store to the same alloca, the merged
# load feeds the return. f(x) == -x for x < 0 else 2*x. Promotion must build
# the phi without changing either arm's value.
DIAMOND_IR = """
define i32 @f(i32 %x) {
entry:
  %p = alloca i32
  %c = icmp slt i32 %x, 0
  br i1 %c, label %neg, label %pos
neg:
  %m = sub i32 0, %x
  store i32 %m, ptr %p
  br label %merge
pos:
  %d = mul i32 %x, 2
  store i32 %d, ptr %p
  br label %merge
merge:
  %v = load i32, ptr %p
  ret i32 %v
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


def test_mem2reg_matches_original_and_independent_llvm_opt_behavior():
    if not OPT_VERDICT.available:
        pytest.skip(OPT_VERDICT.skip_reason())
    pcc_ir, changed = mem2reg_module(DIAMOND_IR)
    assert changed is True
    upstream = run_upstream_opt(DIAMOND_IR, "mem2reg")
    assert upstream.returncode == 0, upstream.stderr

    values = (-1000, -7, -1, 0, 1, 3, 255, 4096)
    original_results = _execute_i32_unary(DIAMOND_IR, values)
    pcc_results = _execute_i32_unary(pcc_ir, values)
    upstream_results = _execute_i32_unary(upstream.ir_text, values)
    expected = tuple((-value) if value < 0 else value * 2 for value in values)

    assert original_results == expected
    assert pcc_results == expected
    assert upstream_results == expected


def test_selected_mem2reg_claim_cannot_regress_to_structure_only():
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_mem2reg_matches_original_and_independent_llvm_opt_behavior"
    )
    calls = {
        node.func.id
        for node in ast.walk(selected)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"mem2reg_module", "run_upstream_opt", "_execute_i32_unary"} <= calls
    for node in ast.walk(selected):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        compared_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        assert not ({"pcc_ir", "upstream"} & compared_names), (
            "selected mem2reg claim must use execution behavior, "
            "not IR substring membership"
        )
