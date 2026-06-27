from __future__ import annotations

from pcc.backend.self_backend_ir import ParsedBlock, ParsedInstr
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
    blocks = {block.name: block for block in func.blocks}
    assert blocks["raise.cont"].instructions == []
    assert blocks["raise.cont"].terminator is not None
    assert blocks["raise.cont"].terminator.kind == "unreachable"
    assert blocks["next"].terminator is not None
    assert blocks["next"].terminator.kind == "ret"
    assert "dead.next" not in blocks


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
