"""Focused AArch64 layout contracts for canonical post-call error checks."""

import pcc.backend.self_backend_aarch64_darwin_flow as aarch64_flow
from pcc.backend.self_backend import emit_aarch64_darwin_asm
from pcc.backend.self_backend_ir import (
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    TypeDesc,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel


_MODULE_PREFIX = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare i64 @py_err_occurred()

define i64 @possibly_raising() {
entry:
  ret i64 7
}
"""


def _previous_nonempty(lines: list[str], label: str) -> str:
    index = lines.index(label)
    index -= 1
    while index >= 0 and not lines[index]:
        index -= 1
    assert index >= 0
    return lines[index]


def test_canonical_post_call_error_check_makes_success_fall_through() -> None:
    ir_text = _MODULE_PREFIX + """
define i32 @main() {
entry:
  %value = call i64 @possibly_raising()
  %err.flag = call i64 @py_err_occurred()
  %err.cmp = icmp ne i64 %err.flag, 0
  br i1 %err.cmp, label %error, label %success

error:
  ret i32 1

success:
  ret i32 0
}
"""

    lines = emit_aarch64_darwin_asm(ir_text).splitlines()
    success_label = "L_main_success:"
    error_label = "L_main_error:"

    assert lines.index(success_label) < lines.index(error_label)
    branch = _previous_nonempty(lines, success_label)
    assert branch.startswith(("  b.", "  cbnz "))
    assert branch.endswith("L_main_error")
    assert "  b L_main_success" not in lines


def test_noncanonical_error_query_keeps_source_block_order() -> None:
    ir_text = _MODULE_PREFIX + """
define i32 @main() {
entry:
  %value = call i64 @possibly_raising()
  %intervening = add i64 %value, 1
  %err.flag = call i64 @py_err_occurred()
  %err.cmp = icmp ne i64 %err.flag, 0
  br i1 %err.cmp, label %error, label %success

error:
  ret i32 1

success:
  ret i32 0
}
"""

    lines = emit_aarch64_darwin_asm(ir_text).splitlines()
    error_label = "L_main_error:"
    success_label = "L_main_success:"

    assert lines.index(error_label) < lines.index(success_label)
    branch = _previous_nonempty(lines, error_label)
    assert branch.startswith(("  b.", "  cbz "))
    assert branch.endswith("L_main_success")


def test_canonical_error_layout_matches_slow_oracle_without_quadratic_scan(
    monkeypatch,
) -> None:
    """Keep the exact source-order policy while indexing success targets."""
    i64 = TypeDesc("int", width=64)
    void = TypeDesc("void")
    checks = []
    errors = []
    successes = []
    edge_count = 80
    for index in range(edge_count):
        checks.append(
            ParsedBlock(
                f"check_{index}",
                instructions=[
                    ParsedInstr(
                        "call",
                        (
                            f"value_{index}",
                            i64,
                            "possibly_raising",
                            False,
                            (),
                            0,
                            False,
                            (),
                        ),
                    ),
                    ParsedInstr(
                        "call",
                        (
                            f"err_{index}",
                            i64,
                            "py_err_occurred",
                            False,
                            (),
                            0,
                            False,
                            (),
                        ),
                    ),
                    ParsedInstr(
                        "icmp",
                        (
                            "ne",
                            f"cmp_{index}",
                            i64,
                            f"err_{index}",
                            "0",
                        ),
                    ),
                ],
                terminator=ParsedInstr(
                    "br_cond",
                    (f"cmp_{index}", f"error_{index}", f"success_{index}"),
                ),
            )
        )
        errors.append(
            ParsedBlock(
                f"error_{index}",
                terminator=ParsedInstr("ret_void", ()),
            )
        )
        successes.append(
            ParsedBlock(
                f"success_{index}",
                terminator=ParsedInstr("ret_void", ()),
            )
        )
    func = ParsedFunction(
        "layout_stress",
        void,
        [],
        True,
        False,
        checks + errors + successes,
    )

    expected = list(func.blocks)
    expected_edges = []
    oracle_comparisons = 0
    position = 0
    while position < len(expected):
        edge = aarch64_flow._canonical_post_call_error_edge(expected[position])
        if edge is None:
            position += 1
            continue
        error_target, success_target = edge
        target_position = -1
        candidate_position = position + 1
        while candidate_position < len(expected):
            oracle_comparisons += 1
            if aarch64_flow.text_key_names_equal(
                expected[candidate_position].name,
                success_target,
            ):
                target_position = candidate_position
                break
            candidate_position += 1
        if target_position >= position + 1:
            if target_position != position + 1:
                expected.insert(position + 1, expected.pop(target_position))
            expected_edges.append(
                (expected[position].name, error_target, success_target)
            )
        position += 1

    comparisons = 0
    names_equal = aarch64_flow.text_key_names_equal

    def counted_names_equal(left: str, right: str) -> bool:
        nonlocal comparisons
        comparisons += 1
        return names_equal(left, right)

    monkeypatch.setattr(aarch64_flow, "text_key_names_equal", counted_names_equal)
    aarch64_flow.plan_aarch64_canonical_error_fallthroughs(func, enabled=True)

    kernel = get_indexed_function_kernel(func)
    assert [
        kernel.block_names[kernel.block_layout_ids.get_unchecked(index)]
        for index in range(len(kernel.block_layout_ids))
    ] == [
        block.name for block in expected
    ]
    assert func.aarch64_cold_fallthrough_edges == expected_edges
    assert oracle_comparisons > 6_000
    assert comparisons <= edge_count * 2
