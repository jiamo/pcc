from __future__ import annotations

import pytest

from pcc.ir_passes.parity import normalize_ir
from pcc.llvm_capi import binding as pcc_bind
from pcc.passes.llvm_text_pipeline import find_opt_binary, run_pipeline


_PROMOTABLE_STACK_IR = """
define i32 @f() {
entry:
  %x = alloca i32, align 4
  store i32 42, ptr %x, align 4
  %v = load i32, ptr %x, align 4
  ret i32 %v
}
""".strip()


@pytest.fixture(scope="module", autouse=True)
def _init_llvm():
    pcc_bind.initialize_native_target()
    pcc_bind.initialize_native_asmprinter()


def test_llvm_capi_memory_pass_pipeline_promotes_stack_slot():
    out = pcc_bind.run_passes_on_ir(
        _PROMOTABLE_STACK_IR,
        "mem2reg,instcombine",
    )

    assert "alloca" not in out
    assert "store i32" not in out
    assert "load i32" not in out
    assert "ret i32 42" in out
    pcc_bind.parse_assembly(out).verify()


@pytest.mark.pcc_gate(unavailable=None if find_opt_binary() is not None else "matching llvm opt not installed")
def test_llvm_capi_memory_pipeline_matches_opt_text_pipeline_for_basic_ir():
    pipeline = "mem2reg,instcombine,simplifycfg"
    opt_path = find_opt_binary()
    assert opt_path is not None

    memory_out = pcc_bind.run_passes_on_ir(_PROMOTABLE_STACK_IR, pipeline)
    text_out = run_pipeline(opt_path, pipeline, _PROMOTABLE_STACK_IR)

    assert normalize_ir(memory_out) == normalize_ir(text_out)

