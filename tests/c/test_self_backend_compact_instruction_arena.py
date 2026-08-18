from __future__ import annotations

import pytest

import pcc.backend.self_backend_ir as backend_ir
from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_ir import (
    CompactParsedInstrArena,
    CompactParsedInstrView,
    PARSED_INSTRUCTION_KINDS,
    ParsedBlock,
    ParsedInstr,
    TypeDesc,
    parsed_module_instruction_arena_profile,
)
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_kernel import get_indexed_function_kernel


def test_stable_opcode_constants_match_the_canonical_vocabulary() -> None:
    for kind_id, kind_name in enumerate(PARSED_INSTRUCTION_KINDS):
        constant_name = "PARSED_INSTRUCTION_KIND_" + kind_name.upper()
        assert getattr(backend_ir, constant_name) == kind_id


def test_compact_instruction_arena_uses_dense_kinds_and_preserves_projection():
    assert CompactParsedInstrView.__slots__[:4] == tuple(
        ParsedInstr.__dataclass_fields__
    )
    values = [
        ParsedInstr("alloca", ("slot", TypeDesc("int", 64))),
        ParsedInstr(
            "load",
            ("value", TypeDesc("int", 64), "slot", TypeDesc("ptr"), 8),
            is_volatile=True,
        ),
        ParsedInstr(
            "binop",
            ("add", "sum", TypeDesc("int", 64), "value", "1"),
            arithmetic_flags=("nsw",),
        ),
    ]
    arena = CompactParsedInstrArena(values)
    assert len(arena) == 3
    assert isinstance(arena[0], CompactParsedInstrView)
    assert [value.kind for value in arena] == [
        "alloca", "load", "binop",
    ]
    assert all(
        0 <= value.kind_id < len(PARSED_INSTRUCTION_KINDS)
        for value in arena
    )
    assert arena[1].is_volatile
    assert arena[2].arithmetic_flags == ("nsw",)
    assert arena.materialize() == values
    assert not hasattr(arena[0], "__dict__")
    assert arena.profile_counters() == {
        "records": 3,
        "kind_id_bytes": 3,
        "volatile_bytes": 3,
        "arithmetic_flag_bytes": 3,
        "diagnostic_materializations": 3,
    }


def test_compact_instruction_arena_rejects_unknown_and_corrupt_kind_ids():
    with pytest.raises(BackendUnavailable, match="unknown parsed-instruction"):
        CompactParsedInstrArena([ParsedInstr("future-unknown", ())])
    arena = CompactParsedInstrArena([ParsedInstr("ret_void", ())])
    arena._kind_ids[0] = len(PARSED_INSTRUCTION_KINDS)
    with pytest.raises(BackendUnavailable, match="corrupt parsed-instruction"):
        _ = arena[0].kind


@pytest.mark.parametrize(
    "projection",
    (
        slice(None),
        slice(1, None),
        slice(None, 3),
        slice(-3, -1),
        slice(None, None, 2),
        slice(None, None, -1),
        slice(3, 0, -2),
        slice(-100, 100, 3),
    ),
)
def test_compact_instruction_arena_slice_matches_list_semantics(projection):
    values = [
        ParsedInstr(kind, ())
        for kind in ("alloca", "load", "store", "ret_void")
    ]
    arena = CompactParsedInstrArena(values)
    assert [value.kind for value in arena[projection]] == [
        value.kind for value in values[projection]
    ]


def test_compact_instruction_arena_rejects_zero_slice_step():
    arena = CompactParsedInstrArena([ParsedInstr("ret_void", ())])
    with pytest.raises(ValueError, match="slice step cannot be zero"):
        _ = arena[::0]


def test_parsed_block_converts_legacy_lists_and_keeps_diagnostic_source_lines():
    block = ParsedBlock(
        name="entry",
        raw_lines=["%sum = add i64 %lhs, %rhs", "ret i64 %sum"],
        instructions=[ParsedInstr(
            "binop",
            ("add", "sum", TypeDesc("int", 64), "lhs", "rhs"),
        )],
        terminator=ParsedInstr("ret", (TypeDesc("int", 64), "sum")),
    )
    assert isinstance(block.instructions, CompactParsedInstrArena)
    assert block.instructions[0].kind == "binop"
    assert block.raw_lines[0] == "%sum = add i64 %lhs, %rhs"
    assert block.terminator is not None
    assert block.terminator.kind == "ret"


def test_parser_uses_function_kind_plane_at_real_ir_boundary():
    module = parse_self_backend_module(
        "target triple = \"arm64-apple-darwin\"\n"
        "define i64 @add_one(i64 %value) {\n"
        "entry:\n"
        "  %sum = add i64 %value, 1\n"
        "  ret i64 %sum\n"
        "}\n"
    )
    function = module.functions[0]
    kernel = get_indexed_function_kernel(function)
    assert function.blocks == []
    assert kernel.instruction_count(0) == 1
    assert PARSED_INSTRUCTION_KINDS[kernel.instruction_kind_id(0, 0)] == "binop"
    assert kernel.value_name(kernel.defined_value_id(0, 0)) == "sum"
    assert parsed_module_instruction_arena_profile(module) == {
        "blocks": 1,
        "records": 1,
        "kind_id_bytes": 1,
        "volatile_bytes": 0,
        "arithmetic_flag_bytes": 0,
        "diagnostic_materializations": 0,
    }
