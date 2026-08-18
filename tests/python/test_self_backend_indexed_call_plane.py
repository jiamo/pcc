from __future__ import annotations

import pcc.backend.self_backend_parse as self_backend_parse
from pcc.backend.self_backend_ir import (
    IndexedCallPlane,
    TypeDesc,
)
from pcc.backend.self_backend_kernel import (
    IndexedFunctionSeed,
    get_indexed_function_kernel,
)
from pcc.backend.self_backend_parse import parse_self_backend_module


def test_parser_publishes_final_function_call_plane_without_block_objects() -> None:
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

define i64 @run(i64 %value, ptr %callback) {
entry:
  %first = call i64 @callee(i64 %value, i64 7)
  %second = call i64 %callback(i64 %first)
  ret i64 %second
}
""".lstrip()
    )
    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    assert func.blocks == []
    assert func.indexed_seed is None
    assert kernel.block_diagnostic_projections == 0
    assert kernel.instruction_arena_diagnostic_projections == 0
    assert [
        kernel.instruction_payload_id_by_id(instruction_id)
        for instruction_id in range(2)
    ] == [0, 1]

    first_projection = kernel.diagnostic_instruction(0, 0).data
    assert first_projection[0] == "first"
    assert first_projection[1].describe() == "i64"
    assert first_projection[2] == "callee"
    assert first_projection[3] is False
    assert [value for _value_type, value in first_projection[4]] == [
        "value",
        "7",
    ]
    assert first_projection[7] == (0, 0)
    assert kernel.call_diagnostic_projections == 1
    assert kernel.indexed_call_plane is None

    first_header = kernel.call_header(0)
    first_span = kernel.call_span(0)
    first_arg = kernel.call_arg(first_header.fourth)
    constant_arg = kernel.call_arg(first_header.fourth + 1)
    assert first_span.third == kernel.value_id("first")
    assert first_arg.second == kernel.value_id("value")
    assert first_arg.third == -1
    assert constant_arg.second == -1
    assert kernel.call_texts[constant_arg.third] == "7"

    second_header = kernel.call_header(1)
    second_span = kernel.call_span(1)
    assert second_header.third & 1
    assert second_span.third == kernel.value_id("second")
    assert kernel.instruction_use_count(0, 1) == 2
    assert kernel.diagnostic_call_data(1)[2:4] == ("callback", True)


def test_indexed_call_type_identity_cache_skips_noncanonical_equal_key() -> None:
    plane = IndexedCallPlane()
    canonical = TypeDesc("int", width=64)
    equal_key = TypeDesc("int", width=64)

    assert plane.intern_type(canonical) == 0
    assert plane.intern_type(equal_key) == 0
    canonical_entry = plane.type_identity_ids[id(canonical)]
    assert canonical_entry[0] == 0
    assert canonical_entry[1] is canonical
    assert id(equal_key) not in plane.type_identity_ids


def test_call_publication_bypasses_generic_instruction_appender(monkeypatch) -> None:
    def reject_generic_append(*_args):
        raise AssertionError("indexed call used generic instruction appender")

    monkeypatch.setattr(
        IndexedFunctionSeed,
        "append_instruction",
        reject_generic_append,
    )
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

declare i64 @callee(i64)

define i64 @run(i64 %value) {
entry:
  %result = call i64 @callee(i64 %value)
  ret i64 %result
}
""".lstrip()
    )

    kernel = get_indexed_function_kernel(module.functions[0])
    assert kernel.instruction_metadata.get4_unchecked(0).second == 0


def test_scalar_call_span_lane_bypasses_regex_and_publishes_packed_calls(
    monkeypatch,
) -> None:
    class RejectCallRegex:
        def match(self, _line):
            raise AssertionError("canonical scalar call reached regex")

    monkeypatch.setattr(self_backend_parse, "_CALL_RE", RejectCallRegex())
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

declare i64 @callee(i64)
declare i64 @vararg(i64, ...)

define i64 @run(i64 %value, ptr %callback) {
entry:
  %first = call i64 (i64) @callee(i64 %value), !dbg !0
  %second = call i64 (i64) %callback(i64 %first)
  %third = tail call i64 (i64, ...) @vararg(i64 %second, i64 7) #1
  ret i64 %third
}
""".lstrip()
    )

    kernel = get_indexed_function_kernel(module.functions[0])
    assert [kernel.diagnostic_call_data(i)[2] for i in range(3)] == [
        "callee",
        "callback",
        "vararg",
    ]
    assert [kernel.diagnostic_call_data(i)[3] for i in range(3)] == [
        False,
        True,
        False,
    ]
    assert kernel.diagnostic_call_data(2)[5:7] == (1, True)


def test_scalar_call_span_lane_declines_cold_or_malformed_shapes() -> None:
    seed = IndexedFunctionSeed()
    cold_lines = (
        '%result = call i64 @"quoted callee"(i64 %value)',
        "%result = call { i64, i64 } @aggregate()",
        "%result = call i64 (i64) @callee(i64 getelementptr (i64, ptr @g, i64 0))",
        "%result = call i64 (i64) @callee(i64)",
    )
    for line in cold_lines:
        assert not self_backend_parse._parse_indexed_scalar_call_span(
            "run",
            "entry",
            line,
            seed,
        )
    assert len(seed.records) == 0
    assert len(seed.args) == 0


def test_definitions_first_seed_bypasses_legacy_hot_payload_parser(
    monkeypatch,
) -> None:
    def unexpected_legacy_payload(*_args):
        raise AssertionError("supported hot instruction used legacy tuple parser")

    monkeypatch.setattr(
        self_backend_parse,
        "_parse_instruction",
        unexpected_legacy_payload,
    )
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

declare i64 @callee(i64)

define i64 @run(i64 %value) {
entry:
  %slot = alloca i64
  %element = getelementptr i64, ptr %slot, i64 0
  store i64 %value, ptr %element
  %loaded = load i64, ptr %element
  %sum = add i64 %loaded, 1
  %positive = icmp sgt i64 %sum, 0
  %chosen = select i1 %positive, i64 %sum, i64 %loaded
  %pointer = inttoptr i64 %chosen to ptr
  %roundtrip = ptrtoint ptr %pointer to i64
  %result = call i64 @callee(i64 %roundtrip)
  ret i64 %result
}
""".lstrip()
    )
    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    metadata = kernel.instruction_metadata
    instruction_facts = kernel.instruction_facts
    fixed_records = kernel.instruction_record_scalars
    gep_records = kernel.gep_scalars

    assert func.indexed_seed is None
    assert func.blocks == []
    assert kernel.cold_instruction_data == []
    assert len(kernel.instruction_metadata) == 10 * 4
    assert len(kernel.instruction_facts) == 10 * 4
    assert kernel.instruction_metadata is metadata
    assert kernel.instruction_facts is instruction_facts
    assert kernel.instruction_record_scalars is fixed_records
    assert kernel.gep_scalars is gep_records


def test_definitions_first_seed_carries_instruction_id_across_blocks() -> None:
    module = parse_self_backend_module(
        """
target triple = "arm64-apple-darwin23.6.0"

define i64 @run(i64 %value, i1 %cond) {
entry:
  %first = add i64 %value, 1
  br i1 %cond, label %left, label %right
left:
  %second = mul i64 %first, 2
  ret i64 %second
right:
  %third = sub i64 %first, 3
  ret i64 %third
}
""".lstrip()
    )
    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    assert func.blocks == []
    assert [kernel.block_fact(block_id).first for block_id in range(3)] == [
        0,
        1,
        2,
    ]
    assert len(kernel.instruction_metadata) == 12
    assert [kernel.instruction_count(block_id) for block_id in range(3)] == [
        1,
        1,
        1,
    ]
