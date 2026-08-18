from __future__ import annotations

from pcc.backend.self_backend_call_flags import (
    CALL_FLAG_CONTINUATION,
    CALL_FLAG_EXCEPTION_POLL,
    CALL_FLAG_FRAME_ENTER,
    CALL_FLAG_FRAME_LEAVE,
    CALL_FLAG_INDIRECT,
    CALL_FLAG_LLVM_INTRINSIC,
    CALL_FLAG_LOOP_SAFEPOINT,
    CALL_FLAG_VARARG,
    classify_call_flags,
)
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KINDS
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module


def test_call_flags_classify_stackmap_semantics_once() -> None:
    cases = (
        ("pcc_gc_frame_enter", False, False, CALL_FLAG_FRAME_ENTER),
        ("pcc_gc_frame_enter_lifo", False, False, CALL_FLAG_FRAME_ENTER),
        ("pcc_gc_frame_leave", False, False, CALL_FLAG_FRAME_LEAVE),
        ("pcc_gc_frame_leave_lifo", False, False, CALL_FLAG_FRAME_LEAVE),
        ("llvm.assume", False, False, CALL_FLAG_LLVM_INTRINSIC),
        ("py_err_occurred", False, False, CALL_FLAG_EXCEPTION_POLL),
        ("task_continuation_resume", False, False, CALL_FLAG_CONTINUATION),
        ("worker__gen_resume", False, False, CALL_FLAG_CONTINUATION),
        ("worker__vthread_resume", False, False, CALL_FLAG_CONTINUATION),
        ("pcc_thread_safepoint", False, False, CALL_FLAG_LOOP_SAFEPOINT),
        ("pcc_gc_safepoint", False, False, CALL_FLAG_LOOP_SAFEPOINT),
        ("ordinary", False, False, 0),
        ("ordinary", True, True, CALL_FLAG_INDIRECT | CALL_FLAG_VARARG),
    )
    for callee, indirect, vararg, expected in cases:
        assert classify_call_flags(callee, indirect, vararg) == expected


def test_parser_publishes_call_flags_in_final_kernel_record() -> None:
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

declare void @pcc_gc_frame_enter(ptr, ptr)
declare i64 @py_err_occurred()
declare void @ordinary()
declare void @llvm.assume(i1)

define void @calls() {
entry:
  call void @pcc_gc_frame_enter(ptr null, ptr null)
  %err = call i64 @py_err_occurred()
  call void @ordinary()
  call void @llvm.assume(i1 true)
  ret void
}
""".strip()
    )
    kernel = get_indexed_function_kernel(module.functions[0])
    expected = {
        "pcc_gc_frame_enter": CALL_FLAG_FRAME_ENTER,
        "py_err_occurred": CALL_FLAG_EXCEPTION_POLL,
        "ordinary": 0,
        "llvm.assume": CALL_FLAG_LLVM_INTRINSIC,
    }
    block = kernel.block_fact(0)
    instruction_index = 0
    observed = {}
    while instruction_index < block.second:
        metadata = kernel.instruction_metadata_by_id(
            block.first + instruction_index
        )
        if PARSED_INSTRUCTION_KINDS[metadata.first] == "call":
            header = kernel.call_header(metadata.second)
            observed[kernel.call_texts[header.second]] = header.third
        instruction_index += 1
    assert observed == expected
