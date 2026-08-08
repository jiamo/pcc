from __future__ import annotations

import ast
import ctypes
from pathlib import Path

import llvmlite.binding as llvm
import pytest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.lower_expect import lower_expect_text
from pcc.ir_passes.parity import run_upstream_opt
from pcc.passes.llvm_text_pipeline import find_opt_binary


ORACLE_MODE = "host llvmlite-MCJIT behavior + independent LLVM opt lower-expect"
OPT_VERDICT = probe_executable_dependency(
    "opt",
    resolver=lambda _name: find_opt_binary(),
)

EXPECT_IR = """
declare i32 @llvm.expect.i32(i32, i32)

define i32 @f(i32 %x) {
entry:
  %expected = call i32 @llvm.expect.i32(i32 %x, i32 7)
  %masked = and i32 %expected, 255
  %scaled = mul i32 %masked, 3
  ret i32 %scaled
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


def test_lower_expect_matches_original_and_independent_llvm_opt_behavior():
    if not OPT_VERDICT.available:
        pytest.fail(OPT_VERDICT.skip_reason())
    pcc_ir, changed = lower_expect_text(EXPECT_IR)
    assert changed is True
    upstream = run_upstream_opt(
        EXPECT_IR,
        "lower-expect",
        opt_path=OPT_VERDICT.resolved_path,
    )
    assert upstream.returncode == 0, upstream.stderr

    values = (-257, -1, 0, 1, 7, 255, 256, 1024, 2**31 - 1, -(2**31))
    original_results = _execute_i32_unary(EXPECT_IR, values)
    pcc_results = _execute_i32_unary(pcc_ir, values)
    upstream_results = _execute_i32_unary(upstream.ir_text, values)
    expected = tuple(((value & 255) * 3) for value in values)

    assert original_results == expected
    assert pcc_results == expected
    assert upstream_results == expected


def test_selected_lower_expect_claim_cannot_regress_to_structure_only():
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_lower_expect_matches_original_and_independent_llvm_opt_behavior"
    )
    calls = {
        node.func.id
        for node in ast.walk(selected)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"lower_expect_text", "run_upstream_opt", "_execute_i32_unary"} <= calls
    for node in ast.walk(selected):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        compared_names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        assert not ({"pcc_ir", "upstream"} & compared_names), (
            "selected lower-expect claim must use execution behavior, not IR substring membership"
        )
