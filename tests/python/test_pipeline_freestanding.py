"""Facade and finite-boundary contracts for pipeline_freestanding."""
from __future__ import annotations

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_freestanding


def test_pipeline_freestanding_facade_is_thin():
    assert pipeline._source_declares_freestanding_module is (
        pipeline_freestanding.source_declares_freestanding_module
    )
    assert pipeline._freestanding_allowed_external_symbols is (
        pipeline_freestanding.freestanding_allowed_external_symbols
    )
    assert pipeline._source_call_arguments is (
        pipeline_freestanding.source_call_arguments
    )
    assert pipeline._freestanding_module_scope_extern_bindings is (
        pipeline_freestanding.freestanding_module_scope_extern_bindings
    )
    assert pipeline._validate_freestanding_ir is (
        pipeline_freestanding.validate_freestanding_ir
    )


def test_freestanding_directive_must_be_one_unconditional_module_assignment():
    assert pipeline_freestanding.source_declares_freestanding_module(
        "__pcc_freestanding__ = True\n"
    )
    assert not pipeline_freestanding.source_declares_freestanding_module(
        "value = True\n"
    )
    with pytest.raises(pipeline.PyPipelineError, match="module-scope"):
        pipeline_freestanding.source_declares_freestanding_module(
            "__pcc_freestanding__: bool = True\n"
        )
    with pytest.raises(pipeline.PyPipelineError, match="only once"):
        pipeline_freestanding.source_declares_freestanding_module(
            "__pcc_freestanding__ = True\n__pcc_freestanding__ = True\n"
        )


def test_freestanding_extern_scanner_admits_only_finite_exact_boundaries():
    source = '''\
from pcc.unsafe import malloc, call_ptr1
from pcc.extern import c_int, c_ptr, extern
__pcc_freestanding__ = True
_main = extern("main", (c_int, c_ptr, c_ptr), c_int)
_wrong = extern("not_owned", (c_ptr,), c_ptr)
'''
    assert pipeline_freestanding.freestanding_module_scope_extern_bindings(
        source
    ) == [
        ("main", "(c_int,c_ptr,c_ptr)", "c_int"),
        ("not_owned", "(c_ptr,)", "c_ptr"),
    ]
    assert pipeline_freestanding.freestanding_allowed_external_symbols(
        source
    ) == {"malloc", "__pcc_verified_indirect_call__", "main"}


def test_freestanding_ir_verifier_accepts_local_and_named_machine_calls_only():
    valid = '''\
define i64 @local(i64 %value) {
entry:
  ret i64 %value
}
define i64 @owner(i64 %value) {
entry:
  %same = call i64 @local(i64 %value)
  %mem = call ptr @malloc(i64 8)
  ret i64 %same
}
'''
    pipeline_freestanding.validate_freestanding_ir(valid, {"malloc"})

    escaped = '''\
define ptr @owner(ptr %value) {
entry:
  %result = call ptr @py_obj_str(ptr %value)
  ret ptr %result
}
'''
    with pytest.raises(pipeline.PyPipelineError, match="managed-runtime"):
        pipeline_freestanding.validate_freestanding_ir(escaped, set())
