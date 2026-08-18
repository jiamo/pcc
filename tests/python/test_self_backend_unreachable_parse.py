from __future__ import annotations

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_ir import ParsedBlock, ParsedInstr
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import (
    _filter_reachable_blocks,
    _filter_reachable_blocks_linear,
    _filtered_blocks_drop_referenced_target,
    parse_self_backend_module,
)


def test_self_backend_parser_truncates_dead_text_after_unreachable():
    module = parse_self_backend_module(
        'target triple = "aarch64-apple-darwin"\n'
        "define external i64 @f(i1 %flag) {\n"
        "entry:\n"
        "  br i1 %flag, label %raise.cont, label %next\n"
        "\n"
        "raise.cont:\n"
        "  unreachable\n"
        "  %dead = add i64 1, 2\n"
        "  br label %dead.next\n"
        "\n"
        "dead.next:\n"
        "  ret i64 9\n"
        "\n"
        "next:\n"
        "  ret i64 7\n"
        "}\n"
    )

    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    assert func.blocks == []
    assert kernel.block_names == ["entry", "raise.cont", "next"]
    raise_id = kernel.block_id("raise.cont")
    next_id = kernel.block_id("next")
    assert kernel.instruction_count(raise_id) == 0
    assert kernel.diagnostic_terminator(raise_id).kind == "unreachable"
    assert kernel.diagnostic_terminator(next_id).kind == "ret"
    assert kernel.block_id("dead.next") < 0


def test_unreachable_cfg_call_is_validated_but_not_published() -> None:
    module = parse_self_backend_module(
        'target triple = "aarch64-apple-darwin"\n'
        "define i64 @f() {\n"
        "entry:\n"
        "  ret i64 0\n"
        "dead:\n"
        "  %unused = call i64 @callee(i64 7)\n"
        "  ret i64 %unused\n"
        "}\n"
    )

    func = module.functions[0]
    kernel = get_indexed_function_kernel(func)
    assert kernel.block_names == ["entry"]
    assert len(kernel.call_scalars) == 0
    assert len(kernel.call_arg_scalars) == 0
    assert len(kernel.instruction_metadata) == 0


def test_unreachable_cfg_instruction_still_fails_closed() -> None:
    with pytest.raises(BackendUnavailable, match="does not support instruction"):
        parse_self_backend_module(
            'target triple = "aarch64-apple-darwin"\n'
            "define i64 @f() {\n"
            "entry:\n"
            "  ret i64 0\n"
            "dead:\n"
            "  %unused = unsupported_opcode i64 7\n"
            "  ret i64 %unused\n"
            "}\n"
        )


def test_reachability_filter_detects_dropped_existing_branch_target():
    entry = ParsedBlock(
        name="entry",
        terminator=ParsedInstr("br", ("call.cont.42",)),
    )
    target = ParsedBlock(
        name="call.cont.42",
        terminator=ParsedInstr("ret", ()),
    )
    dead = ParsedBlock(
        name="dead",
        terminator=ParsedInstr("ret", ()),
    )

    assert _filtered_blocks_drop_referenced_target(
        [entry, target, dead],
        [entry],
    )
    assert not _filtered_blocks_drop_referenced_target(
        [entry, target, dead],
        [entry, target],
    )
    assert [
        block.name for block in _filter_reachable_blocks_linear([entry, target, dead])
    ] == ["entry", "call.cont.42"]


def test_reachability_filter_recovers_when_hash_changes_drop_entry_block():
    class ChangingHashText(str):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            instance.hash_calls = 0
            return instance

        def __hash__(self):
            self.hash_calls += 1
            return super().__hash__() ^ self.hash_calls

    entry = ParsedBlock(
        name=ChangingHashText("entry"),
        terminator=ParsedInstr("br", ("target",)),
    )
    target = ParsedBlock(
        name="target",
        terminator=ParsedInstr("ret", ()),
    )
    dead = ParsedBlock(
        name="dead",
        terminator=ParsedInstr("ret", ()),
    )

    assert [
        block.name for block in _filter_reachable_blocks([entry, target, dead])
    ] == ["entry", "target"]
